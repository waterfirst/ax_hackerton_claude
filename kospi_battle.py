#!/usr/bin/env python3
"""kospi_battle.py — Claude vs Codex KOSPI 시가/종가 대결 러너.

모드:
  open   07:35  시가 예측 (T_EWY) → records/YYYY-MM-DD.json 저장
  close  12:35  종가 예측 (양방향 애벌란치) → 같은 파일에 추가
  score  16:35  실제 시가/종가 대비 시가·종가 각각 채점 → 파일에 기록

플레이어: claude-kospi-diode (ax_hackerton_claude v8 — 2년 백테스트 재캘리브레이션)
정보·연구 목적. 투자자문 아님.
"""
from __future__ import annotations
import sys, os, json, datetime, subprocess
from kospi_diode_mcp import core
from kospi_diode_mcp import adaptive

HEADER = "🔷 [CLAUDE · KOSPI 다이오드]"
CHAT_ID = "5767743818"
BOT_KEY = "f260e77812b3f38f"  # waterfirst_bot (식별자, 토큰 아님)
BOT_SETTINGS = "/home/waterfirst/.cokacdir/bot_settings.json"


def send_telegram(text: str) -> dict:
    """cokacdir 봇 토큰으로 텔레그램 직접 발송. LLM 불필요 → 토큰 한도 무관."""
    try:
        import requests
        with open(BOT_SETTINGS, encoding="utf-8") as f:
            tok = json.load(f)[BOT_KEY]["token"]
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=15,
        )
        return {"telegram": "ok" if r.ok else f"http {r.status_code}"}
    except Exception as e:
        return {"telegram": "fail", "reason": str(e)[:120]}


def build_message(mode: str, r: dict) -> str:
    d = load()
    if mode == "open":
        return (f"🔷 [CLAUDE · KOSPI 시가] {TODAY}\n"
                f"예측 시가: {r.get('pred_open')} (gap {r.get('gap_pct')}%)\n"
                f"전일 {r.get('prev_close')} · EWY {d.get('ewy_overnight')}%\n"
                f"근거: {' / '.join(r.get('reasons', []))}\n"
                f"→ GitHub 기록 완료 · 정보·연구용")
    if mode == "close":
        fl = r.get("flows", {})
        return (f"🔷 [CLAUDE · KOSPI 종가] {TODAY}\n"
                f"예측 종가: {r.get('pred_close')}\n"
                f"레짐: {r.get('regime_kr')}\n"
                f"수급 외인/기관: {fl.get('foreign')}/{fl.get('inst')}\n"
                f"→ GitHub 기록 완료 · 정보·연구용")
    if mode == "score":
        os_ = r.get("open_score", {})
        cs_ = r.get("close_score", {})
        return (f"🔷 [CLAUDE · KOSPI 채점] {TODAY}\n"
                f"실제 시가/종가: {r.get('actual_open')}/{r.get('actual_close')}\n"
                f"시가: 예측 {d.get('pred_open')} → 오차 {os_.get('error_pct')}% · tier {os_.get('score')}\n"
                f"종가: 예측 {d.get('pred_close')} → 오차 {cs_.get('error_pct')}% · tier {cs_.get('score')}\n"
                f"→ GitHub 기록 완료 · 정보·연구용")
    return f"{HEADER} {mode}"

REPO = os.path.dirname(os.path.abspath(__file__))
RECORDS = os.path.join(REPO, "records")
TODAY = datetime.datetime.now().strftime("%Y-%m-%d")
PATH = os.path.join(RECORDS, f"{TODAY}.json")
ENV = "/home/waterfirst/python/memory/.env"
GH_REPO = "github.com/waterfirst/ax_hackerton_claude.git"


