#!/usr/bin/env python3
"""adaptive.py — KOSPI 시가엔진 재귀적 자기수정(self-tuning) 레이어.

DQN 유비: 매일 채점오차를 피드백받아 시가 계수를 스스로 조정 → ~500거래일에 수렴.
발산 절대금지(과거 hyper_bull 4일 방치 자멸 교훈): 하드 클램프 + gradient EMA + lr annealing.

불변식(하위호환): 상태파일(records/adaptive_params.json)이 없거나
환경변수 KOSPI_ADAPTIVE_DISABLE가 설정되면 DEFAULTS를 반환 →
predict_open이 기존 v8과 100% 동일하게 동작한다.
정보·연구 목적. 투자자문 아님.
"""
from __future__ import annotations
import json
import os
import datetime
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_RECORDS = os.path.join(os.path.dirname(_HERE), "records")
PARAMS_PATH = os.path.join(_RECORDS, "adaptive_params.json")
LOG_PATH = os.path.join(_RECORDS, "adaptive_log.jsonl")
LKG_PATH = os.path.join(_RECORDS, "adaptive_lkg.json")  # last-known-good 스냅샷

# 기본값 = 현재 core.py v8 상수와 동일 → day-0 동작 불변
DEFAULTS: dict[str, Any] = {
    "K_EWY": 0.30,
    "EWY_WINSOR": 3.0,
    "BLOWOFF_COEF": 0.0,     # 전일급등 되돌림 감산계수(위기레짐 전용). 0=미적용
    "CRISIS_K": 0.80,        # VKOSPI>=THR 위기레짐 EWY 계수
    "CRISIS_WINSOR": 6.0,
    "VKOSPI_CRISIS_THR": 40.0,
    "day_count": 0,
    "grad_ema": {"K_EWY": 0.0, "CRISIS_K": 0.0, "BLOWOFF_COEF": 0.0},
}

# 하드 클램프(발산 방지) — 계수는 절대 이 범위를 벗어나지 못한다
BOUNDS: dict[str, tuple[float, float]] = {
    "K_EWY": (0.30, 1.20),
    "EWY_WINSOR": (3.0, 7.0),
    "BLOWOFF_COEF": (0.0, 0.35),
    "CRISIS_K": (0.50, 1.20),
    "CRISIS_WINSOR": (4.0, 7.0),
}
EMA_BETA = 0.2          # gradient EMA — 단일일 급변 억제
_TUNABLE = ("K_EWY", "EWY_WINSOR", "BLOWOFF_COEF", "CRISIS_K", "CRISIS_WINSOR")


def load_params() -> dict[str, Any]:
    """현재 계수 반환. 상태파일 없음/DISABLE → DEFAULTS(=기존 v8, 하위호환)."""
    if os.environ.get("KOSPI_ADAPTIVE_DISABLE"):
        return dict(DEFAULTS)
    try:
        with open(PARAMS_PATH, encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:
        return dict(DEFAULTS)
    out = dict(DEFAULTS)
    out.update({k: saved[k] for k in saved if k in DEFAULTS})
    ge = dict(DEFAULTS["grad_ema"])
    ge.update(saved.get("grad_ema", {}))
    out["grad_ema"] = ge
    return out


def _clamp(key: str, val: float) -> float:
    lo, hi = BOUNDS[key]
    return max(lo, min(hi, val))


def _save(p: dict[str, Any]) -> None:
    os.makedirs(_RECORDS, exist_ok=True)
    with open(PARAMS_PATH, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)


def _append_log(entry: dict[str, Any]) -> None:
    try:
        os.makedirs(_RECORDS, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def learn(
    pred_open: float,
    actual_open: float,
    prev_close: float,
    ewy: float | None = None,
    prev_kospi_ret: float | None = None,
    vkospi: float | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """당일 시가 채점 후 호출 — 부호오차로 계수를 error-reducing 방향 미세조정.

    수렴 안전장치: (a) gradient EMA(0.2) (b) lr annealing = 0.05/(1+day/100)
    (c) 하드 클램프. 상태파일이 없으면 이 호출이 최초 생성한다(그 전까진 v8 불변).
    """
    if not pred_open or not actual_open or not prev_close:
        return {"skip": "insufficient data"}
    date = date or datetime.datetime.now().strftime("%Y-%m-%d")
    p = load_params()
    before = {k: p[k] for k in _TUNABLE}

    actual_gap = (actual_open - prev_close) / prev_close * 100.0
    pred_gap = (pred_open - prev_close) / prev_close * 100.0
    err = actual_gap - pred_gap          # +: 실제가 예측보다 위 / -: 아래

    dc = int(p.get("day_count", 0))
    lr = 0.05 * (1.0 / (1.0 + dc / 100.0))   # annealing (초반 큼→후반 미세)
    crisis = vkospi is not None and vkospi >= p.get("VKOSPI_CRISIS_THR", 40.0)
    ge = p["grad_ema"]
    reasons: list[str] = []

    # EWY 계수 학습: 예측이 EWY방향 갭을 과소/과대했으면 조정.
    #   g = err × sign(EWY) / max(|EWY|,1). EWY-3서 실제갭이 더 아래면 K↑ (오늘 7/16 상황).
    if ewy is not None and abs(ewy) > 0.3:
        g = err * (1.0 if ewy > 0 else -1.0) / max(abs(ewy), 1.0)
        key = "CRISIS_K" if crisis else "K_EWY"
        ge[key] = (1 - EMA_BETA) * ge.get(key, 0.0) + EMA_BETA * g
        p[key] = _clamp(key, p[key] + lr * ge[key])
        reasons.append(f"{key}: gradEMA {ge[key]:+.4f}, lr {lr:.4f} → {p[key]:.4f}")

    # 블로우오프 되돌림 학습(위기레짐 + 전일급등 ≥+4%): 실제가 예측보다 아래면 되돌림 부족 → COEF↑
    if crisis and prev_kospi_ret is not None and prev_kospi_ret >= 4.0:
        g = (-err) / max(prev_kospi_ret, 1.0)   # err<0 → g>0(증가) / err>0 → g<0(감소)
        ge["BLOWOFF_COEF"] = (1 - EMA_BETA) * ge.get("BLOWOFF_COEF", 0.0) + EMA_BETA * g
        p["BLOWOFF_COEF"] = _clamp("BLOWOFF_COEF", p["BLOWOFF_COEF"] + lr * ge["BLOWOFF_COEF"])
        reasons.append(f"BLOWOFF_COEF: gradEMA {ge['BLOWOFF_COEF']:+.4f} → {p['BLOWOFF_COEF']:.4f}")

    p["day_count"] = dc + 1
    after = {k: p[k] for k in _TUNABLE}
    _save(p)

    # last-known-good: 오차가 안정권(|err|<=2.0%)이면 스냅샷 저장(롤백/감사용)
    if abs(err) <= 2.0:
        try:
            with open(LKG_PATH, "w", encoding="utf-8") as f:
                json.dump({**after, "day_count": p["day_count"], "date": date},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    entry = {
        "date": date, "day_count": p["day_count"], "err_gap_pct": round(err, 3),
        "crisis": crisis, "lr": round(lr, 5),
        "before": {k: round(before[k], 4) for k in before},
        "after": {k: round(after[k], 4) for k in after},
        "reasons": reasons,
    }
    _append_log(entry)
    return entry
