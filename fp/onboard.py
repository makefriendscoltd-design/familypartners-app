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
    # 자동생성 X — 본인이 자유롭게 정해서 만들고 입력.
    return "(자유롭게 정하세요)"


# 파트너 유형별: 공지 템플릿 + 판매링크 슬롯(키, 라벨)
TYPE_REGISTRY = {
    "family": {
        "label": "메이크패밀리 파트너스",
        "desc": "기존 패밀리회원(평생회원) · 강의 3종 판매",
        "file": "kakao_notice.txt",
        "slots": [
            ("LINK_FAMILYDAY", "패밀리데이 모집링크"),
            ("LINK_AIMAX", "AIMAX 창업프로그램 링크"),
            ("LINK_SECONDBRAIN", "제2의 뇌 링크"),
            ("LINK_FAMILYMEMBER", "패밀리 회원 모집"),
        ],
    },
    "aimax": {
        "label": "AIMAX 파트너스",
        "desc": "AIMAX 창업프로그램 수강생 · AI 직원/서비스 판매",
        "file": "kakao_notice_aimax.txt",
        "slots": [
            ("AI_YERI", "블로그 직원 예리씨"),
            ("AI_HYUNJU", "영업 사원 현주씨"),
            ("AI_BLOGAUTO", "블로그 자동화 팀"),
            ("AI_JIEUN", "오피스매니저 지은씨"),
            ("AI_NAKYUNG", "판서쌤 나경씨"),
            ("AI_YUNMI", "스크립트 작가 윤미씨"),
            ("AI_SONGI", "자료조사원 송이씨"),
        ],
    },
    "both": {
        "label": "둘 다 (통합)",
        "desc": "둘 다 수강 · 강의 + AI 직원 전부 판매",
        "file": "kakao_notice_both.txt",
        "slots": [
            ("B_FAMILYDAY", "패밀리데이 모집링크"),
            ("B_STARTUP", "AIMAX 창업프로그램 링크"),
            ("B_SECONDBRAIN", "제2의 뇌 링크"),
            ("B_FAMILYMEMBER", "패밀리 회원 모집"),
            ("B_YERI", "블로그 직원 예리씨"),
            ("B_HYUNJU", "영업 사원 현주씨"),
            ("B_BLOGAUTO", "블로그 자동화 팀"),
            ("B_JIEUN", "오피스매니저 지은씨"),
            ("B_NAKYUNG", "판서쌤 나경씨"),
            ("B_YUNMI", "스크립트 작가 윤미씨"),
            ("B_SONGI", "자료조사원 송이씨"),
        ],
    },
}


def type_cfg(ptype: str | None) -> dict:
    return TYPE_REGISTRY.get(ptype or "family", TYPE_REGISTRY["family"])


def type_label(ptype: str | None) -> str:
    return type_cfg(ptype)["label"]


def type_slots(ptype: str | None) -> list:
    return type_cfg(ptype)["slots"]


def links_of(row) -> dict:
    """파트너 행의 links_json → {슬롯키: url} dict."""
    import json
    try:
        return json.loads(row["links_json"] or "{}") or {}
    except Exception:
        return {}


def build_kit(name: str, code: str | None, token: str | None = None,
              handle: str | None = None, links: dict | None = None,
              openchat: str | None = None, ptype: str | None = "family") -> str:
    portal_url = f"{portal_base()}/me?t={token}" if token else "(포털 토큰 없음)"
    links = links or {}

    # 유형별 단톡방 공지(판매링크 슬롯 자동삽입)
    notice = notice_text(ptype, links)
    slots = type_slots(ptype)
    has_links = any((links.get(k) or "").strip() for k, _ in slots)

    # SNS 프로필
    account_id = (handle or "").lstrip("@") or _account_id(code)
    oc = (openchat or "").strip() or "[본인 오픈톡방 링크를 여기에]"
    profile = _read("sns_profile.txt")
    profile = (profile
               .replace("{{NICKNAME}}", name)
               .replace("{{ACCOUNT_ID}}", account_id)
               .replace("{{OPENCHAT}}", oc))

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
    step4 = ("내 판매 링크가 STEP 2 공지에 **이미 삽입돼 있습니다.** 그대로 쓰세요."
             if has_links else
             "판매 링크는 **상품마다 별도 발급**입니다. 운영진에게 카톡으로 '링크 발급 요청' 하면,\n"
             "받은 링크가 STEP 2 공지의 해당 자리에 자동으로 채워집니다.")
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
📷 **계정 생성 후 꼭 설정:** 모바일 우측 상단 ☰(짝대기 2~3개) → 계정 → 미디어 → **'고화질로 업로드' 체크 필수**

---

## STEP 2. 본인 단톡방 운영
1) 카카오톡 단톡방을 만들고, **내 방 프로필 닉네임을 `AIMAX 매니저 ({name})` 로 설정**하세요. (예전 'AIMAX 매니저' 통일 → 이제 성함 붙임)
2) 아래 공지를 **복사해서 내 카톡방 '공지'에 직접 붙여넣으세요.** (카톡에 자동으로 안 올라갑니다. 상품 링크는 넣으면 자동 채워짐):
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


# 오픈톡방 통일 설정값(가이드북)
OPENCHAT_TITLE = "AIMAX, 24시간 일하는 직원을 만듭니다"
OPENCHAT_INTRO = ("AIMAX, 24시간 일하는 직원을 만듭니다\n"
                  "AI로 나만의 시스템 만드는 방법을 공유합니다.")


def profile_text(name: str, handle: str | None = None, openchat: str | None = None) -> str:
    """STEP 1용 — 스레드 소개글(통일)."""
    account_id = (handle or "").lstrip("@") or _account_id(None)
    oc = (openchat or "").strip() or "[본인 오픈톡방 링크를 여기에]"
    return (_read("sns_profile.txt")
            .replace("{{NICKNAME}}", name)
            .replace("{{ACCOUNT_ID}}", account_id)
            .replace("{{OPENCHAT}}", oc))


def notice_text(ptype: str | None = "family", links: dict | None = None) -> str:
    """STEP 2용 — 유형별 단톡방 공지(판매링크 슬롯 자동삽입)."""
    links = links or {}
    cfg = type_cfg(ptype)
    notice = _read(cfg["file"])
    for key, label in cfg["slots"]:
        val = (links.get(key) or "").strip() or "(별도 발급 — 운영진에게 받아 넣기)"
        notice = notice.replace("{{" + key + "}}", val)
    return notice
