"""데모 데이터 시드 — 오늘 기준으로 '완료/위험군/강퇴후보'가 모두 나오게 구성.

실행: python seed_demo.py
(주의: 기존 DB를 초기화합니다. 운영 DB와 분리하려면 FP_DB 환경변수로 경로 지정)
"""
import sys
from datetime import timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from fp import core, db

db.db_path().unlink(missing_ok=True)
db.init_db()
conn = db.connect()

today = core.today()
J = core.iso(today - timedelta(days=10))  # 10일 전 일괄 가입


def days_ago(n):
    return core.iso(today - timedelta(days=n))


# (이름, 핸들, 연락처, 추적코드, 제출한 'n일 전' 목록)
PARTNERS = [
    # 7일 연속 완료(오늘 포함) — 모범
    ("김실행", "@kim_do", "010-1111-2222", "FP-KIM", list(range(0, 8))),
    # 오늘 포함 3일 연속 완료
    ("이꾸준", "@lee_keep", "010-2222-3333", "FP-LEE", [0, 1, 2, 5, 6]),
    # 오늘 아직 미제출(어제까진 했음) → 위험군
    ("박오늘", "@park_today", "010-3333-4444", "FP-PARK", [1, 2, 3, 4]),
    # 어제 빵꾸 → 강퇴 후보(그제까진 함)
    ("최빵꾸", "@choi_miss", "010-4444-5555", "FP-CHOI", [2, 3, 4, 5]),
    # 가입 후 한 번도 안 올림 → 강퇴 후보
    ("정구경", "@jung_watch", "010-5555-6666", "FP-JUNG", []),
]

for name, handle, contact, code, posted in PARTNERS:
    pid = core.add_partner(conn, name, handle, contact, code, J)
    for n in posted:
        core.add_submission(
            conn, pid,
            f"https://www.threads.net/{handle}/post/demo{n}",
            "threads", days_ago(n),
        )

# 오늘의 글감 1건
core.add_drop(
    conn,
    title="AI로 하루 10분 아낀 실제 사례",
    body="저는 매일 아침 이걸로 30분을 아낍니다.\n어제 한 대표님은 이 방법으로 견적서 작성을 5분에 끝냈어요.\n당신은 아직도 손으로 하고 계신가요?",
    assets=["assets/2026-06-04/demo_thumb.png", "https://drive.google.com/file/demo-video"],
    dtype="marketing",
)

# 매출 몇 건 (정산/몰수 데모용)
def pid_of(name):
    return core.find_partner(conn, name)["id"]

core.add_sale(conn, pid_of("김실행"), "ai-familyday", 30000)
core.add_sale(conn, pid_of("김실행"), "second-brain", 300000)
core.add_sale(conn, pid_of("이꾸준"), "ai-familyday", 30000)
core.add_sale(conn, pid_of("최빵꾸"), "membership", 1590000)  # 강퇴 시 몰수될 매출

# 검수 데모: 이꾸준이 김실행 오늘 글을 그대로 베껴 제출(같은 URL) → 중복 플래그
core.add_submission(conn, pid_of("이꾸준"),
                    "https://www.threads.net/@kim_do/post/demo0", "threads")

# 운영 공지 데모
core.add_notice(conn, "이번 주 토요일 AI 패밀리데이 안내",
                "장소: 강남 / 오후 2시. 참석자는 노트북 지참 부탁드립니다.")

conn.close()
print(f"시드 완료 — 가입일 {J}, 오늘 {core.iso(today)}, 파트너 {len(PARTNERS)}명")
print("다음 실행해 보세요:  python -m fp report")
