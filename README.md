# 패밀리 파트너스 — 운영 시스템

> 파트너 공동판매 + **매일 콘텐츠 챌린지**를 사람 손이 아니라 시스템으로 굴린다.
> 파이썬 표준 라이브러리만 사용 — 설치 불필요, 윈도우에서 바로 실행.

## 확정된 운영 규칙 (시스템에 박혀 있음)
- **매일 1건 발행 — 주말·공휴일 예외 없음.**
- **1회 누락 = 즉시 강퇴.** 사정 안 봐줌.
- 누락 강퇴 시 **그 달 수익 몰수.**
- **월 정산.**

---

## 들어온 파트너 처리 순서 (온보딩)

```powershell
python -m fp init                                  # 최초 1회

# 1) 등록 (추적코드는 수익 측정의 핵심 — 꼭 발급)
python -m fp partner add --name 홍길동 --handle @gildong --contact 010-1234-5678 --code FP-HONG

# 2) 온보딩 키트 생성 → out/onboard_홍길동.md 로 저장
#    개인 구매링크가 끼워진 ① 카톡방 공지 ② 프로필 ③ 구매링크 묶음 ④ 규칙
python -m fp onboard --who 홍길동
```

키트는 **템플릿 공통, 링크만 개인별**. 파트너는 받아서 카톡방 공지/프로필에 그대로 붙이면 끝.
상품·가격·링크·쉐어율은 [config/products.json](config/products.json) 에서 수정합니다.

---

## 매일 글감/소스 배포 (운영자가 매일)

```powershell
# 영상/사진은 assets/날짜/ 에 두거나 공유 링크(Drive 등) 사용 — assets/README.md 참고
python -m fp drop add --type marketing `
  --title "AI로 하루 10분 아낀 사례" `
  --body "저는 매일 아침 이걸로 30분을 아낍니다..." `
  --assets "assets/2026-06-04/video1.mp4,https://drive.google.com/..."

python -m fp drop today        # 파트너 단톡방에 방송할 형태로 출력(본문+다운로드 첨부)
python -m fp drop list         # 최근 글감 목록
```

유형: `ai`(AI 생성) / `marketing`(기존 마케팅 자료) / `evergreen`(상시 자료). 기존 자료를 하나씩 풀 때는 `marketing`/`evergreen`.

---

## 매일 챌린지 운영 (운영자 5분)

```powershell
python -m fp ingest --csv data\submissions.csv   # 카톡/폼 제출 일괄 적재
python -m fp report                              # 완료 / 위험군 / 강퇴후보
python -m fp reminders                           # 보낼 메시지 복붙
python -m fp enforce                             # 누락자 미리보기
python -m fp enforce --yes                       # 실제 강퇴 + 당월 몰수 집행
```

제출 1건씩: `python -m fp submit --who @gildong --url <링크>`

---

## 운영 관리 (검수·이력·랭킹)
제출이 **진짜인지** 검수해야 "1회 누락 즉시 강퇴" 규칙이 공정해집니다. 무효 처리하면 그 제출은 출석에서 빠집니다.

```powershell
python -m fp review                       # 오늘 제출 목록 (✗무효 / ⚠중복 자동 플래그)
python -m fp reject --id 12 --reason "스팸/재탕"   # 제출 무효 처리(출석 미인정)
python -m fp board                        # 활동 랭킹(연속일 리더보드)
python -m fp partner show --who 홍길동      # 파트너 전체 이력(제출·경고·상태 타임라인)
python -m fp partner warn --who 홍길동 --reason "..."   # 경고 기록
python -m fp notice add --title "일정 변경" --body "..."  # 운영 공지(포털에 게시)
python -m fp notice list / off --id N      # 공지 목록 / 내림
```

- **⚠중복** = 같은 링크가 2회 이상(재탕/베끼기). 무효 처리하면 중복 계산에서도 빠집니다.
- 웹에서도: `/review`(무효 버튼) · `/board`(랭킹) · 대시보드/랭킹의 **이름 클릭 → 파트너 상세**.

---

## (보류) 매출 & 월 정산
> 추적링크가 확정되기 전까지 보류. `sale`/`settle`/`month` 명령은 남아 있고, 링크 방식(쿠폰/링크디) 정해지면 금액만 넣어 쓰면 됩니다. 정산은 사람이 하므로 `settle`은 확인용 리포트.

```powershell
# 매출 기록 (개인 추적링크로 들어온 결제)
python -m fp sale add --who 홍길동 --product ai-familyday --amount 30000 --ref 주문번호
python -m fp sale ingest --csv data\sales.csv