def _read_token() -> str | None:
    try:
        with open(ENV, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("github_token"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def git_push(mode: str) -> dict:
    """records/ 변경을 ax_hackerton_claude 리포에 커밋·푸시. 토큰은 로그 비노출."""
    token = _read_token()
    if not token:
        return {"push": "skip", "reason": "github_token 없음"}
    url = f"https://x-access-token:{token}@{GH_REPO}"

    def mask(s: str) -> str:
        return (s or "").replace(token, "***")[:200]

    try:
        subprocess.run(["git", "-C", REPO, "add", "records/"],
                       check=True, capture_output=True, text=True)
        c = subprocess.run(["git", "-C", REPO, "commit", "-m",
                            f"[CLAUDE] KOSPI {mode} · {TODAY}"],
                           capture_output=True, text=True)
        if c.returncode != 0 and "nothing to commit" in (c.stdout + c.stderr):
            return {"push": "skip", "reason": "변경 없음"}
        p = subprocess.run(["git", "-C", REPO, "push", url, "HEAD:main"],
                           capture_output=True, text=True)
        if p.returncode != 0:
            return {"push": "fail", "reason": mask(p.stderr)}
        return {"push": "ok", "commit": f"{TODAY} {mode}", "repo": GH_REPO}
    except Exception as e:
        return {"push": "fail", "reason": mask(str(e))}


def load() -> dict:
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"date": TODAY, "player": "claude-kospi-diode", "model": "v8"}


def save(d: dict) -> None:
    os.makedirs(RECORDS, exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def _flags() -> dict:
    """장전 수동 플래그(records/next_open_flags.json): us_holiday, hyper_bull, hyper_bear."""
    try:
        with open(os.path.join(RECORDS, "next_open_flags.json"), encoding="utf-8") as f:
            fl = json.load(f)
    except Exception:
        return {}
    # 자동만료(2026-07-10 교훈): set_at 날짜가 오늘이 아니면 서사 스위치를 전부 중립화.
    # 원인이 된 참사 — hyper_bull이 7/6에 켜진 뒤 4일간 재판단 없이 stuck →
    # 폭락장(-9.4%)에 상방편향 지속 → 7/7·7/8 시가 연패. flag는 '그날'만 유효하도록 강제.
    set_date = str(fl.get("set_at", ""))[:10]
    if set_date != TODAY:
        for k in ("hyper_bull", "hyper_bear", "us_holiday", "sox_colead"):
            fl[k] = False
        fl["_expired"] = f"set_at={set_date or '없음'} != {TODAY} → 서사 스위치 중립화"
    return fl


def do_open() -> dict:
    prev = core.fetch_prev_close()
    us = core.fetch_overnight()
    fl = _flags()
    vkospi = core.fetch_vkospi()               # 위기레짐 판정(실패 시 None→정상)
    prev_ret = core.fetch_prev_kospi_ret()     # 블로우오프 되돌림 학습용
    out = core.predict_open(
        prev_close=prev,
        ewy_overnight=us["EWY"],
        sox_overnight=us.get("SOX", 0.0),
        us_holiday=bool(fl.get("us_holiday", False)),
        hyper_bull=bool(fl.get("hyper_bull", False)),
        hyper_bear=bool(fl.get("hyper_bear", False)),
        sox_colead=bool(fl.get("sox_colead", False)),
        prev_kospi_ret=prev_ret,
        vkospi=vkospi,
    )
    d = load()
    d.update({
        "prev_close": prev,
        "ewy_overnight": us["EWY"],
        "sox_overnight": us.get("SOX", 0.0),
        "vkospi": vkospi,
        "prev_kospi_ret": prev_ret,
        "flags_used": fl,
        "pred_open": out["pred_open"],
        "open_gap_pct": out.get("gap_pct"),
        "open_reasons": out.get("reasons", []),
        "adaptive": out.get("adaptive"),
    })
    save(d)
    return {"mode": "open", **out}


def do_close() -> dict:
    snap = core.fetch_intraday()
    out = core.predict_close(
        open_price=snap["open"], current=snap["current"],
        high=snap["high"], low=snap["low"],
        foreign=snap["foreign"], inst=snap["inst"],
    )
    d = load()
    d.update({
        "intraday": snap,
        "pred_close": out["pred_close"],
        "close_regime": out.get("regime_kr"),
    })
    save(d)
    return {"mode": "close", **out}


def do_score() -> dict:
    snap = core.fetch_intraday()
    actual_open, actual_close = snap["open"], snap["current"]
    d = load()
    d["actual_open"], d["actual_close"] = actual_open, actual_close
    res = {"mode": "score", "actual_open": actual_open, "actual_close": actual_close}
    if d.get("pred_open") is not None:
        so = core.score(d["pred_open"], actual_open)
        d["open_score"], res["open_score"] = so, so
        # 재귀적 자기수정: 오늘 시가 실측으로 계수 학습(~500일 수렴). 실패해도 채점은 지속.
        try:
            learned = adaptive.learn(
                pred_open=d["pred_open"], actual_open=actual_open,
                prev_close=d.get("prev_close"), ewy=d.get("ewy_overnight"),
                prev_kospi_ret=d.get("prev_kospi_ret"), vkospi=d.get("vkospi"),
                date=d.get("date"),
            )
            d["adaptive_learn"], res["adaptive_learn"] = learned, learned
        except Exception as e:
            res["adaptive_learn"] = {"error": str(e)[:120]}
    else:
        res["open_score"] = {"note": "시가 예측 기록 없음"}
    if d.get("pred_close") is not None:
        sc = core.score(d["pred_close"], actual_close)
        d["close_score"], res["close_score"] = sc, sc
    else:
        res["close_score"] = {"note": "종가 예측 기록 없음"}
    save(d)
    return res


MODES = {"open": do_open, "close": do_close, "score": do_score}


def _is_trading_day(day: str | None = None) -> tuple[bool, str]:
    """주말/KRX 휴장일이면 (False, 사유). 휴장일 목록: records/krx_holidays.txt."""
    day = day or TODAY
    d = datetime.date.fromisoformat(day)
    if d.weekday() >= 5:
        return False, "주말"
    hol = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "records", "krx_holidays.txt")
    try:
        with open(hol, encoding="utf-8") as f:
            for line in f:
                code = line.split("#", 1)[0].strip()
                if code == day:
                    return False, "KRX 휴장일"
    except FileNotFoundError:
        pass
    return True, ""


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "show"
    if mode == "show":
        print(json.dumps(load(), ensure_ascii=False, indent=2))
    elif mode in MODES:
        ok, why = _is_trading_day()
        if not ok:
            if mode == "open":
                send_telegram(f"🔷 [CLAUDE · KOSPI] {TODAY}\n⏸️ {why} — 시가/종가 예측 skip")
            print(json.dumps({"mode": mode, "skip": True, "reason": why,
                              "date": TODAY}, ensure_ascii=False, indent=2))
            sys.exit(0)
        try:
            result = MODES[mode]()
            result = {"header": HEADER, **result}
            result["file"] = PATH
            result["github"] = git_push(mode)
            result["telegram"] = send_telegram(build_message(mode, result))
            result["disclaimer"] = "정보·연구 목적. 투자자문 아님."
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as ex:
            err = f"🔷 [CLAUDE · KOSPI {mode}] {TODAY}\n⚠️ 실패: {type(ex).__name__} — 장 시간/네트워크 확인"
            send_telegram(err)
            print(json.dumps({"error": f"{type(ex).__name__}: {ex}", "mode": mode},
                             ensure_ascii=False, indent=2))
            sys.exit(1)
    else:
        print(json.dumps({"error": f"unknown mode: {mode}",
                          "valid": list(MODES)}, ensure_ascii=False))
        sys.exit(2)
