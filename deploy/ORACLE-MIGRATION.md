# Family Partners → Oracle 이전 런북 (완전 무료 전환)

목표: familypartners 를 Neon(Vercel) 에서 **기존 Oracle 서버**로 옮겨
`https://familypartners.aimax.ai.kr` 로 서빙. **DB 는 로컬 SQLite, 이미지는 디스크 → egress·DB 요금 0.**

- 서버: Oracle Always Free ARM (`openclaw`, aarch64), Tailscale `ssh -p 3333 ubuntu@100.69.85.89`
- 공개: 박스 위 Caddy(v2.10.2) 서브도메인 블록 + 자동 HTTPS
- 앱: systemd `familypartners.service`, loopback `127.0.0.1:18790`
- 왜 SQLite 되나: `fp/db.py` 는 `DATABASE_URL` 이 **없으면 자동 SQLite**(`FP_DB`). 그래서 서버 .env 에 DATABASE_URL 을 넣지 않는다.

## 배포 키트(이 폴더)
| 파일 | 역할 |
|------|------|
| `deploy_oracle.sh` | 로컬에서 실행 — 패키징→scp→서비스/타이머 설치→Caddy 라우트→헬스체크 |
| `migrate_neon_to_sqlite.py` | Neon → SQLite 1회 데이터 이전(id 보존, 원본 불변) |
| `familypartners.service` | 앱 상주 systemd 유닛 |
| `familypartners-sms.service` + `.timer` | 매일 21시 KST 문자발송(구 Vercel Cron 대체) |
| `deploy_caddy_route.py` | Caddyfile 서브도메인 블록 멱등 주입 |
| `.env.example` | 서버 `.env` 템플릿 |

## 사전조건 (민수/오너 액션 3가지)
1. **DNS** — `familypartners.aimax.ai.kr` 를 **api.aimax.ai.kr 과 동일한 IP**로 지정(A 레코드, 또는 CNAME api.aimax.ai.kr). 대상 IP 확인: `dig +short api.aimax.ai.kr`. 전파 확인: `dig +short familypartners.aimax.ai.kr`.
2. **Neon 접속 URL** — 데이터 이전 1회용. 키 전달 or 로컬 export 시 사용. (원본은 읽기만 함)
3. **문자 발송 시크릿** — 현재 Vercel env 의 `PPURIO_ACCOUNT_ID` / `PPURIO_BASIC_AUTH` / `PPURIO_CALLER_NUMBER` 를 서버 .env 로 이동.

## 절차

### 1. DNS 세팅 (위 사전조건 1)

### 2. 데이터 이전 (로컬, 1회)
```bash
cd <repo>
NEON_URL="<Neon 접속 URL>" python3 deploy/migrate_neon_to_sqlite.py --out /tmp/challenge.db
# 출력에서 테이블별 src=dest 행수 일치 확인
scp -P 3333 /tmp/challenge.db ubuntu@100.69.85.89:/home/ubuntu/familypartners/data/challenge.db
```

### 3. 서버 .env 준비
```bash
ssh -p 3333 ubuntu@100.69.85.89
mkdir -p /home/ubuntu/familypartners
cp /home/ubuntu/familypartners/deploy/.env.example /home/ubuntu/familypartners/.env  # 최초 배포 후엔 이미 존재
nano /home/ubuntu/familypartners/.env   # CRON_SECRET(openssl rand -hex 24), PPURIO_* 채우기, chmod 600
```
(주의: 최초 실행 시 `.env` 가 없으면 `deploy_oracle.sh` 가 예제를 깔고 "값 채우고 재실행" 하며 멈춤 — 정상.)

### 4. 배포
```bash
cd <repo>
bash deploy/deploy_oracle.sh
```

### 5. 검증
```bash
# 서버 내부
ssh -p 3333 ubuntu@100.69.85.89 'systemctl status familypartners --no-pager | head; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18790/feed'
# 외부(DNS/TLS 전파 후)
curl -I https://familypartners.aimax.ai.kr/feed        # 200
# 데이터 확인: 파트너/글감 수가 Neon 과 같은지 육안
# 문자 타이머
ssh -p 3333 ubuntu@100.69.85.89 'systemctl list-timers familypartners-sms.timer --no-pager'
```

### 6. 컷오버
- Oracle 에서 사이트·로그인·글감·이미지·문자 타이머까지 정상 확인되면
- (선택) 기존 도메인 `familypartners.vercel.app` 는 Oracle 로 안내/리다이렉트하거나 사용중지
- **Neon 은 롤백 안전판으로 최소 며칠 유지** → 안정 확인 후 Free 다운그레이드 또는 프로젝트 삭제, Vercel 프로젝트 정리

## 롤백
- 문제 시 Vercel(Neon) 원복은 즉시: DNS 를 원복하거나 vercel.app 주소를 그대로 쓰면 됨(그쪽 코드/DB 그대로 살아있음).
- 서버 롤백: `sudo systemctl stop familypartners; sudo systemctl disable familypartners`, Caddyfile 백업(`/etc/caddy/Caddyfile.bak-familypartners-*`) 복구 후 `sudo systemctl reload caddy`.

## 남은 최적화(후속, 선택)
- 이미지가 지금은 SQLite 안 base64. 로컬이라 egress 무관하지만, 원하면 디스크 파일 저장(`stored_name`/`LIB_DIR`)으로 바꿔 DB 슬림화 가능.
- `get_library` 의 `SELECT *`(blob 포함) → 필요한 컬럼만 SELECT 로 좁히면 로컬 I/O·메모리 절감.
