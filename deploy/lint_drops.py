"""글감 큐 검사기 — 규칙을 프롬프트가 아니라 코드로 강제한다.

    python deploy/lint_drops.py            # config/drops_queue.json 검사
    python deploy/lint_drops.py <파일>     # 다른 파일 검사

왜 있나
-------
글감 규칙(줄 수, 댓글 유도, 없는 자료 약속 금지 …)을 루틴 프롬프트에 계속
쌓아왔는데, 같은 프롬프트가 "10분 안에 push 하라"고도 해서 서로 부딪혔다.
기계가 볼 수 있는 건 기계가 보고, 프롬프트는 판단이 필요한 것만 남긴다.

실제로 2026-08-04 에 세 번 사고가 났고 그중 둘은 여기서 잡힌다:
  - 댓글 유도 없이 끝낸 글감 20건 (반응 0)
  - 자료실에 없는 자료를 준다고 약속한 10건 (보낼 게 없음)
그리고 파트너 피드백 "표현이 직관적이지 않다" → 본문에 도구 이름이 없었다.

종료코드: 0 = 전부 통과 / 1 = 불합격 있음
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "config" / "drops_queue.json"
ASSETS = ROOT / "config" / "assets.json"

MAX_LINES = 8            # 빈 줄 제외
MAX_CHARS = 150
CTA_WORDS = ("댓글", "답글")
# CTA 줄에 있으면 "뭘 주는지 모르겠는" 문장이 된다
VAGUE = ("이렇게", "이 순서", "이거 보고", "그걸", "이런 식으로", "이 방법")
BANMAL = ("음", "임", "함", "거임", "더라", "잖아", "해봐", "줄게", "거든", "네요?")
NUM = re.compile(r"[0-9]|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|서른|마흔|백|천|만")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines_of(body: str) -> list[str]:
    return [l.strip() for l in body.split("\n") if l.strip()]


def check_one(d: dict, cfg: dict) -> list[str]:
    """글감 1건 검사 — 실패 사유 목록을 돌려준다(빈 리스트면 합격)."""
    bad = []
    body = d.get("body") or ""
    lines = lines_of(body)
    chars = len(body.replace("\n", ""))
    tail = "\n".join(lines[-2:])
    dtype = d.get("type")

    if not lines:
        return ["본문이 비었음"]
    if len(lines) > MAX_LINES:
        bad.append(f"줄 수 {len(lines)} (최대 {MAX_LINES})")
    if chars > MAX_CHARS:
        bad.append(f"글자 수 {chars} (최대 {MAX_CHARS})")

    # ① 댓글 유도 — 이게 없으면 실측상 답글이 0~1개다
    if not any(w in tail for w in CTA_WORDS):
        bad.append("마지막 두 줄에 댓글 유도 없음")

    # ② 약속한 자료가 실재하는가 (evergreen 만 자료를 준다)
    if dtype == "evergreen":
        code = d.get("asset")
        if not code:
            bad.append("evergreen 인데 asset 코드 없음")
        elif code not in cfg["by_code"]:
            bad.append(f"자료실에 없는 자료: {code}")

    # ③ 직관성 — 본문에 도구·기관 이름이 하나는 있어야 뭘 하는 건지 안다
    #    (참여형 marketing 은 주는 게 없으니 면제)
    if dtype in ("evergreen", "ai"):
        if not any(p in body for p in cfg["proper"]):
            bad.append("도구·서비스 이름이 본문에 없음(직관성)")

    # ④ CTA 줄이 지시대명사로 뭉개지지 않았는가
    for v in VAGUE:
        if v in tail:
            bad.append(f"CTA에 모호한 표현 '{v}'")
            break

    # ⑤ 구체적 숫자
    if not NUM.search(body):
        bad.append("구체적 숫자 없음")

    # ⑥ 댓글 키워드가 이미 쓴 것과 겹치는가
    kw = d.get("cta_keyword")
    if kw and kw in cfg["used_kw_before"]:
        bad.append(f"댓글 키워드 '{kw}' 가 이미 쓴 것과 중복")
    return bad


def check_day(date: str, group: list[dict]) -> list[str]:
    """하루치 4건 사이의 규칙."""
    bad = []
    if len(group) != 4:
        bad.append(f"{len(group)}건 (하루 4건이어야 함)")
    tg = [d.get("target") for d in group]
    fm = [d.get("fmt") for d in group]
    if len(set(tg)) != len(tg):
        bad.append(f"타겟 중복: {tg}")
    if len(set(fm)) != len(fm):
        bad.append(f"형태 중복: {fm}")
    if sum(1 for d in group if d.get("fmt") == "리스트형") > 1:
        bad.append("리스트형 2건 이상")
    if not any(any(b in (d.get("body") or "") for b in BANMAL) for d in group):
        bad.append("반말 위주 글감이 없음")
    types = sorted(d.get("type") for d in group)
    if types != ["ai", "evergreen", "evergreen", "marketing"]:
        bad.append(f"구성이 evergreen2/ai1/marketing1 이 아님: {types}")
    return bad


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else QUEUE
    a = load(ASSETS)
    drops = load(path).get("drops") or []

    # 이번 큐에서 쓰는 키워드는 "이미 쓴 것"에서 빼고 본다(자기 자신과 충돌 방지)
    now_kw = {d.get("cta_keyword") for d in drops if d.get("cta_keyword")}
    cfg = {
        "by_code": {x["code"]: x for x in a["assets"]},
        "proper": a["_고유명사"],
        "used_kw_before": set(a["_이미쓴_댓글키워드"]) - now_kw,
    }

    fails = 0
    by_date: dict[str, list[dict]] = defaultdict(list)
    for d in drops:
        by_date[d.get("date", "?")].append(d)

    for date in sorted(by_date):
        group = by_date[date]
        print(f"\n── {date}")
        for msg in check_day(date, group):
            print(f"   ✗ [하루] {msg}")
            fails += 1
        for d in group:
            num = (d.get("title") or "").split(" (")[0].replace("컨텐츠 ", "")
            lines = lines_of(d.get("body") or "")
            chars = len((d.get("body") or "").replace("\n", ""))
            bad = check_one(d, cfg)
            mark = "✓" if not bad else "✗"
            asset = cfg["by_code"].get(d.get("asset"), {}).get("name", "-")
            print(f"   {mark} {num:<6} {d.get('type'):<9} {len(lines)}줄 {chars:>3}자 "
                  f"| {lines[0][:22] if lines else '':<24} | {asset[:26]}")
            for m in bad:
                print(f"       ↳ {m}")
                fails += 1

    print(f"\n{'=' * 60}\n글감 {len(drops)}건 · 불합격 {fails}건")
    if fails:
        print("불합격이 있으면 push 하지 말고 그 건만 고치세요.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
