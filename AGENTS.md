# 파트너스 콘텐츠 작업

새 글 작성은 `config/brands/makefamily.md`의 최신 소재 수집 절차부터 시작한다. 기존 예약 큐의 소재를 돌려 쓰거나 기억으로 최신 뉴스를 쓰지 않는다. `python3 deploy/collect_drop_sources.py --out /tmp/fp-drop-research-고유시각`으로 X·레딧의 실제 제작 사례와 라운지 자료 목록을 먼저 수집하고, Agent Reach로 원문과 댓글을 확인한다. 공식 블로그는 사실 확인용이며 수집 실패 시 대체 글감으로 채우지 않는다. 수집기는 후보를 찾을 뿐이며 원문 검증·작성·발행을 대신하지 않는다.

무료자료형 글감을 쓰거나 고칠 때 `config/brands/makefamily.md`의 2026-09-22 사용자 기준과 2026-10-19 최신 품질 기준과 실제 운영 피드 컨텐츠 1~30을 먼저 확인한다. 해당 원본은 문체 참고용으로 보존한다.

글감과 자료의 정본은 각각 `config/drops_queue.json`, `config/assets.json`이다. 한국어 초안 후 설치된 최신 humanize-korean을 적용하고, `python3 deploy/lint_drops.py --include-published`로 기존 글 수정까지 검사한다. 검사기는 구조 검사이며 후킹 평가는 실제 원문과의 수동 비교가 필요하다. 글감 큐를 바꾸면 배포 후 운영 DB에서 반영 범위와 본문을 확인한다.

라운지 목록 조회는 검증된 Hycli `fp-lounge catalog` 명령을 사용할 수 있다. `fp-hycli`가 PATH에 있으면 수집기가 자동 사용하고, 직접 지정은 `--lounge-cli /절대경로/fp-hycli`다. 설치되지 않은 환경은 기존 HTTP 경로를 쓴다. Hycli 호출 실패를 HTTP 성공으로 숨기지 않는다. X·레딧 Hycli 조회는 아직 미검증이며 이를 작동 중이라고 보고하지 않는다. 설치·검증 절차는 `config/hycli/README.md`를 따른다.
