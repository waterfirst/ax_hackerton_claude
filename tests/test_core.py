#!/usr/bin/env python3
"""오프라인 단위테스트 4종 — 네트워크 불필요."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'kospi_diode_mcp'))
import core


def test_t_ewy_open_coupling():
    """T_EWY: gap = 0.58 × EWY. EWY +2% → gap +1.16%."""
    out = core.predict_open(prev_close=8000, ewy_overnight=2.0, sox_overnight=2.0)
    assert abs(out["gap_pct"] - 1.16) < 0.01
    assert out["pred_open"] == round(8000 * 1.0116)


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


def test_score_tiers():
    """티어 채점: 0.2%→5, 0.4%→4, >1.5%→0."""
    assert core.score(8020, 8000)["score"] == 5   # 0.25%
    assert core.score(8032, 8000)["score"] == 4   # 0.40%
    assert core.score(8200, 8000)["score"] == 0   # 2.5%


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("모든 테스트 통과")
