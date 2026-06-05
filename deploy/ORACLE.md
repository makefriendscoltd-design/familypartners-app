# Oracle Cloud 평생무료 VM 배포 가이드

> 목표: 24시간 켜져 있는 무료 서버에 패밀리 파트너스를 올려, 파트너가 인터넷으로 접속.
> 비용 0원(Always Free 등급). 아래 **1~6단계는 박상철님이** 콘솔에서, **설치(7단계)는 Claude가** 터미널로 진행합니다.

---

## 1. 가입
1. https://www.oracle.com/cloud/free/ → **Start for free**
2. 이메일·정보 입력, **카드 인증**(결제 아님, 본인확인용). 등급은 **Always Free** 유지.
3. 홈 지역(Home Region)은 가까운 곳(예: South Korea Central - Chuncheon) 선택.

## 2. VM(인스턴스) 생성
1. 콘솔 → 메뉴 → **Compute → Instances → Create instance**
2. 이름: `familypartners`
3. Image & shape:
   - Image: **Canonical Ubuntu** (22.04 또는 24.04)
   - Shape: **Always Free 적용 대상** 선택 — `VM.Standard.E2.1.Micro`(x86) 또는 `Ampere A1`(ARM, 1 OCPU/6GB). "Always Free eligible" 표시 확인.

## 3. SSH 키 (접속 열쇠)
1. 생성 화면 "Add SSH keys" → **Generate a key pair for me** →
   **Save private key**(파일) 다운로드. 이 파일이 서버 접속 열쇠입니다. 잘 보관.
   (또는 본인 키 업로드)
2. **Create** 클릭 → 1~2분 후 인스턴스 생성됨.

## 4. 공개 IP 확인
- 인스턴스 상세 화면의 **Public IP address** 를 메모. (예: 152.x.x.x)

## 5. 다운로드한 키 파일 위치
- 보통 `다운로드` 폴더의 `ssh-key-….key` 파일. 이 **파일 경로**를 메모.

## 6. 포트 열기 (Security List)
1. 인스턴스 상세 → **Virtual Cloud Network**(VCN) 클릭
2. **Security Lists** → 기본 보안목록 클릭 → **Add Ingress Rules**
3. 입력:
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: **TCP**
   - Destination Port Range: `8080`
4. **Add Ingress Rules** 저장.

---

## 7. 앱 설치 (여기부터 Claude가 진행)
위 1~6 끝나면 **공개 IP + 키 파일 경로**를 Claude에게 알려주세요.
그러면 Claude가 박상철님 PC 터미널에서:
1. 이 폴더의 코드를 VM으로 전송(scp)
2. `bash deploy/setup.sh` 실행 → 24시간 자동 구동 서비스 등록 + 방화벽
3. `http://<공개IP>:8080` 접속 확인까지

마치면 그 주소가 운영 사이트입니다. 처음 접속 시 관리자 비밀번호를 설정하면 됩니다.

---

## 보안 메모 (중요)
- 위 구성은 **http**(암호화 없음)입니다. 관리자 비밀번호가 평문으로 오갈 수 있어요.
- 실사용 전, **무료 HTTPS**를 앞에 두는 걸 권장: Cloudflare(도메인 연결, 무료) 또는
  Cloudflare Tunnel 을 VM에 설치. 도입 시 Claude가 같이 세팅해 드립니다.
