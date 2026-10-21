# 파트너스 콘텐츠 작업

새 글 작성은 `config/brands/makefamily.md`의 최신 소재 수집 절차부터 시작한다. 기존 예약 큐의 소재를 돌려 쓰거나 기억으로 최신 뉴스를 쓰지 않는다. `python3 deploy/collect_drop_sources.py --out /tmp/fp-drop-research-고유시각`으로 X·레딧의 실제 제작 사례와 라운지 자료 목록을 먼저 수집하고, Agent Reach로 원문과 댓글을 확인한다. 공식 블로그는 사실 확인용이며 수집 실패 시 대체 글감으로 채우지 않는다. 수집기는 후보를 찾을 뿐이며 원문 검증·작성·발행을 대신하지 않는다.

무료자료형 글감을 쓰거나 고칠 때 `config/brands/makefamily.md`의 2026-09-22 사용자 기준과 2026-10-19 최신 품질 기준과 실제 운영 피드 컨텐츠 1~30을 먼저 확인한다. 해당 원본은 문체 참고용으로 보존한다.

글감과 자료의 정본은 각각 `config/drops_queue.json`, `config/assets.json`이다. 한국어 초안 후 설치된 최신 humanize-korean을 적용하고, `python3 deploy/lint_drops.py --include-published`로 기존 글 수정까지 검사한다. 검사기는 구조 검사이며 후킹 평가는 실제 원문과의 수동 비교가 필요하다. 글감 큐를 바꾸면 배포 후 운영 DB에서 반영 범위와 본문을 확인한다.

라운지 목록 조회는 검증된 Hycli `fp-lounge catalog` 명령을 사용할 수 있다. `fp-hycli`가 PATH에 있으면 수집기가 자동 사용하고, 직접 지정은 `--lounge-cli /절대경로/fp-hycli`다. 설치되지 않은 환경은 기존 HTTP 경로를 쓴다. Hycli 호출 실패를 HTTP 성공으로 숨기지 않는다. X·레딧 Hycli 조회는 아직 미검증이며 이를 작동 중이라고 보고하지 않는다. 설치·검증 절차는 `config/hycli/README.md`를 따른다.

## 2026-10-21 작성 환경과 완료 판정 수정

작성 전 `python3 deploy/check_drop_authoring.py`로 실제 설치 경로를 확인한다. humanize 정본은 `~/.local/share/im-not-ai`다. 플러그인 목록이나 현재 계정 PATH에 이름이 없다는 이유로 미설치라고 단정하지 않는다. 이 명령의 `installed`는 설치 여부이며 윤문 완료가 아니다. Agent Reach 스킬과 CLI 설치 여부도 구별한다. Exa 검색 성공, 원문 조회 성공, 발행일 확인을 각각 구분한다.

수집기는 원문 스냅샷까지 저장하지만 자동으로 검증 완료 처리하지 않는다. HTTP 200 안의 Reddit 403/로그인 페이지도 실패다. 검색 결과 `search_published_at`은 힌트이며 `published_at`으로 옮기지 않는다. X·Reddit 수집 실패를 라운지 전용 작성으로 대체하지 않는다. 원문이 막히면 접근 장애와 미완료 범위를 보고한다.

큐 신규·수정본은 `deploy/lint_drops.py --include-published --baseline origin/main --reviews 작업폴더/reviews.json`으로 제출한다. 기존 예약본은 보존하되 새로 쓰는 날짜 묶음은 실제 원문과 발행일이 확인된 최근 커뮤니티 사례를 최소 하나 포함한다. 리뷰 파일 계약은 `deploy/DROP_AUTHORING.md`를 따른다. 검사 성공만으로 후킹·조회수 또는 의미 보존의 독립 검증을 주장하지 않는다.
