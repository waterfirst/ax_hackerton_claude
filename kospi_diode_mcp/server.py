#!/usr/bin/env python3
"""
server.py — kospi-diode MCP 서버 (FastMCP)
==========================================
KOSPI 다이오드 회로 모델 v7.1을 MCP 툴로 노출한다.

툴:
  forecast_open      시가 예측 + 회로 근거 (스냅샷 입력 또는 라이브)
  forecast_close     장중 스냅샷 → 종가 예측 + 레짐
  explain_regime     입력 신호를 회로 소자로 해석 ('왜' 이 레짐인가)
  score_prediction   오차율 티어 채점(0~5)
  daily_workflow     하루 운영 루틴(07:30~마감 복기) 반환
  submission_answers 카카오페이 AX 해커톤 5문항 답변 반환

실행: python -m kospi_diode.server   (또는 mcp dev server.py)
정보·연구 목적. 투자자문 아님.
"""
from __future__ import annotations
from typing import Any, Optional

try:
    from mcp.server.fastmcp import FastMCP
except Exception as e:  # SDK 미설치 안내
    raise SystemExit(
        "mcp SDK가 필요합니다: pip install 'mcp[cli]'\n"
        "순수 예측 로직만 쓰려면 core.py를 직접 import 하세요."
    ) from e

try:
    from . import core
    from .submission import submission_answers as _answers
except ImportError:  # 스크립트 직접 실행
    import core
    from submission import submission_answers as _answers

mcp = FastMCP("kospi-diode")


@mcp.tool()
def forecast_open(snapshot: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """KOSPI 시가 예측 (T_EWY 변압기, gap=0.58×EWY).

    snapshot 미제공 시 네이버 금융 라이브 데이터를 수집한다.
    snapshot 키: prev_close, ewy_overnight, sox_overnight(선택),
                 hyper_bear(선택), prev_kospi_ret/prev_ewy(선택, 잔차 보정).
    """
    if snapshot is None:
        try:
            prev = core.fetch_prev_close()
            us = core.fetch_overnight()
            snapshot = {"prev_close": prev, "ewy_overnight": us["EWY"],
                        "sox_overnight": us["SOX"]}
        except Exception as ex:
            return {"error": f"라이브 수집 실패: {ex}",
                    "action": "snapshot(prev_close, ewy_overnight) 직접 입력 필요"}
    if "prev_close" not in snapshot or "ewy_overnight" not in snapshot:
        return {"error": "필수 입력 부족",
                "action": "snapshot에 prev_close, ewy_overnight 필요"}
    out = core.predict_open(
        prev_close=float(snapshot["prev_close"]),
        ewy_overnight=float(snapshot["ewy_overnight"]),
        sox_overnight=float(snapshot.get("sox_overnight", 0.0)),
        hyper_bear=bool(snapshot.get("hyper_bear", False)),
        prev_kospi_ret=snapshot.get("prev_kospi_ret"),
        prev_ewy=snapshot.get("prev_ewy"),
    )
    out["disclaimer"] = "정보·연구 목적. 투자자문 아님."
    return out


@mcp.tool()
def forecast_close(snapshot: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """KOSPI 종가 예측 (양방향 애벌란치 사다리).

    snapshot 미제공 시 라이브(장중) 데이터를 수집한다.
    snapshot 키: open, current, high, low, foreign, inst,
                 program(선택), inst_prev(선택, 상방 배증 판정).
    """
    if snapshot is None:
        try:
            snapshot = core.fetch_intraday()
        except Exception as ex:
            return {"error": f"라이브 수집 실패: {ex}",
                    "action": "snapshot(open, current, high, low, foreign, inst) 직접 입력 필요"}
    need = ("open", "current", "high", "low", "foreign", "inst")
    if not all(k in snapshot for k in need):
        return {"error": "필수 입력 부족", "action": f"snapshot에 {need} 필요"}
    out = core.predict_close(
        open_price=float(snapshot["open"]),
        current=float(snapshot["current"]),
        high=float(snapshot["high"]),
        low=float(snapshot["low"]),
        foreign=float(snapshot["foreign"]),
        inst=float(snapshot["inst"]),
        program=float(snapshot.get("program", 0.0)),
        inst_prev=float(snapshot.get("inst_prev", 0.0)),
    )
    out["disclaimer"] = "정보·연구 목적. 투자자문 아님."
    return out


@mcp.tool()
def explain_regime(snapshot: dict[str, Any]) -> dict[str, Any]:
    """입력 신호를 회로 소자로 해석 — '왜' 지금 이 레짐인가.

    snapshot 키: ewy_overnight, sox_overnight, foreign, inst, vkospi(선택).
    """
    return core.explain_regime(
        ewy_overnight=float(snapshot.get("ewy_overnight", 0.0)),
        sox_overnight=float(snapshot.get("sox_overnight", 0.0)),
        foreign=float(snapshot.get("foreign", 0.0)),
        inst=float(snapshot.get("inst", 0.0)),
        vkospi=snapshot.get("vkospi"),
    )


@mcp.tool()
def score_prediction(predicted: float, actual: float) -> dict[str, Any]:
    """오차율 티어 채점: ≤0.25%→5 … >1.5%→0."""
    return core.score(float(predicted), float(actual))


@mcp.tool()
def daily_workflow() -> dict[str, Any]:
    """하루 운영 루틴 (07:30 시가 → 장중 3회 → 12:30 종가 → 마감 복기)."""
    return {
        "07:30": "forecast_open — 전일종가+EWY 오버나잇으로 시가 갭 고정 제출(ex-ante)",
        "09:00 / 10:30 / 12:00": "장중 스냅샷 3회 (외인/기관 수급 임계 추적)",
        "12:30": "forecast_close — 양방향 애벌란치 사다리로 종가 고정 제출",
        "15:30 이후": "score_prediction 채점 + explain_regime 회로 복기, 실패는 코드 반영",
        "principle": "ex-ante 고정·제출 후 변경 금지·패배 시 당일 수정+소급검증",
        "disclaimer": "정보·연구 목적. 투자자문 아님.",
    }


@mcp.tool()
def submission_answers() -> dict[str, str]:
    """카카오페이증권 AX 해커톤 5문항 답변 반환."""
    return _answers()


if __name__ == "__main__":
    mcp.run()
