"""직원 1호 멀티플라이어 — 글감 1개 → 5채널 콘텐츠.

소스 콘텐츠(글감) + 브랜드명을 받아 스레드 3종/인스타/이메일/카톡/블로그를 생성.
표준 라이브러리(urllib)만으로 Anthropic Messages API 호출 (anthropic SDK 불필요, 설치 0).

브랜드 보이스 문서(config/brands/<브랜드>.md)는 system 블록에 cache_control 로 캐싱 →
같은 브랜드로 여러 번 돌리면 입력 비용 ~90% 절감.

실행 전: 환경변수 ANTHROPIC_API_KEY 설정 필요.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-opus-4-8"
BRANDS_DIR = Path(__file__).resolve().parent.parent / "config" / "brands"

SYSTEM_INSTRUCTION = (
    "당신은 메이크패밀리의 콘텐츠 멀티플라이어입니다. "
    "소스 콘텐츠(글감) 1개를 받아 5개 채널로 변환합니다. 모든 출력은 한국어.\n"
    "- threads: 후킹 각도가 서로 다른 스레드 포스트 3개 "
    "(① 공감/일상 ② 문제제기/역발상 ③ 결과·증거 제시). 각 280자 내외, 첫 줄에서 멈칫하게.\n"
    "- instagram_caption: 인스타 캡션(저장 욕구 자극, 마지막에 행동 요청).\n"
    "- instagram_hashtags: 해시태그 5~10개(앞에 # 포함).\n"
    "- email: 직접반응(Hormozi) 구조 이메일 — 후킹 제목 한 줄 + 본문(문제→해결→증거→행동요청).\n"
    "- kakao: 카톡 단문 메시지(2~4줄, 링크 클릭/신청 유도).\n"
    "- blog_title / blog_body: SEO 블로그(제목은 검색 의도 반영, 본문은 소제목 포함 구조화).\n"
    "아래 브랜드 보이스를 철저히 따르세요. 반드시 JSON 스키마로만 응답."
)

# structured outputs 스키마 (Opus: additionalProperties:false 필수, min/maxLength 미지원)
SCHEMA = {
    "type": "object",
    "properties": {
        "threads": {"type": "array", "items": {"type": "string"}},
        "instagram_caption": {"type": "string"},
        "instagram_hashtags": {"type": "array", "items": {"type": "string"}},
        "email": {"type": "string"},
        "kakao": {"type": "string"},
        "blog_title": {"type": "string"},
        "blog_body": {"type": "string"},
    },
    "required": ["threads", "instagram_caption", "instagram_hashtags",
                 "email", "kakao", "blog_title", "blog_body"],
    "additionalProperties": False,
}


def load_brand_voice(brand: str) -> str:
    f = BRANDS_DIR / f"{brand}.md"
    if not f.exists():
        f = BRANDS_DIR / "default.md"
    head = f"# 적용 브랜드: {brand}\n\n" if f.name == "default.md" else ""
    return head + (f.read_text(encoding="utf-8") if f.exists() else f"브랜드: {brand}")


def build_payload(source: str, brand: str) -> dict:
    return {
        "model": MODEL,
        "max_tokens": 6000,
        "system": [
            {"type": "text", "text": SYSTEM_INSTRUCTION},
            # 브랜드 보이스 = 같은 브랜드 반복 호출 시 캐시 (마지막 system 블록에 breakpoint)
            {"type": "text", "text": load_brand_voice(brand),
             "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{
            "role": "user",
            "content": f"브랜드: {brand}\n\n[소스 콘텐츠 / 글감]\n{source}\n\n"
                       f"위 소스를 5개 채널로 변환해줘.",
        }],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }


def _call_api(payload: dict) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "환경변수 ANTHROPIC_API_KEY 가 없습니다. "
            "PowerShell: $env:ANTHROPIC_API_KEY=\"sk-ant-...\" 설정 후 다시 실행하세요.")
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"API 오류 {e.code}: {body}") from None


def multiply(source: str, brand: str) -> dict:
    """글감 → 5채널 dict. (API 호출)"""
    resp = _call_api(build_payload(source, brand))
    # output_config.format 사용 시 첫 text 블록이 유효한 JSON
    text = next((b["text"] for b in resp.get("content", []) if b.get("type") == "text"), "")
    if not text:
        raise RuntimeError(f"빈 응답: {json.dumps(resp)[:500]}")
    data = json.loads(text)
    usage = resp.get("usage", {})
    data["_cache_read"] = usage.get("cache_read_input_tokens", 0)
    return data


def format_output(d: dict) -> str:
    threads = d.get("threads", [])
    th = "\n\n".join(f"  [스레드 {i+1}] {t}" for i, t in enumerate(threads))
    tags = " ".join(d.get("instagram_hashtags", []))
    return (
        "━━━ 스레드 3종 (후킹 각도 다름) ━━━\n" + th +
        "\n\n━━━ 인스타 캡션 ━━━\n" + d.get("instagram_caption", "") +
        "\n" + tags +
        "\n\n━━━ 이메일 (직접반응) ━━━\n" + d.get("email", "") +
        "\n\n━━━ 카톡 메시지 ━━━\n" + d.get("kakao", "") +
        "\n\n━━━ 블로그 (SEO) ━━━\n[" + d.get("blog_title", "") + "]\n" +
        d.get("blog_body", "")
    )
