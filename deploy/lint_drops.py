"""글감 큐 구조 검사기 — 가독성·CTA·자료 존재를 확인한다.

후킹·사용자 말투·주장 검증은 브랜드 기준 및 실제 원문과 별도 대조한다.

    python deploy/lint_drops.py            # config/drops_queue.json 검사
    python deploy/lint_drops.py <파일>     # 다른 파일 검사

왜 있나
-------
글감 규칙(줄 수, 자료 신청 안내, 없는 자료 약속 금지 …)을 루틴 프롬프트에 계속
쌓아왔는데, 같은 프롬프트가 "10분 안에 push 하라"고도 해서 서로 부딪혔다.
기계가 볼 수 있는 건 기계가 보고, 프롬프트는 판단이 필요한 것만 남긴다.

실제로 2026-08-04 에 세 번 사고가 났고 그중 둘은 여기서 잡힌다:
  - 자료실에 없는 자료를 준다고 약속한 10건 (보낼 게 없음)
그리고 파트너 피드백 "표현이 직관적이지 않다" → 본문에 도구 이름이 없었다.

종료코드: 0 = 전부 통과 / 1 = 불합격 있음
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

KST = timezone(timedelta(hours=9))

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "config" / "drops_queue.json"
ASSETS = ROOT / "config" / "assets.json"

MAX_LINES = 8            # 빈 줄 제외
MAX_CHARS = 150
# 무료자료형은 사용자가 지정한 초기 피드의 짧은 활용 목록을 담는다.
# 숫자·AI 도구명을 억지로 넣는 대신 자료 실재와 가독성을 검사한다.
FREE_MAX_LINES = 10
FREE_MAX_CHARS = 180
FREE_MAX_LINE_CHARS = 30
FREE_MAX_PARAGRAPH_LINES = 3
CTA_WORDS = ("댓글", "답글")
# CTA 줄에 있으면 "뭘 주는지 모르겠는" 문장이 된다
VAGUE = ("이렇게", "이 순서", "이거 보고", "그걸", "이런 식으로", "이 방법")
BANMAL = ("음", "임", "함", "거임", "더라", "잖아", "해봐", "줄게", "거든", "네요?")


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

    # 이미 예약된 글은 보존하고, 이후 신규 작성분에는 확인한 원문 기록을 요구한다.
    source_after = cfg.get("sources_required_after")
    if d.get("format") in ("case_thread", "standalone") or (source_after and (d.get("date") or "") > source_after):
        sources = d.get("sources")
        if not isinstance(sources, list) or not sources:
            bad.append("새 글에 확인한 원문 sources 없음")
        else:
            for source in sources:
                if not isinstance(source, dict):
                    bad.append("원문 출처 형식 오류")
                    continue
                url = urlsplit(str(source.get("url") or ""))
                if url.scheme != "https" or not url.netloc or not source.get("title"):
                    bad.append("원문 HTTPS URL 또는 제목 없음")
                if "published_at" not in source:
                    bad.append("원문 발행일 필드 없음 (미확인은 null)")
                for field in ("collected_at", "verified_at"):
                    try:
                        stamp = datetime.fromisoformat(str(source.get(field) or "").replace("Z", "+00:00"))
                        if stamp.tzinfo is None or stamp > datetime.now(KST) + timedelta(minutes=5):
                            raise ValueError
                    except ValueError:
                        bad.append(f"원문 {field} 확인 시각 없음/오류")

    if not lines:
        return ["본문이 비었음"]
    max_lines = FREE_MAX_LINES if dtype == "evergreen" else MAX_LINES
    max_chars = FREE_MAX_CHARS if dtype == "evergreen" else MAX_CHARS
    is_standalone = d.get("format") == "standalone"
    if is_standalone:
        max_lines, max_chars = 20, 500
        if len(body) > 500:
            bad.append("단독 본문 500자 초과(줄바꿈 포함)")
        if d.get("replies"):
            bad.append("단독 글에 이어지는 본문 댓글이 있음")
        if len((d.get("link_comment") or "")) > 500:
            bad.append("링크 댓글 500자 초과")
    if len(lines) > max_lines:
        bad.append(f"줄 수 {len(lines)} (최대 {max_lines})")
    if chars > max_chars:
        bad.append(f"글자 수 {chars} (최대 {max_chars})")
    if dtype == "evergreen" and not is_standalone:
        if any(len(line) > FREE_MAX_LINE_CHARS for line in lines):
            bad.append(f"한 줄 {FREE_MAX_LINE_CHARS}자 초과")
        paragraphs = re.split(r"\n\s*\n", body.strip())
        if len(lines_of(paragraphs[0])) > 2:
            bad.append("첫 문장 뒤 빈 줄 필요")
        if any(len(lines_of(p)) > FREE_MAX_PARAGRAPH_LINES for p in paragraphs):
            bad.append(f"한 문단 {FREE_MAX_PARAGRAPH_LINES}줄 초과")

    # 무료자료 신청이나 명시한 댓글 키워드는 안내가 필요하다.
    # 정보·의견 글은 내용만으로 끝낼 수 있다.
    is_thread = d.get("format") == "case_thread"
    needs_cta = not is_thread and not is_standalone and (dtype == "evergreen" or bool(d.get("cta_keyword")))
    if needs_cta and not any(w in tail for w in CTA_WORDS):
        bad.append("마지막 두 줄에 댓글 유도 없음")

    # ② 약속한 자료가 실재하는가 (evergreen 만 자료를 준다)
    if dtype == "evergreen" and not is_thread and not is_standalone:
        code = d.get("asset")
        if not code:
            bad.append("evergreen 인데 asset 코드 없음")
        elif code not in cfg["by_code"]:
            bad.append(f"자료실에 없는 자료: {code}")

    # ③ 직관성 — 본문에 도구·기관 이름이 하나는 있어야 뭘 하는 건지 안다
    #    (참여형 marketing 은 주는 게 없으니 면제)
    if dtype == "ai":
        if not any(p.casefold() in (body + '\n' + '\n'.join(d.get('replies') or [])).casefold() for p in cfg["proper"]):
            bad.append("도구·서비스 이름이 본문에 없음(직관성)")

    # ④ CTA 줄이 지시대명사로 뭉개지지 않았는가
    for v in VAGUE:
        if needs_cta and v in tail:
            bad.append(f"CTA에 모호한 표현 '{v}'")
            break

    # 숫자는 근거가 있을 때만 쓴다. 형식상 필수로 요구하지 않는다.

    # ⑥ 댓글 키워드가 이미 쓴 것과 겹치는가
    kw = d.get("cta_keyword")
    if kw and kw in cfg["used_kw_before"]:
        bad.append(f"댓글 키워드 '{kw}' 가 이미 쓴 것과 중복")
    if is_thread:
        replies = d.get("replies")
        if not isinstance(replies, list) or len(replies) < 2:
            bad.append("사례 댓글 최소 2개 필요")
        elif any(not isinstance(t, str) or not t.strip() or len(t) > 500 for t in replies):
            bad.append("댓글 내용 없음 또는 500자 초과")
        closing = d.get("link_comment") or ""
        if len(closing) > 500 or "https://lounge.aimax.ai.kr/worldclass/" not in closing:
            bad.append("마지막 댓글에 라운지 활용노트 링크 필요")
        if not d.get("sources"):
            bad.append("사례 원문 출처 필요")
        if d.get("asset") or d.get("cta_keyword"):
            bad.append("사례 댓글형에 예전 자료 신청 코드·키워드가 남음")
        if re.search(r"https?://", body + "\n" + "\n".join(replies or [])):
            bad.append("링크는 마지막 댓글에만 배치")
        if re.search(r"\([^)]*(?:v[0-9]+|내부용|테스트용)[^)]*\)", body, re.I):
            bad.append("본문에 내부 작업 표기 노출")
    return bad


def check_day(date: str, group: list[dict]) -> tuple[list[str], list[str]]:
    """하루치 4건 사이의 규칙. (불합격, 경고) 를 돌려준다.

    구성 비율 같은 건 판단 영역이라 경고로만 알린다 — 자료를 하나 더 주는 날이
    나쁜 건 아니다. ai 유형이 없어도 콘텐츠 목적에 따라 허용한다.
    """
    bad, warn = [], []
    # Additional reviewed cases do not replace the four scheduled daily posts.
    group = [d for d in group if not d.get("supplemental")]
    if not group:
        return bad, warn
    if len(group) != 4:
        bad.append(f"{len(group)}건 (하루 4건이어야 함)")
    if date >= "2026-10-22":
        if any(d.get('format') != 'case_thread' for d in group):
            bad.append('모든 글감은 본문 + 대댓글 구조여야 함')
        if any('\n' in (d.get('body') or '').strip() for d in group):
            bad.append('후킹 본문은 한 줄로 작성')
    elif all((d.get("date") or "") >= "2026-10-20" for d in group):
        if sum(d.get("format") == "case_thread" for d in group) != 1:
            bad.append("일일 타래 1개 필요")
        if sum(d.get("format") == "standalone" for d in group) != 3:
            bad.append("일일 단독 글 3개 필요")
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
        warn.append(f"구성 {types} — 권장은 evergreen2/ai1/marketing1")
    return bad, warn


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=QUEUE)
    parser.add_argument("--include-published", action="store_true",
                        help="기존 글 수정 시 지난 날짜와 오늘도 엄격하게 검사")
    parser.add_argument('--baseline', default='origin/main',
                        help='Published queue Git ref for new/changed editorial review')
    parser.add_argument('--reviews', type=Path, help='Original and humanize review manifest')
    parser.add_argument('--structure-only', action='store_true', help='Existing text rearrangement only; no new claims or sources')
    args = parser.parse_args(argv[1:])
    if args.path.resolve() == QUEUE.resolve():
        import subprocess
        command = [sys.executable, str(ROOT / 'deploy/check_drop_authoring.py'),
                   '--baseline', args.baseline]
        if args.structure_only:
            command += ['--structure-only']
        if args.reviews:
            command += ['--reviews', str(args.reviews)]
        if subprocess.run(command, cwd=ROOT).returncode:
            return 1
    path = args.path
    a = load(ASSETS)
    drops = load(path).get("drops") or []

    # 이번 큐에서 쓰는 키워드는 "이미 쓴 것"에서 빼고 본다(자기 자신과 충돌 방지)
    now_kw = {d.get("cta_keyword") for d in drops if d.get("cta_keyword")}
    cfg = {
        "by_code": {x["code"]: x for x in a["assets"]},
        "proper": a["_고유명사"],
        "used_kw_before": set(a["_이미쓴_댓글키워드"]) - now_kw,
        "sources_required_after": a.get("_research", {}).get("sources_required_after"),
    }

    # 기본은 오늘 이후 검사. 기존 글 수정은 --include-published로 전부 검사한다.
    today = datetime.now(KST).strftime("%Y-%m-%d")
    fails = 0
    by_date: dict[str, list[dict]] = defaultdict(list)
    skipped = 0
    for d in drops:
        if not args.include_published and (d.get("date") or "") < today:
            skipped += 1
            continue
        by_date[d.get("date", "?")].append(d)
    if skipped:
        print(f"(지난 날짜 {skipped}건은 건너뜀 — 오늘 {today} 이후만 검사)")

    warns = 0
    for date in sorted(by_date):
        group = by_date[date]
        print(f"\n── {date}")
        day_bad, day_warn = check_day(date, group)
        for msg in day_bad:
            print(f"   ✗ [하루] {msg}")
            fails += 1
        for msg in day_warn:
            print(f"   ! [하루] {msg}")
            warns += 1
        # 오늘자는 자정에 이미 공개됐다 — 고쳐도 먼저 본 파트너에겐 안 닿으므로
        # 실패로 세지 않고 경고로만 알린다.
        live = date == today and not args.include_published
        for d in group:
            num = (d.get("title") or "").split(" (")[0].replace("컨텐츠 ", "")
            lines = lines_of(d.get("body") or "")
            chars = len((d.get("body") or "").replace("\n", ""))
            bad = check_one(d, cfg)
            mark = "✓" if not bad else ("!" if live else "✗")
            asset = cfg["by_code"].get(d.get("asset"), {}).get("name", "-")
            print(f"   {mark} {num:<6} {d.get('type'):<9} {len(lines)}줄 {chars:>3}자 "
                  f"| {lines[0][:22] if lines else '':<24} | {asset[:26]}")
            for m in bad:
                print(f"       ↳ {m}{' (이미 공개됨 — 참고)' if live else ''}")
                if live:
                    warns += 1
                else:
                    fails += 1

    checked = sum(len(v) for v in by_date.values())
    print(f"\n{'=' * 60}\n검사 {checked}건 · 불합격 {fails}건 · 경고 {warns}건")
    if fails:
        print("불합격이 있으면 push 하지 말고 그 건만 고치세요.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
