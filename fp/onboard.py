"""파트너 온보딩 키트 생성.

들어온 파트너에게 개인 추적링크를 끼운
① 단톡방 공지(실제 템플릿)  ② SNS 프로필 세팅  ③ 개인 구매링크 묶음  ④ 규칙
을 한 파일로 출력. 템플릿(config/templates/*.txt)은 공통, 링크만 개인별.

단톡방/프로필 문구는 매달 바뀌므로 코드가 아니라 txt 파일에서 수정한다.
"""
from __future__ import annotations

import os
import random
from pathlib import Path

from . import products

TPL_DIR = Path(__file__).resolve().parent.parent / "config" / "templates"


def portal_base() -> str:
    # 운영 배포 시 FP_PORTAL_BASE 환경변수로 실제 도메인 지정
    return os.environ.get("FP_PORTAL_BASE", "http://localhost:8000").rstrip("/")

# 단톡방 공지의 (개별링크 삽입) 자리 ↔ 상품키 매핑
NOTICE_SLOTS = {
    "{{LINK_FAMILYDAY}}": "ai-familyday",
    "{{LINK_AIMAX}}": "aimax-startup",
    "{{LINK_SECONDBRAIN}}": "second-brain",
}


def _read(name: str) -> str:
    return (TPL_DIR / name).read_text(encoding="utf-8")


def _account_id(code: str | None) -> str:
    # aimax_OOOO (oo에 랜덤값) — 제안값. 중복 시 파트너가 직접 조정.
    suffix = "".join(random.choices("0123456789", k=4))
    return f"aimax_{suffix}"


def build_kit(name: str, code: str | None, token: str | None = None,
              handle: str | None = None, sales_url: str | None = None) -> str:
    plist = products.products()
    portal_url = f"{portal_base()}/me?t={token}" if token else "(포털 토큰 없음)"
    sales = (sales_url or "").strip()
    slot = sales or "(운영진이 보내드린 판매 링크를 여기에 넣으세요)"

    # 단톡방 공지: 판매링크 슬롯 = 등록 시 입력한 판매페이지 링크
    notice = _read("kakao_notice.txt")
    for token, key in NOTICE_SLOTS.items():
        notice = notice.replace(token, slot)

    # SNS 프로필
    account_id = (handle or "").lstrip("@") or _account_id(code)
    profile = _read("sns_profile.txt")
    profile = (profile
               .replace("{{NICKNAME}}", name)
               .replace("{{ACCOUNT_ID}}", account_id)
               .replace("{{OPENCHAT}}", "[본인 오픈톡방 링크를 여기에]"))

    files_url = f"{portal_base()}/files"
    base = portal_base()
    is_local = base.startswith("http://localhost") or base.startswith("http://127.")
    header_portal = "" if is_local else (
        f"내 작업실(매일 글 올린 뒤 링크를 제출하는 나만의 페이지 — 북마크 필수):\n{portal_url}\n\n")
    step1_submit = ("→ **매일 1건 발행(주말 없음)**. 발행 후 운영방(카톡)에 게시물 링크를 보내세요 = 출석."
                    if is_local else
                    "→ **매일 1건 발행(주말 없음)**. 발행 후 작업실에 게시물 링크를 제출하세요 = 출석.")
    step3_body = ("글에 쓸 **사진·영상·자료집·글감**은 운영진이 카톡으로 보내드립니다. 받아서 콘텐츠로 만들어 고객을 모으세요."
                  if is_local else
                  f"자료실에서 **사진·영상·자료집·글감**을 받아 콘텐츠로 만들어 고객을 모으세요.\n자료실: {files_url}")
    step4 = (f"내 판매 페이지 링크 (STEP 2 공지에 이미 삽입됨):\n{sales}" if sales else
             "판매 페이지 링크는 운영진이 개별로 보내드립니다. 받으면 STEP 2 공지의 안내 자리에 넣으세요.")
    return f"""# {name}님 패밀리 파트너스 세팅 키트
{header_portal}아래 STEP 1~4 순서대로 세팅하세요.

---

## STEP 1. 매일 콘텐츠 업로드 (스레드)
먼저 새 스레드 계정을 아래 값으로 만드세요.
```
{profile}
```
{step1_submit}
※ 계정 id 는 제안값입니다. 이미 있으면 끝자리만 바꿔서 쓰세요. 오픈톡방 링크는 본인 것으로 교체.

---

## STEP 2. 본인 단톡방 운영
1) 카카오톡 단톡방을 만들고, **내 닉네임을 `AIMAX 매니저` 로 설정**하세요.
2) 아래 공지를 방에 **그대로 복붙** (개별 상품링크는 이미 내 것으로 들어 있음):
```
{notice}
```

---

## STEP 3. 관심 고객 모으기
{step3_body}

---

## STEP 4. 상품 판매 (운영 시스템 이식)
{step4}

---

## 규칙 (필독)
- **매일 1건 발행 — 주말·공휴일 예외 없음.**
- **1회라도 누락하면 즉시 강퇴 + 그 달 수익은 몰수됩니다.** (사정 봐주기 없음)
"""
