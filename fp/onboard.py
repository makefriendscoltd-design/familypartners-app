"""서포터즈 온보딩 키트 생성.

들어온 서포터즈에게 개인 추적링크를 끼운
① 단톡방 공지(실제 템플릿)  ② SNS 프로필 세팅  ③ 추천 입학 링크  ④ 규칙
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
    "{{LINK_SCHOOL}}": "ai-ceo-school",
}

# 모든 유형 공통으로 들어가는 공지 블록 ↔ 조각 파일 (한 곳에서만 수정)
# 위치는 유형별 템플릿의 {{토큰}} 자리에 따라 정해진다.
COMMON_BLOCKS = {
    "COMMON_RULES": "common_rules.txt",        # 필독/프로필명/강퇴 규칙
    "COMMON_RESOURCES": "common_resources.txt",  # 무료 라이브 + AIMAX 자료 모음집
    "COMMON_CONTACT": "common_contact.txt",      # 기타 문의
}


def _read(name: str) -> str:
    return (TPL_DIR / name).read_text(encoding="utf-8")


def _account_id(code: str | None) -> str:
    # 자동생성 X — 본인이 자유롭게 정해서 만들고 입력.
    return "(자유롭게 정하세요)"


# 유형별: 공지 템플릿 + 링크 슬롯(키, 라벨)
# 2026-10 부터 신규 가입은 supporter 하나뿐. family/aimax/both 는 기존 데이터 호환용으로만 남긴다.
# 공지는 전 유형 공통으로 학교 모집 공지(kakao_notice_school.txt)를 쓰고, 옛 상품 링크는 다시 쓰지 않는다.
# 슬롯 키 LINK_SCHOOL 은 이미 저장된 링크와 호환되도록 그대로 둔다(라벨만 추천 입학 링크).
DEFAULT_TYPE = "supporter"
SCHOOL_SLOTS = [("LINK_SCHOOL", "AI사장학교 추천 입학 링크")]
LINK_PLACEHOLDER = "(서포터즈 방에서 요청하면 개별 링크를 드립니다)"
TYPE_REGISTRY = {
    key: {
        "label": label,
        "desc": desc,
        "file": "kakao_notice_school.txt",
        "slots": SCHOOL_SLOTS,
    }
    for key, label, desc in (
        ("supporter", "AI사장학교 서포터즈", "AI사장학교 재학생·졸업생 · 추천 입학 장학금"),
        ("family", "이전 유형 · 메이크패밀리", "기존 데이터 호환용"),
        ("aimax", "이전 유형 · AIMAX", "기존 데이터 호환용"),
        ("both", "이전 유형 · 통합", "기존 데이터 호환용"),
    )
}


def norm_type(ptype: str | None) -> str:
    """알 수 없는 유형은 supporter 로 본다."""
    return ptype if ptype in TYPE_REGISTRY else DEFAULT_TYPE


def type_cfg(ptype: str | None) -> dict:
    return TYPE_REGISTRY[norm_type(ptype)]


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
              openchat: str | None = None, ptype: str | None = DEFAULT_TYPE) -> str:
    portal_url = f"{portal_base()}/me?t={token}" if token else "(포털 토큰 없음)"
    links = links or {}

    # 유형별 단톡방 공지(추천 입학 링크 슬롯 자동삽입)
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
        f"내 작업실(매일 콘텐츠를 올린 뒤 링크를 제출하는 개인 페이지 — 북마크 필수):\n{portal_url}\n\n")
    step1_submit = ("→ **매일 1건 발행(주말 없음)**. 발행 후 운영방(카톡)에 게시물 링크를 보내세요 = 출석."
                    if is_local else
                    "→ **매일 1건 발행(주말 없음)**. 발행 후 작업실에 게시물 링크를 제출하세요 = 출석.")
    step3_body = ("매일 올릴 **숏폼 영상·스레드 글·카드뉴스**는 운영진이 카톡으로 보내드립니다. 받아서 본인 SNS에 올리세요."
                  if is_local else
                  f"자료실에서 **숏폼 영상·스레드 글·카드뉴스**를 받아 본인 SNS에 올리세요.\n자료실: {files_url}")
    step4 = ("내 추천 입학 링크가 STEP 2 공지에 **이미 들어가 있습니다.** 그대로 쓰세요."
             if has_links else
             "서포터즈 방에 “추천 입학 링크 발급 요청합니다”라고 남기면 개별 링크를 만들어 드립니다.\n"
             "받은 링크를 작업실에 넣고 저장하면 STEP 2 공지의 해당 자리에 자동으로 들어갑니다.")
    return f"""# {name}님 AI사장학교 서포터즈 세팅 키트
{header_portal}아래 STEP 1~4 순서대로 세팅하세요.

---

## STEP 1. 매일 콘텐츠 업로드 (본인 SNS)
인스타그램·유튜브·스레드 중 본인 SNS 계정에 올립니다. 스레드 계정은 아래 값으로 만드세요.
```
{profile}
```
{step1_submit}
※ 계정 id 는 제안값입니다. 이미 있으면 끝자리만 바꿔서 쓰세요. 오픈톡방 링크는 본인 것으로 교체.
📷 **계정 생성 후 꼭 설정:** 모바일 우측 상단 ☰(짝대기 2~3개) → 계정 → 미디어 → **'고화질로 업로드' 체크 필수**

---

## STEP 2. 본인 단톡방 운영
1) 카카오톡 오픈톡방을 만들고, **내 방 프로필 닉네임을 `AI사장학교 서포터즈 ({name})` 로 설정**하세요.
2) 아래 공지를 **복사해서 내 카톡방 '공지'에 직접 붙여넣으세요.** (카톡에 자동으로 올라가지 않습니다. 추천 입학 링크는 넣으면 자동으로 채워집니다):
```
{notice}
```

---

## STEP 3. 매일 제공 콘텐츠 받기
{step3_body}

---

## STEP 4. 추천 입학 링크
{step4}

---

## 규칙 (필독)
- **매일 1건 업로드 후 링크 인증 — 주말·공휴일도 포함입니다.**
- 인증이 빠진 날 하루마다 **경고 1회**가 쌓입니다.
- **경고 2회가 되면 서포터즈 자격이 종료**되고, 다시 신청할 수 없습니다.
- 자격이 종료돼도 이미 확정된 추천 입학 장학금은 지급합니다.
- 추천으로 입학한 1명당 장학금 990,000원(등록금 3,300,000원의 30%)을 매월 지급합니다.
- AI사장학교 박조교가 매주 SNS 피드백을 드립니다.
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


def notice_text(ptype: str | None = DEFAULT_TYPE, links: dict | None = None) -> str:
    """STEP 2용 — 유형별 단톡방 공지(추천 입학 링크 슬롯 자동삽입)."""
    links = links or {}
    cfg = type_cfg(ptype)
    notice = _read(cfg["file"])
    # 공통 공지 블록(모든 유형 공유)을 먼저 끼워넣는다.
    for token, fname in COMMON_BLOCKS.items():
        notice = notice.replace("{{" + token + "}}", _read(fname).rstrip("\n"))
    for key, label in cfg["slots"]:
        val = (links.get(key) or "").strip() or LINK_PLACEHOLDER
        notice = notice.replace("{{" + key + "}}", val)
    return notice
