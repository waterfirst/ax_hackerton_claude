#!/usr/bin/env python3
"""
core.py — KOSPI 다이오드 회로 모델 v7.1 (순수 예측 로직 + 라이브 데이터)
=====================================================================
전기회로 등가모델로 KOSPI 시가/종가를 점예측한다.
- 시가:  T_EWY 변압기 결합 (gap = k x EWY 오버나잇, k=0.58) + 잔차 되돌림
- 종가:  양방향 애벌란치 사다리 (하방 제너 항복 / 상방 기관폭주 돌파 / 드리프트)

순수 함수(predict_open/predict_close/score/explain_regime)는 네트워크 없이 동작 →
오프라인 단위테스트 가능. fetch_* 함수만 네이버 금융 실시간 API에 의존.

정보·연구 목적. 투자자문 아님.
"""
from __future__ import annotations
import json
import re
import datetime
from typing import Any, Optional

try:
    import requests
except Exception:  # 테스트 환경에 requests 없을 수 있음
    requests = None

# ── 회로 상수 (v8: 2년 602일 백테스트로 재캘리브레이션 2026-07-10) ────────
# 교훈: v7 K=0.58은 EWY 과신 → 순진 baseline(갭0)에도 짐(MAE 0.86>0.84).
#       K를 0.30으로 낮추고 극단 EWY를 ±3%로 winsor하면 walk-forward OOS에서
#       baseline·v7 둘 다 이김(0.78 vs 0.91 vs 0.95). 극단일(|EWY|>3%)에서 특히 개선.
K_EWY = 0.30      # T_EWY 변압기 결합계수 (v7 0.58 → v8 0.30; EWY 과신 교정)
EWY_WINSOR = 3.0  # EWY 오버나잇 극단 축소 밴드 ±3% (환율노이즈·미장과민 fat-tail 억제)
R_RESID = 0.5     # 잔차 되돌림 계수 (전일 오버슈트 보정, 7/3 교훈)
GAP_CLAMP = 6.0   # 시가갭 한계 밴드 ±6%
HOLIDAY_DISCOUNT = 0.4  # 미장 휴장 다음날 EWY 신호 신뢰도 (7/6 교훈: 낡은 신호 참패)
HYPER_BULL_DAMP = 0.5   # capex 수요서사 강세 시 하방 갭 완충 계수
SOX_COLEAD_W = 0.6      # SOX 공동앵커: EWY 가중치(나머지 1-W는 SOX). 반도체 주도 아침 보정
SOX_COLEAD_MIN = 2.5    # SOX 공동앵커 발동 최소 |SOX|%. 강한 반도체 신호일 때만

# 애벌란치 항복 임계 (제너 물리)
AV_FOREIGN = -30000   # 외인 순매도 항복전압 (계약/억 기준)
AV_NET = -20000       # 외인+기관 순수급 항복전압
UP_INST = 20000       # 상방 애벌란치 기관 매수 임계
DEF_INST = 15000      # 강기관 방어 임계

HEAD = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}


