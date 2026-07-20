#!/usr/bin/env python3
"""오프라인 단위테스트 4종 — 네트워크 불필요."""
import os
import sys
# 적응형 자기수정 상태파일이 존재해도 단위테스트는 항상 v8 기본계수로 결정론적 실행.
os.environ.setdefault("KOSPI_ADAPTIVE_DISABLE", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'kospi_diode_mcp'))
import core


def test_t_ewy_open_coupling():
    """T_EWY v8: gap = 0.30 × winsor(EWY,±3). EWY +2% → gap +0.6%.
    (v7 K=0.58은 2년 백테스트에서 baseline에도 패 → v8 K=0.30 재캘리브레이션.)"""
    out = core.predict_open(prev_close=8000, ewy_overnight=2.0, sox_overnight=2.0)
    assert abs(out["gap_pct"] - 0.6) < 0.01
    assert out["pred_open"] == round(8000 * 1.006)


def test_ewy_winsor_extreme():
    """v8 winsor: 극단 EWY(+10%)는 ±3%로 축소 → gap = 0.30×3 = 0.9%.
    (SOX 교차검증 회피 위해 sox는 완만하게 둠)"""
    out = core.predict_open(prev_close=8000, ewy_overnight=10.0, sox_overnight=2.0)
    assert abs(out["gap_pct"] - 0.9) < 0.01  # winsor 미적용이면 3.0%였을 것


def test_avalanche_down():
    """하방 항복: 외인 -40k & 순수급 -25k → 저가 아래로, 기관 매수 무관."""
    out = core.predict_close(open_price=8000, current=7900, high=8010, low=7850,
                             foreign=-40000, inst=15000)  # 기관 +15k여도
    assert out["regime"] == "avalanche_down"
    assert out["pred_close"] < 7850  # 저가 아래로 continuation


def test_avalanche_up():
    """상방 항복: 기관 +30k & 직전 대비 배증 → 고가캡 해제."""
    out = core.predict_close(open_price=8000, current=8100, high=8120, low=7990,
                             foreign=5000, inst=30000, inst_prev=10000)
    assert out["regime"] == "avalanche_up"
    assert out["pred_close"] > 8100  # 현재가 위로 모멘텀 연장


def test_trend_down_cont_0713():
    """v9: 7/13 재현 — 12:35 저가밀착+매도지속 → 종가는 현재가 아래로 연장.
    구 v7은 현재가(6940)에 앵커해 오차 1.955%였음. v9는 저가 하회 연장."""
    out = core.predict_close(open_price=7412.03, current=6941.76, high=7529.07,
                             low=6937.79, foreign=-16790, inst=-5563)
    assert out["regime"] == "trend_down_cont"
    assert out["pred_close"] < 6937.79            # 저가 하회 예측
    assert abs(out["pred_close"] - 6806.93) < 40  # 실제 근접(구 133p 오차 대폭 개선)


def test_close_no_misfire_calm():
    """완만장(소폭 하락, 저가밀착 아님)은 추세지속 오발화 없이 기존 레짐."""
    out = core.predict_close(open_price=7400, current=7380, high=7420, low=7360,
                             foreign=-3000, inst=2000)
    assert out["regime"] not in ("trend_down_cont", "trend_up_cont")


def test_score_tiers():
    """티어 채점: 0.2%→5, 0.4%→4, >1.5%→0."""
    assert core.score(8020, 8000)["score"] == 5   # 0.25%
    assert core.score(8032, 8000)["score"] == 4   # 0.40%
    assert core.score(8200, 8000)["score"] == 0   # 2.5%


def test_us_holiday_discount():
    """미장 휴장(7/6 교훈): 낡은 EWY 디스카운트 + SOX 교차검증 스킵 → 갭 절대값 축소."""
    base = core.predict_open(prev_close=8088, ewy_overnight=-2.89, sox_overnight=-5.45)
    hol = core.predict_open(prev_close=8088, ewy_overnight=-2.89, sox_overnight=-5.45,
                            us_holiday=True)
    assert abs(hol["gap_pct"]) < abs(base["gap_pct"])   # 신호 신뢰도↓
    assert hol["pred_open"] > base["pred_open"]          # 하락폭 완화


def test_hyper_bull_damp():
    """capex 수요서사 강세: 하방 갭 완충 → 덜 하락."""
    base = core.predict_open(prev_close=8088, ewy_overnight=-2.89, us_holiday=True)
    bull = core.predict_open(prev_close=8088, ewy_overnight=-2.89, us_holiday=True,
                             hyper_bull=True)
    assert bull["gap_pct"] > base["gap_pct"]             # 음수 gap이 0쪽으로
    assert bull["pred_open"] > base["pred_open"]
    # bull은 상방(양수 gap)엔 개입 안 함
    up = core.predict_open(prev_close=8088, ewy_overnight=2.0, hyper_bull=True)
    up0 = core.predict_open(prev_close=8088, ewy_overnight=2.0)
    assert up["gap_pct"] == up0["gap_pct"]


def test_sox_colead_blend():
    """SOX 공동앵커(7/9 교훈): EWY 약·SOX 강·같은 방향이면 SOX 블렌딩으로 상방 보정."""
    # 7/10 재현: EWY +1.11, SOX +3.06 → 순수 EWY보다 위로
    base = core.predict_open(prev_close=7291.91, ewy_overnight=1.11, sox_overnight=3.06)
    lead = core.predict_open(prev_close=7291.91, ewy_overnight=1.11, sox_overnight=3.06,
                             sox_colead=True)
    assert lead["gap_pct"] > base["gap_pct"]          # SOX가 위로 끌어올림
    assert lead["pred_open"] > base["pred_open"]
    # SOX 약하면(<MIN) 발동 안 함 → 기존과 동일
    weak = core.predict_open(prev_close=7291.91, ewy_overnight=1.11, sox_overnight=1.0,
                             sox_colead=True)
    weak0 = core.predict_open(prev_close=7291.91, ewy_overnight=1.11, sox_overnight=1.0)
    assert weak["gap_pct"] == weak0["gap_pct"]
    # 방향 반대(EWY+ / SOX-)면 발동 안 함
    opp = core.predict_open(prev_close=7291.91, ewy_overnight=1.11, sox_overnight=-3.06,
                            sox_colead=True)
    opp0 = core.predict_open(prev_close=7291.91, ewy_overnight=1.11, sox_overnight=-3.06)
    assert opp["gap_pct"] == opp0["gap_pct"]
    # flag off면 기존과 완전 동일 (기본 동작 불변 보증)
    off = core.predict_open(prev_close=7291.91, ewy_overnight=1.11, sox_overnight=3.06)
    assert off["gap_pct"] == base["gap_pct"]


def test_vkospi_none_proxy_crisis():
    """VKOSPI None이어도 프록시(EWY 절대값/전일급등) 충족 시 위기레짐을 탄다."""
    base = core.predict_open(prev_close=8000, ewy_overnight=-1.0, prev_kospi_ret=4.5,
                             vkospi=None, params=core.adaptive.DEFAULTS)
    calm = core.predict_open(prev_close=8000, ewy_overnight=-2.9, prev_kospi_ret=3.9,
                             vkospi=None, params=core.adaptive.DEFAULTS)
    assert base["adaptive"]["crisis"] is True
    assert "위기" in base["model"]
    assert abs(base["gap_pct"] - (-0.5)) < 0.01   # CRISIS_K 0.50 × -1.0 (2026-07-20 백테스트 재보정)
    assert calm["adaptive"]["crisis"] is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("모든 테스트 통과")
