"""상품 카탈로그 + 파트너 개인 추적링크 생성.

config/products.json 을 읽는다. 경로는 환경변수 FP_PRODUCTS 로 변경 가능.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "products.json"


def config_path() -> Path:
    return Path(os.environ.get("FP_PRODUCTS", str(DEFAULT_CONFIG)))


def load() -> dict:
    with open(config_path(), encoding="utf-8") as f:
        return json.load(f)


def products() -> list[dict]:
    return load().get("products", [])


def find(key: str) -> dict | None:
    for p in products():
        if p["key"] == key:
            return p
    return None


def personal_link(product: dict, code: str | None) -> str:
    """파트너 추적코드를 끼운 개인 구매링크. 코드 없으면 base 그대로."""
    tmpl = load().get("link_template", "{base}?ref={code}")
    if not code:
        return product["base"]
    return tmpl.format(base=product["base"], code=code)


def won(n: int) -> str:
    return f"{n:,}원"