def _f(x: Any) -> float:
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════
#  순수 예측 함수 (네트워크 불필요)
# ══════════════════════════════════════════════════════════════
def predict_open(
    prev_close: float,
    ewy_overnight: float,
    sox_overnight: float = 0.0,
    hyper_bear: bool = False,
    hyper_bull: bool = False,
    us_holiday: bool = False,
    sox_colead: bool = False,
    prev_kospi_ret: Optional[float] = None,
    prev_ewy: Optional[float] = None,
) -> dict[str, Any]:
    """시가 점예측 — T_EWY 변압기 결합.

    gap = K_EWY x EWY  (× 휴장 디스카운트)  (+ 잔차)  (+ SOX)  (± 서사 스위치)
    """
    ewy_w = max(-EWY_WINSOR, min(EWY_WINSOR, ewy_overnight))  # v8 극단 winsor
    gap = K_EWY * ewy_w
    reasons = [f"T_EWY(v8): {K_EWY}×EWY_w({ewy_w:+.2f}%; raw {ewy_overnight:+.2f})={gap:+.2f}%"]

    # 미장 휴장 다음날: 오버나잇 EWY가 낡은 신호 → 신뢰도 하락, 갭 축소
    # (7/6 교훈: 7/3 미국 휴장 → 낡은 EWY -2.89%로 하락예측했으나 실제 갭상승 참패)
    if us_holiday:
        gap *= HOLIDAY_DISCOUNT
        reasons.append(f"미장휴장: EWY 신호 신뢰도↓ (×{HOLIDAY_DISCOUNT}) → {gap:+.2f}%")

    # 잔차항: 전일 KOSPI가 EWY-내재를 초과/미달하면 되돌림 (변압기 이중계산 방지)
    if prev_kospi_ret is not None and prev_ewy is not None:
        overshoot = prev_kospi_ret - K_EWY * prev_ewy
        gap += -R_RESID * overshoot
        reasons.append(f"잔차되돌림: -{R_RESID}×overshoot({overshoot:+.2f})={-R_RESID*overshoot:+.2f}%")

    # SOX 교차검증: 방향 불일치 + 큰 괴리(2%p+)면 절충
    # 단, 미장 휴장이면 SOX도 낡은 신호 → 교차검증 스킵 (7/6 교훈)
    sox_gap = 0.5 * sox_overnight
    crosscheck_fired = not us_holiday and abs(gap - sox_gap) > 2.0
    if crosscheck_fired:
        gap = (gap + sox_gap) / 2
        reasons.append(f"SOX교차검증 절충 → {gap:+.2f}%")

    # SOX 공동앵커(비장의 대책): EWY와 SOX가 같은 방향이고 SOX가 강하면(≥MIN) SOX를
    # 시가 앵커에 블렌딩. KOSPI 시총 ~40%가 반도체 → 반도체 주도 아침엔 SOX가 시가를
    # 더 잘 끈다. (7/9 교훈: EWY 약·SOX 강일 때 순수 EWY는 갭상승을 크게 과소예측)
    # 교차검증(대괴리)이 이미 터진 날엔 이중적용 방지 위해 스킵.
    if (sox_colead and not us_holiday and not crosscheck_fired
            and gap * sox_gap > 0 and abs(sox_overnight) >= SOX_COLEAD_MIN):
        gap = SOX_COLEAD_W * gap + (1 - SOX_COLEAD_W) * sox_gap
        reasons.append(
            f"SOX공동앵커: EWY×{SOX_COLEAD_W}+SOX×{round(1-SOX_COLEAD_W,2)} "
            f"(SOX {sox_overnight:+.2f}%) → {gap:+.2f}%")

    # SW_hyper 서사 스위치 (bear/bull 배타적)
    if hyper_bear and gap > -1.0:
        # 하이퍼스케일러 악재: 상방 차단
        gap = min(gap, -1.0)
        reasons.append("SW_hyper bearish: 상방 차단(≤-1.0%)")
    elif hyper_bull and gap < 0:
        # capex 수요서사 강세(한국 HBM 공급자 수혜) → 하방 갭 완충
        # (7/6 교훈: capex +204% 서사가 SOX 약세·EWY 약세를 덮고 반도체 디커플링)
        gap *= HYPER_BULL_DAMP
        reasons.append(f"SW_hyper bullish: capex 수요서사, 하방 완충(×{HYPER_BULL_DAMP}) → {gap:+.2f}%")

    gap = max(-GAP_CLAMP, min(GAP_CLAMP, gap))
    pred = round(prev_close * (1 + gap / 100))
    return {
        "pred_open": pred,
        "gap_pct": round(gap, 3),
        "prev_close": prev_close,
        "reasons": reasons,
        "model": "v8 T_EWY(K=0.30,winsor±3) + 휴장/서사 보정",
    }