python -m fp settle --month 2026-06    # 파트너별 매출·쉐어정산·몰수 반영
python -m fp month  --month 2026-06    # 챌린지 달성률(인증용)
```

`sale ingest` CSV 헤더: `who,product,amount[,sale_date,order_ref]`

---

## 파트너 상태 관리
```powershell
python -m fp partner list
python -m fp partner kick --who 홍길동 --reason "..."   # 수동 강퇴
python -m fp partner pause --who 홍길동                  # 일시중지
python -m fp partner reactivate --who 홍길동
```

`--who` 는 이름 / @핸들 / id 아무거나. 어떤 명령이든 `--date 2026-06-04` 로 기준일 지정 가능.

---

## 제출 CSV 형식 (`ingest`)
헤더 필수, 순서 무관. `who`,`url` 만 있으면 됨. 구글폼 응답시트 그대로 사용 가능.
```csv
who,url,channel,post_date,note
@gildong,https://www.threads.net/@gildong/post/abc,threads,,
```
`channel` 생략 시 threads · `post_date` 생략 시 오늘 · 미등록 파트너 행은 건너뜀.

---

## 데모로 먼저 보기
```powershell
python seed_demo.py     # 완료/위험군/강퇴후보 + 글감 + 매출까지 더미 세팅 (기존 DB 초기화)
python -m fp report
python -m fp enforce --yes
python -m fp settle --month 2026-06
```

## 두 개의 면 (운영자 / 파트너)
한 서버, 한 DB. 경로로 갈립니다.

| | 운영자 (박상철) | 파트너 (회원) |
|---|---|---|
| 진입 | `/` 대시보드 (+CLI) | 개인 토큰 링크 `/me?t=...` |
| 보는 것 | 전체 보드·정산·온보딩 | 본인 스트릭·오늘 글감·내 판매링크·**공지·내 이력** |
| 하는 것 | 강퇴 집행·정산 확인·**공지 게시** | **글 제출**(출석) |
| 공통 | — | **공개 인증 보드 `/wall`** (모두의 실행을 투명 공개) |
| 로그인 | 없음(로컬) | 없음(토큰=인증) |

파트너 제출은 운영자 보드에 **즉시** 반영됩니다(같은 DB).

## 운영 서버 (브라우저)
```powershell
python -m fp serve            # → http://localhost:8000
```
- 운영자: http://localhost:8000/ (대시보드·월정산·글감·온보딩)
- 파트너 셀프등록: http://localhost:8000/join  ← 카톡 입장 후 이 링크 안내
- 파트너 작업실: http://localhost:8000/me?t=토큰  ← 온보딩 키트에 자동 포함

운영자 대시보드는 읽기 전용(강퇴 집행은 CLI `enforce --yes`). 파트너 포털만 쓰기(글 제출) 허용.

> 배포 시: 파트너가 접속하려면 공개 도메인 필요. `FP_PORTAL_BASE` 환경변수로 온보딩 키트의 포털 주소를 실제 도메인으로 지정하세요(기본 http://localhost:8000).

단톡방 공지·SNS 프로필 문구는 [config/templates/](config/templates/) 의 txt 파일에서 수정합니다(매달 바뀌는 내용). `(개별링크 삽입)` 자리는 `{{LINK_FAMILYDAY}}` 등 토큰으로, 파트너 추적링크가 자동 삽입됩니다.

## 운영 DB 분리
```powershell
$env:FP_DB = "D:\coding\familypartners\data\live.db"
python -m fp init
```

## 구조
```
fp/
  db.py        스키마(partners/submissions/events/drops/sales)
  core.py      판정 엔진 · 글감 · 매출 · 강퇴집행 · 정산
  products.py  상품 카탈로그 + 개인 추적링크
  onboard.py   온보딩 키트(카톡방·프로필·링크)
  messages.py  리마인더·경고 멘트
  cli.py       명령어
  server.py    운영 대시보드 웹서버(stdlib)
config/products.json        상품·가격·링크·쉐어율 (여기 수정)
config/templates/*.txt      단톡방 공지·SNS 프로필 문구 (매달 수정)
assets/                글감 첨부 영상·사진 (assets/README.md)
out/                   생성된 온보딩 키트
data/                  DB·CSV
```

## 다음 확장 포인트
- **수집 자동화**: 제출을 Threads API(`GET /me/threads`)로. 파트너 OAuth 필요.
- **콘텐츠 생성(직원 1호 멀티플라이어)**: 글감 1개 → 스레드 3종 자동 변환(Claude API).
- **발송 자동화**: `reminders`/`drop today` 출력 → 카톡/문자 API 연동.
- **추적링크 측정**: 랜딩/결제에서 `?ref=코드` 캡처 → `sale ingest` 자동화.
