# 글감 자동 공급 운영 정본

설정 정본: `config/drop_maintenance.json`. 매일09:00·21:00 KST.
오늘 누락부터 먼저 채우고 내일부터5일까지 정규4+실천2씩 유지한다.
원고 정본은 기존 `config/drops_queue.json`, 자료 정책은 `config/assets.json`.
설명 대댓글 수는 설정 정본의 `reply_count`를 따른다. 자료 링크는 별도다.
모델 출력 스키마와 배포 전 검증이 같은 설정을 읽는다. 문단 분할·반복으로 채우지 않는다.

2026-11-03 확인: 옛 Claude Cloud trigger
`trig_01K2ZuKSFmbTtPgnJ1AFjJdJ`는 실제로 계속 실행됐다.
최근 실행은 필수 수집·윤문 런타임이 없어 push0이었다.
제공자 UI의 성공/완료 표시는 원고 등록 성공을 뜻하지 않았다.
실행 근거: `_workspace/2026-11-03-auto-repair/cloud-latest-run.json`.

Mac 실행: `python3 deploy/drop_maintenance.py --run`.
점검: `python3 deploy/drop_maintenance.py --check`.
공유 실행기 `loopguard/bin/agent-run --only codex`가 읽기 전용으로 작성한다.
Claude 세션이나 직접 `codex exec`를 새로 호출하지 않는다.
부모 제어기는 독점 잠금 아래 원문·날짜·인용·윤문·lint·실제 렌더러를 검증한다.
기존 원고·운영 DB가 바뀌면 덮어쓰지 않고 중단한다.
검증한 날짜 한 묶음만 임시 Git index로 commit/push한다.
운영 HEAD, 기존 행 해시, 신규 본문, 댓글 복사, 이미지 해시까지 확인해야 성공이다.
실패는 nonzero 종료 및 `~/.local/share/familypartners-drop-maintenance/status.json`에 남는다.
단계는 collecting/authoring/validating/deploying/complete로 구분하고 검사 출력도 남긴다.
원본JSON은 그대로 보존하고 텍스트 스냅샷만 UTF-8/LF로 정규화한다.
모델은 원문 앵커ID를 선택하고, 제어기가 실제 인용문을 추출해 원문과 대조한다.
부분적으로 이미 채운 날짜는 자동 재작성하지 않고 검토 필요 상태로 멈춘다.

LaunchAgent는 `com.local.familypartners-drop-maintenance`이며 RunAtLoad=false.
클라우드 루틴 비활성화의 실제 제공자 증거와 실제6개 발행 검증이 있어야 설치된다.
동일 작업 중복 설치는 거부한다. 기존 다른 작업·로그·세션에는 손대지 않는다.
Mac이 꺼져 있거나 계정·브라우저 연결이 끊기면 실패할 수 있으므로 상태 파일과
launchd 종료 코드를 함께 본다. 검증을 없애거나 실패를 성공으로 바꾸지 않는다.