def predict_close(
    open_price: float,
    current: float,
    high: float,
    low: float,
    foreign: float,
    inst: float,
    program: float = 0.0,
    inst_prev: float = 0.0,
) -> dict[str, Any]:
    """종가 점예측 — 양방향 애벌란치 사다리.

    우선순위: 하방항복 → 상방폭주 → gap실패 → 강기관드리프트 → 고변동드리프트
    """
    net = foreign + inst
    # 1. 하방 애벌란치 (제너 항복): 외인 압도 → 저가 아래로 continuation
    if foreign <= AV_FOREIGN and net <= AV_NET:
        pred = round(min(current, low) - 0.5 * (current - low))
        return _close_result(pred, "D_av 하방항복(외인압도, 기관무관)", "avalanche_down",
                             foreign, inst, net)
    # 2. 상방 애벌란치: 기관 폭주+가속 → 고가캡 해제, 모멘텀 연장
    #    inst_prev(전일 기관)가 있어야 '배증(가속)'을 확인 가능. 없으면 오발화 방지로 미발화.
    if inst >= UP_INST and inst_prev > 0 and inst >= 2 * inst_prev:
        pred = round(current + 0.8 * (current - open_price))
        return _close_result(pred, "D_av 상방항복(기관폭주 배증, 고가캡 해제)", "avalanche_up",
                             foreign, inst, net)
    # 3. gap 실패 + 매도 → 하방
    if current < open_price - 80 and (foreign < -10000 or program < -8000):
        pred = round(current - 0.40 * (current - low))
        return _close_result(pred, "gap실패+매도(하방 드리프트)", "gap_fail",
                             foreign, inst, net)
    # 4. 강기관 방어(G_inst 연속) + 갭유지 → 되돌림 상방
    if current >= open_price - 80 and inst > DEF_INST:
        pred = round(current + 0.35 * (current - open_price))
        return _close_result(pred, "G_inst 강방어 드리프트 연장", "inst_defense",
                             foreign, inst, net)
    # 5. 고변동 레짐: 현재가 앵커 폐지 → 장중 드리프트 1/4 연장
    pred = round(current + 0.25 * (current - open_price))
    return _close_result(pred, "고변동 드리프트(현재가 앵커 폐지)", "drift",
                         foreign, inst, net)


def _close_result(pred, regime_kr, regime_id, foreign, inst, net):
    return {
        "pred_close": pred,
        "regime": regime_id,
        "regime_kr": regime_kr,
        "flows": {"foreign": foreign, "inst": inst, "net": net},
        "model": "v7.1 bidirectional avalanche",
    }


def score(pred: float, actual: float) -> dict[str, Any]:
    """오차율 티어 채점 (≤0.25%→5 … >1.5%→0)."""
    if pred is None or actual in (None, 0):
        return {"score": 0, "error_pct": None}
    e = abs(pred - actual) / actual * 100
    for thr, pt in ((0.25, 5), (0.50, 4), (0.75, 3), (1.0, 2), (1.5, 1)):
        if e <= thr:
            return {"score": pt, "error_pct": round(e, 3), "pred": pred, "actual": actual}
    return {"score": 0, "error_pct": round(e, 3), "pred": pred, "actual": actual}


