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
        f"오늘 자정 전까지 스레드 1건 올리고 링크만 답장 주세요.\n"
        f"노출량 게임입니다 — 하루 한 번이 쌓여서 매출이 됩니다."
    )


def warning(name: str) -> str:
    """어제 빵꾸난 강퇴 후보에게 보내는 경고(운영자 판단 전 통보용)."""
    return (
        f"{name}님, 어제 게시물이 확인되지 않았습니다.\n"
        f"패밀리 파트너스는 '매일 실행'이 유일한 조건이에요.\n"
        f"오늘 안에 사유 회신과 함께 게시물 올려주시면 운영진이 확인하겠습니다."
    )
