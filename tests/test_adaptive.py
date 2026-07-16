#!/usr/bin/env python3
"""adaptive 자기수정 레이어 단위테스트 3종 — 네트워크 불필요, 임시 상태파일 사용."""
import os
import sys
import json
import tempfile

_MCP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'kospi_diode_mcp')
sys.path.insert(0, _MCP)
import adaptive  # noqa: E402
import core      # noqa: E402


def _isolate(tmp):
    """상태/로그 경로를 임시폴더로 격리(실기록 오염 방지)."""
    adaptive.PARAMS_PATH = os.path.join(tmp, "adaptive_params.json")
    adaptive.LOG_PATH = os.path.join(tmp, "adaptive_log.jsonl")
    adaptive.LKG_PATH = os.path.join(tmp, "adaptive_lkg.json")
    os.environ.pop("KOSPI_ADAPTIVE_DISABLE", None)


def test_backward_compat_no_state():
    """불변식: 상태파일 없으면 predict_open이 기존 v8과 완전히 동일."""
    with tempfile.TemporaryDirectory() as tmp:
        _isolate(tmp)
        p = adaptive.load_params()
        assert p["K_EWY"] == 0.30 and p["EWY_WINSOR"] == 3.0
        # vkospi 미전달 → 정상레짐 → gap = 0.30×winsor(EWY)
        out = core.predict_open(prev_close=8000, ewy_overnight=2.0, sox_overnight=2.0)
        assert abs(out["gap_pct"] - 0.6) < 0.01
        # 극단 EWY도 winsor±3 → 0.30×3 = 0.9
        out2 = core.predict_open(prev_close=8000, ewy_overnight=10.0, sox_overnight=2.0)
        assert abs(out2["gap_pct"] - 0.9) < 0.01


def test_clamp_never_diverges():
    """발산방지: 같은 방향 오차를 500번 줘도 계수가 클램프 상한을 못 넘는다."""
    with tempfile.TemporaryDirectory() as tmp:
        _isolate(tmp)
        # 매일 EWY-3인데 실제는 큰 갭다운(-4.45%) → K를 계속 올리려는 압력
        for _ in range(500):
            adaptive.learn(pred_open=7219, actual_open=6960.5, prev_close=7284.41,
                           ewy=-3.02, prev_kospi_ret=6.24, vkospi=78.0)
        p = adaptive.load_params()
        lo, hi = adaptive.BOUNDS["CRISIS_K"]
        assert lo <= p["CRISIS_K"] <= hi           # 클램프 준수
        assert 0.0 <= p["BLOWOFF_COEF"] <= 0.35     # 클램프 준수
        assert p["day_count"] == 500


def test_convergence_direction_crisis():
    """수렴방향: 위기장에서 시가갭을 과소예측(실제가 더 아래)하면 CRISIS_K가 올라간다."""
    with tempfile.TemporaryDirectory() as tmp:
        _isolate(tmp)
        k0 = adaptive.load_params()["CRISIS_K"]
        # 7/16형: EWY-3.02, 예측 -0.9% 갭인데 실제 -4.45% 갭, VKOSPI 78 위기
        for _ in range(10):
            adaptive.learn(pred_open=7219, actual_open=6960.5, prev_close=7284.41,
                           ewy=-3.02, prev_kospi_ret=6.24, vkospi=78.0)
        p = adaptive.load_params()
        assert p["CRISIS_K"] > k0                    # 위기계수 상승(과소예측 교정 방향)
        assert p["BLOWOFF_COEF"] > 0.0               # 전일급등 되돌림 학습됨
        # 학습 후 위기레짐 예측은 기존보다 더 큰(음의) 갭을 낸다
        out = core.predict_open(prev_close=7284.41, ewy_overnight=-3.02,
                                prev_kospi_ret=6.24, vkospi=78.0)
        base = core.predict_open(prev_close=7284.41, ewy_overnight=-3.02,
                                 prev_kospi_ret=6.24, vkospi=78.0,
                                 params=adaptive.DEFAULTS)
        assert out["gap_pct"] < base["gap_pct"]      # 더 강한 갭다운 예측


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("adaptive 테스트 통과")
