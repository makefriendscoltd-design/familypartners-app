# 파트너스 콘텐츠 작업

무료자료형 글감을 쓰거나 고칠 때 `config/brands/makefamily.md`의 2026-09-22 사용자 기준과 실제 운영 피드 컨텐츠 1~30을 먼저 확인한다. 해당 원본은 문체 참고용으로 보존한다.

글감과 자료의 정본은 각각 `config/drops_queue.json`, `config/assets.json`이다. 한국어 초안 후 설치된 최신 humanize-korean을 적용하고, `python3 deploy/lint_drops.py --include-published`로 기존 글 수정까지 검사한다. 검사기는 구조 검사이며 후킹 평가는 실제 원문과의 수동 비교가 필요하다. 글감 큐를 바꾸면 배포 후 운영 DB에서 반영 범위와 본문을 확인한다.
