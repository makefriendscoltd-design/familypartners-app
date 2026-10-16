"""리마인더 / 경고 메시지 생성 (카톡·스레드 DM에 그대로 붙여넣는 용도).

운영자가 매일 손으로 쓰던 독려 멘트를 자동 출력. 메이크패밀리 톤(실행 중심).
"""
from __future__ import annotations


def reminder(name: str, streak: int) -> str:
    """오늘 아직 미제출인 위험군에게 보내는 마감 전 리마인더."""
    if streak >= 7:
        head = f"{name}님 🔥 {streak}일 연속 가고 있어요. 오늘 하나면 이어집니다."
    elif streak >= 1:
        head = f"{name}님, 현재 {streak}일 연속. 오늘만 올리면 끊기지 않습니다."
    else:
        head = f"{name}님, 오늘 첫 게시물로 연속 기록 시작해요."
    return (
        f"{head}\n"
        f"오늘 자정 전까지 본인 SNS에 1건 올리고 작업실에 링크를 인증해 주세요.\n"
        f"노출량 게임입니다 — 하루 한 번이 쌓여서 매출이 됩니다."
    )


def warning(name: str) -> str:
    """어제 인증이 빠진 서포터즈에게 보내는 경고 안내(운영자가 직접 보낼 때)."""
    return (
        f"[AI사장학교 서포터즈] {name}님, 어제 게시물 인증이 확인되지 않아 경고 1회가 쌓였습니다.\n"
        f"경고 2회가 되면 서포터즈 자격이 종료됩니다.\n"
        f"사정이 있으면 오늘 안에 회신해 주세요."
    )
