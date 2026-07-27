"""2026-07-28 글감 4건 등록 — 타겟·형태 태그 포함.

같은 날 글감을 서로 다른 타겟·형태로 내서, 파트너가 자기 계정 색깔에 맞는 걸
골라 쓰게 하는 게 목적. (전원이 같은 글을 올리면 스레드에 도배돼 반응이 죽는다)

실행 — 운영 서버에서:
    cd /home/ubuntu/familypartners
    FP_DB=/home/ubuntu/familypartners/data/challenge.db python3 deploy/drops_2026-07-28.py

같은 날짜로 두 번 실행하면 중복 등록되므로, 이미 등록됐는지 먼저 확인한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fp import core, db  # noqa: E402

DATE = "2026-07-28"

DROPS = [
    dict(
        title="컨텐츠 51 (*무료자료실 메뉴판 가이드북 촬영 OR 직접 만든 메뉴판 화면 녹화)",
        target="자영업자", fmt="리스트형", dtype="marketing",
        body="""사장님들 이거 모르고 돈 쓰십니다

메뉴판 하나 바꾸는데 2주 걸린대요
가격만 올려도 전체 다시 만들어야 한다고

챗GPT로 하면 20분입니다
계절마다 바꿔도 추가 비용 0원이에요

가게 하시는 분들 댓글에 '메뉴판'
정리본 보내드릴게요""",
    ),
    dict(
        title="컨텐츠 51-1 (*자료실 '컨텐츠 17-5 노트북LM으로PPT제작.mp4' 사용하세요)",
        target="직장인", fmt="자가진단", dtype="ai",
        body="""발표 자료 만들다 밤샌 적 있는 사람?

1. 자료는 다 모았는데 시작을 못 함
2. 슬라이드 디자인에서 2시간 날림
3. 다 만들고 보니 순서가 이상함
4. 결국 전날 밤에 갈아엎음
5. 발표는 5분인데 준비는 8시간

3개 이상이면 이거 보셔야 합니다
자료만 넣으면 슬라이드가 나와요

몇 개 해당되세요?
댓글에 숫자만 남겨주세요""",
    ),
    dict(
        title="컨텐츠 51-2 (*동화책 만드는 과정 직접 녹화 강추 — 결과물 보여주는 게 반응 제일 좋음)",
        target="부모", fmt="대화인용", dtype="ai",
        body="""어젯밤에 애가 "또 읽어줘" 해서 네 번 읽었어요

자기 이름이 주인공이라고
계속 들고 오더라고요

아이 이름이랑 좋아하는 것 몇 개만 넣으면
그림까지 들어간 동화책이 나옵니다
5분이면 돼요

몇 살 아이 키우세요?
댓글에 나이 남겨주시면
그 나이대에 맞게 만드는 법 알려드릴게요""",
    ),
    dict(
        title="컨텐츠 51-3 (*영상·사진 없어도 됩니다. 본인 경험 숫자로 바꿔 쓰면 훨씬 좋습니다)",
        target="입문자", fmt="실패담", dtype="marketing",
        body="""AI 툴 결제만 하고 안 쓴 거
저는 네 개입니다

1. 좋아 보여서 결제
2. 첫날 두 시간 만져봄
3. 결과물이 어설퍼서 접음
4. 다음 달 결제 문자 보고 해지

문제는 툴이 아니었어요
뭘 시킬지 안 정하고 시작한 거죠

지금은 결제 전에 이거 먼저 씁니다
"이걸로 내 무슨 일을 없앨 건가"
한 줄 못 쓰면 결제 안 해요

다들 몇 개나 있으세요
결제만 하고 안 쓰는 거 ㅋㅋ""",
    ),
]


def main() -> int:
    db.init_db()
    conn = db.connect()
    try:
        existing = {r["title"] for r in conn.execute(
            "SELECT title FROM drops WHERE drop_date=?", (DATE,))}
        added = skipped = 0
        for d in DROPS:
            if d["title"] in existing:
                print(f"  건너뜀(이미 있음): {d['title'][:40]}")
                skipped += 1
                continue
            did = core.add_drop(conn, d["title"], d["body"], None, d["dtype"], DATE,
                                target=d["target"], fmt=d["fmt"])
            print(f"  등록 #{did} [{d['target']}][{d['fmt']}] {d['title'][:40]}")
            added += 1
        print(f"\n{DATE} — 등록 {added}건 / 건너뜀 {skipped}건")
        total = conn.execute("SELECT COUNT(*) c FROM drops WHERE drop_date=?",
                             (DATE,)).fetchone()["c"]
        print(f"해당 날짜 글감 총 {total}건")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
