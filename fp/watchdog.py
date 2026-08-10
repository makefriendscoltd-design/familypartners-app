"""글감 버퍼 감시 — 자동화가 죽었을 때 사람에게 알린다.

왜 있나
-------
글감을 만드는 클라우드 루틴이 2026-08-01 이후 6회 연속 push 를 못 했는데,
아무도 몰랐다. 발화 기록만 남고 성공 여부를 보는 눈이 없었기 때문이다.
결국 8/4 아침에 "내일 글감이 왜 없냐"는 말이 나오고서야 발견했다.

자동화는 죽지 않게 감시하는 것까지가 자동화다. 서버는 24시간 떠 있고
뿌리오 자격증명도 여기 있으니, 감시는 서버가 하는 게 맞다.

동작
----
30분마다 '내일·모레' 글감이 각각 4건인지 본다. 부족하면 운영자에게 문자.
같은 내용으로 하루 한 번만 보낸다(settings 에 마지막 발송일 기록).

설정
----
`.env` 의 `OP_PHONE` 에 운영자 번호를 넣으면 켜진다. 없으면 로그만 찍는다.
번호는 레포에 두지 않는다.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import timedelta

from . import core, db, ppurio

CHECK_EVERY = 30 * 60          # 30분
NEED = 4                       # 하루 필요 건수
LOOKAHEAD = 2                  # 내일·모레까지는 반드시 차 있어야 한다
MARK = "watchdog:last_alert"   # 마지막으로 문자 보낸 날짜(YYYY-MM-DD)


def buffer_state(conn) -> dict:
    """앞으로 5일 각각 몇 건인지 + 며칠치가 연속으로 차 있는지."""
    today = core.now().date()          # KST 기준
    days = {}
    streak = 0
    for i in range(1, 6):
        d = core.iso(today + timedelta(days=i))
        r = conn.execute("SELECT COUNT(*) c FROM drops WHERE drop_date=?", (d,)).fetchone()
        days[d] = r["c"]
        if r["c"] >= NEED and streak == i - 1:
            streak = i
    return {"days": days, "streak": streak, "today": core.iso(today)}


def _short_days(st: dict) -> list[str]:
    """내일·모레 중 부족한 날짜."""
    return [d for i, (d, c) in enumerate(sorted(st["days"].items()))
            if i < LOOKAHEAD and c < NEED]


def check_once(send: bool = True) -> dict:
    """한 번 검사. 부족하면(그리고 오늘 아직 안 보냈으면) 문자를 보낸다."""
    conn = db.connect()
    try:
        st = buffer_state(conn)
        short = _short_days(st)
        st["short"] = short
        if not short:
            return st
        phone = (os.environ.get("OP_PHONE") or "").strip()
        already = core.get_setting(conn, MARK)
        st["alerted"] = False
        if not send or already == st["today"]:
            return st
        body = ("[패밀리파트너스] 글감 버퍼 부족\n"
                + " / ".join(f"{d} {st['days'][d]}건" for d in sorted(st["days"]))
                + f"\n연속 확보 {st['streak']}일. 루틴이 죽었는지 확인하세요.")
        if phone and ppurio.is_configured():
            try:
                ppurio.send_sms([{"phone": phone, "name": "운영자"}], body)
                core.set_setting(conn, MARK, st["today"])
                st["alerted"] = True
            except Exception as e:                 # 발송 실패가 서버를 막지 않는다
                print(f"[감시] 문자 발송 실패: {type(e).__name__}: {e}")
        else:
            print(f"[감시] {body}  (OP_PHONE 미설정 — 문자 못 보냄)")
            core.set_setting(conn, MARK, st["today"])
        return st
    finally:
        conn.close()


def _loop() -> None:
    while True:
        try:
            check_once()
        except Exception as e:                     # 감시가 서버를 죽이면 본말전도
            print(f"[감시] 검사 실패(무시): {type(e).__name__}: {e}")
        time.sleep(CHECK_EVERY)


def start() -> None:
    """serve() 에서 한 번 호출. 데몬 스레드라 종료를 막지 않는다."""
    threading.Thread(target=_loop, name="drop-watchdog", daemon=True).start()
    print("[감시] 글감 버퍼 감시 시작 (30분 간격)")
