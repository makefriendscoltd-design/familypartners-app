"""뿌리오(Ppurio) SMS/LMS 발송 클라이언트 — 표준 라이브러리만 사용.

memberapps/lib/ppurio.js 를 파이썬으로 포팅. 외부 의존성 0.

설정은 환경변수 또는 프로젝트 루트 `.env` 에서 읽는다:
  PPURIO_ACCOUNT_ID     계정 ID (필수)
  PPURIO_BASIC_AUTH     "계정:API키" Base64 (필수, 토큰 발급용 Basic 인증)
  PPURIO_CALLER_NUMBER  발신번호 (필수)
  PPURIO_PROXY_URL      (선택) 고정 IP 프록시 주소 — 클라우드 배포 시 IP 화이트리스트 우회용
  PPURIO_PROXY_SECRET   (선택) 프록시 인증 시크릿

토큰 발급: POST {base}/v1/token  (Authorization: Basic ...)
메시지 발송: POST {base}/v1/message (Authorization: Bearer <token>)
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def _base() -> str:
    return (os.environ.get("PPURIO_PROXY_URL") or "https://message.ppurio.com").rstrip("/")


def is_configured() -> bool:
    """필수 설정 3종이 모두 있으면 True."""
    return bool(os.environ.get("PPURIO_BASIC_AUTH")
                and os.environ.get("PPURIO_ACCOUNT_ID")
                and os.environ.get("PPURIO_CALLER_NUMBER"))


def _byte_len(s: str) -> int:
    # EUC-KR 기준 바이트 수: 비ASCII(한글 등) 2, ASCII 1
    return sum(2 if ord(c) > 0x7F else 1 for c in s)


def _truncate_bytes(s: str, max_bytes: int) -> str:
    out, total = [], 0
    for c in s:
        total += 2 if ord(c) > 0x7F else 1
        if total > max_bytes:
            break
        out.append(c)
    return "".join(out)


_PHONE_RE = re.compile(r"^01[016789]\d{7,8}$")


def normalize_phone(raw: str | None) -> str | None:
    """전화번호 정규화 — 유효한 한국 휴대폰이면 숫자만 남겨 반환, 아니면 None."""
    cleaned = re.sub(r"[^0-9]", "", raw or "")
    if cleaned.startswith("82") and len(cleaned) >= 12:  # +82 국제번호 → 0
        cleaned = "0" + cleaned[2:]
    return cleaned if _PHONE_RE.match(cleaned) else None


_token = {"val": None, "exp": 0.0}


def _http(url: str, headers: dict, data: bytes | None = None):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, (json.loads(body) if body.strip() else {})
        except json.JSONDecodeError:
            return e.code, {}


def _get_token() -> str:
    now = time.time()
    if _token["val"] and now < _token["exp"]:
        return _token["val"]
    headers = {"Content-Type": "application/json",
               "Authorization": f"Basic {os.environ['PPURIO_BASIC_AUTH']}"}
    secret = os.environ.get("PPURIO_PROXY_SECRET")
    if secret:
        headers["X-Proxy-Secret"] = secret
    status, data = _http(f"{_base()}/v1/token", headers)
    tok = data.get("token") or data.get("accesstoken")
    if status != 200 or not tok:
        raise RuntimeError(
            f"뿌리오 토큰 발급 실패: {data.get('code', status)} - {data.get('description', '')}")
    _token["val"] = tok
    # 만료시각(KST) 파싱은 타임존 이슈가 있어, 안전하게 20분 고정 캐시.
    _token["exp"] = now + 20 * 60
    return tok


def send_sms(recipients, content: str, subject: str | None = None) -> dict:
    """SMS/LMS 발송. 90byte 초과면 자동 LMS.

    recipients: [{"phone": "010...", "name": "홍길동"}, ...] 또는 [(phone, name), ...]
    반환: {"sent": int, "skipped": [번호...], "type": "SMS"|"LMS", "raw": dict}
    실패 시 RuntimeError.
    """
    if not is_configured():
        raise RuntimeError(
            "뿌리오 설정이 없습니다(.env 또는 환경변수: "
            "PPURIO_ACCOUNT_ID / PPURIO_BASIC_AUTH / PPURIO_CALLER_NUMBER).")
    token = _get_token()
    msg_type = "LMS" if _byte_len(content) > 90 else "SMS"

    targets, skipped = [], []
    for r in recipients:
        if isinstance(r, dict):
            phone, name = r.get("phone"), r.get("name")
        else:
            phone, name = r[0], (r[1] if len(r) > 1 else None)
        norm = normalize_phone(phone)
        if not norm:
            skipped.append(phone)
            continue
        t = {"to": norm}
        if name:
            t["name"] = name
        targets.append(t)

    if not targets:
        raise RuntimeError("유효한 전화번호가 없습니다.")

    body = {
        "account": os.environ["PPURIO_ACCOUNT_ID"],
        "messageType": msg_type,
        "content": content,
        "from": os.environ["PPURIO_CALLER_NUMBER"],
        "duplicateFlag": "N",
        "targetCount": len(targets),
        "targets": targets,
        "refKey": f"fp_{int(time.time())}"[:32],
    }
    if msg_type == "LMS":
        body["subject"] = subject or _truncate_bytes(content.split("\n")[0], 30)

    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {token}"}
    secret = os.environ.get("PPURIO_PROXY_SECRET")
    if secret:
        headers["X-Proxy-Secret"] = secret

    status, data = _http(f"{_base()}/v1/message", headers,
                         json.dumps(body).encode("utf-8"))
    code = data.get("code")
    if status != 200 or (code is not None and str(code) != "1000"):
        raise RuntimeError(
            f"SMS 발송 실패: {data.get('code', status)} - {data.get('description', 'unknown')}")
    return {"sent": len(targets), "skipped": skipped, "type": msg_type, "raw": data}