def explain_regime(
    ewy_overnight: float = 0.0,
    sox_overnight: float = 0.0,
    foreign: float = 0.0,
    inst: float = 0.0,
    vkospi: Optional[float] = None,
) -> dict[str, Any]:
    """입력 신호를 회로 소자로 해석 — '왜' 이 레짐인가."""
    net = foreign + inst
    elems = []
    elems.append({
        "element": "T_EWY (변압기)",
        "reading": f"EWY {ewy_overnight:+.2f}% → 시가갭 내재 {K_EWY*ewy_overnight:+.2f}%",
        "meaning": "미국시간 한국 가격발견 = 밤새 채점된 답안지",
    })
    elems.append({
        "element": "V_semi (반도체 전압원)",
        "reading": f"SOX {sox_overnight:+.2f}%",
        "meaning": "하이퍼스케일러 capex 서사가 1차 전류원(공급자 아님)",
    })
    if foreign <= AV_FOREIGN and net <= AV_NET:
        av = "항복 도통(붕괴)"
    elif inst >= UP_INST:
        av = "상방 항복(돌파 가능)"
    else:
        av = "차단(정상)"
    elems.append({
        "element": "D_av (제너 다이오드)",
        "reading": f"외인 {foreign:+,} / 순수급 {net:+,} → {av}",
        "meaning": "임계 초과 시 눈사태 도통, 양방향 항복",
    })
    elems.append({
        "element": "G_inst (가변 컨덕턴스)",
        "reading": f"기관 {inst:+,}",
        "meaning": "방어는 부호가 아닌 크기(연속). +2.9조 성공 vs +0.2조 실패",
    })
    if vkospi is not None:
        regime = "위기 레짐(τ↑, R_eff 붕괴)" if vkospi >= 40 else "정상 레짐"
        elems.append({
            "element": "R_eff(τ) (가변 저항)",
            "reading": f"VKOSPI {vkospi}",
            "meaning": f"{regime} — 회복 시정수 2.3→10.3일 전환 가능",
        })
    return {"elements": elems, "net_flow": net, "model": "v7.1 diode circuit"}


# ══════════════════════════════════════════════════════════════
#  라이브 데이터 (네이버 금융) — 정보 부족 시 명시적 폴백
# ══════════════════════════════════════════════════════════════
def _require_requests():
    if requests is None:
        raise RuntimeError("requests 미설치 — 라이브 데이터 불가. 스냅샷을 직접 입력하세요.")


def fetch_prev_close() -> float:
    _require_requests()
    url = ("https://api.finance.naver.com/siseJson.naver?symbol=KOSPI"
           "&requestType=1&startTime=20260601&endTime=20260801&timeframe=day")
    rows = json.loads(requests.get(url, headers=HEAD, timeout=15).text.strip().replace("'", '"'))
    return _f(rows[-1][4])


def fetch_overnight() -> dict[str, float]:
    _require_requests()
    out: dict[str, float] = {}
    for s, n in [(".SOX", "SOX"), (".INX", "SP500"), (".IXIC", "NASDAQ")]:
        try:
            r = requests.get(f"https://api.stock.naver.com/index/{s}/price?pageSize=1&page=1",
                             headers=HEAD, timeout=10).json()
            out[n] = _f(r[0].get("fluctuationsRatio")) if r else 0.0
        except Exception:
            out[n] = 0.0
    try:
        out["EWY"] = _f(requests.get("https://api.stock.naver.com/stock/EWY/basic",
                                     headers=HEAD, timeout=8).json().get("fluctuationsRatio"))
    except Exception:
        out["EWY"] = 0.0
    return out


def fetch_intraday() -> dict[str, Any]:
    _require_requests()
    d = requests.get("https://m.stock.naver.com/api/index/KOSPI/basic", headers=HEAD, timeout=10).json()
    cur = _f(d.get("closePrice"))
    today = datetime.datetime.now().strftime("%Y%m%d")
    url = ("https://api.finance.naver.com/siseJson.naver?symbol=KOSPI"
           f"&requestType=1&startTime={today}&endTime=20991231&timeframe=day")
    r = json.loads(requests.get(url, headers=HEAD, timeout=15).text.strip().replace("'", '"'))[-1]
    o, hi, lo = _f(r[1]), _f(r[2]), _f(r[3])
    F = I = 0
    try:
        rr = requests.get(f"https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate={today}&sosok=01&page=1",
                          headers=HEAD, timeout=12)
        rr.encoding = "euc-kr"
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", rr.text, re.S):
            c = [re.sub(r"<[^>]+>", "", x).replace("&nbsp;", "").replace(",", "").strip()
                 for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if c and re.match(r"\d{2}\.\d{2}\.\d{2}$", c[0]):
                F, I = int(c[2]), int(c[3])
                break
    except Exception:
        pass
    return {"open": o, "current": cur, "high": hi, "low": lo, "foreign": F, "inst": I}
