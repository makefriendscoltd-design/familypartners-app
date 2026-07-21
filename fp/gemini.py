"""Google Gemini 리라이팅 클라이언트 — 표준 라이브러리(urllib)만 사용.

글감 본문의 형식·구조는 그대로 두고 단어·표현만 바꿔 변형한다.
ppurio.py 와 같은 패턴: .env 또는 환경변수에서 설정을 읽고, 외부 의존성 0.

설정(환경변수 또는 프로젝트 루트 .env):
  GEMINI_API_KEY   Google AI Studio 발급 키 (필수)
  GEMINI_MODEL     모델명 (선택, 기본 gemini-2.5-flash)

키 발급: https://aistudio.google.com/apikey
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 글감 형식은 유지하고 표현만 바꾸도록 강하게 지시하는 시스템 프롬프트.
SYSTEM = (
    "너는 한국어 SNS 게시글을 리라이팅하는 도우미야. "
    "입력으로 주어진 글의 형식·구조·줄바꿈·빈 줄·이모지 위치·문단 순서는 "
    "반드시 그대로 유지해. 오직 사용하는 단어와 표현(어휘·말투의 디테일)만 자연스러운 "
    "다른 표현으로 바꿔. 의미와 정보는 100% 동일하게 유지하고, 내용을 새로 추가하거나 "
    "빼지 마. 해시태그(#...)·링크(URL)·멘션(@...)·숫자·이모지·고유명사는 바꾸지 말고 "
    "그대로 둬.\n"
    "가장 중요한 규칙 — 줄 길이를 맞춰라: 각 줄의 글자 수를 원본의 같은 줄과 "
    "거의 같게 유지해(±2~3자 이내). 한 줄이 원본보다 눈에 띄게 길어지거나 짧아지면 "
    "안 돼. 특히 번호·기호로 된 목록 항목은 원본처럼 짧고 균일하게 유지해. "
    "이건 모바일 화면에서 줄바꿈 위치가 원본과 똑같이 보이게 하기 위한 거야. "
    "길이를 맞추기 어려우면, 의미가 같은 더 짧은 표현을 골라서라도 줄 길이를 맞춰. "
    "문장 끝맺음 말투(예: '~하세요', '~합니다')도 원본과 같은 형태로 유지해.\n"
    "설명이나 머리말 없이, 변형된 글 본문만 출력해."
)


def _load_env_file() -> None:
    """프로젝트 루트 .env 를 os.environ 에 주입(기존 환경변수는 덮어쓰지 않음)."""
    p = ROOT / ".env"
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_env_file()


def _api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "")


def _model() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def is_configured() -> bool:
    return bool(_api_key())


def rewrite(body: str) -> str:
    """글감 본문을 받아 표현만 바꾼 변형본을 반환. 실패 시 RuntimeError."""
    text = (body or "").strip()
    if not text:
        raise RuntimeError("리라이팅할 본문이 비어 있습니다.")
    if not is_configured():
        raise RuntimeError("Gemini 설정이 없습니다(.env 또는 환경변수 GEMINI_API_KEY).")

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{_model()}:generateContent")
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "temperature": 1.0,
            "maxOutputTokens": 2048,
            # 2.5 계열의 사고(thinking)를 꺼서 빠르고 저렴하게 — 단순 치환엔 불필요.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": _api_key()}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(msg).get("error", {})
            detail = err.get("message") or msg
        except json.JSONDecodeError:
            detail = msg
        raise RuntimeError(f"Gemini 호출 실패({e.code}): {detail[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Gemini 연결 실패: {e.reason}")

    # 안전 필터 등으로 후보가 비어 있을 수 있음.
    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise RuntimeError(f"안전 필터로 차단됨({blocked}). 원문 표현을 조금 바꿔 다시 시도하세요.")
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError("결과가 비어 있습니다. 잠시 후 다시 시도하세요.")
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    out = "".join(part.get("text", "") for part in parts).strip()
    if not out:
        finish = cands[0].get("finishReason") or "UNKNOWN"
        raise RuntimeError(f"결과 본문이 없습니다(finishReason={finish}).")
    return out
