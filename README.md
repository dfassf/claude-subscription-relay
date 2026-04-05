# claude-docker-sandbox

Claude Max 구독을 활용한 샌드박스 API 서버.
Claude Code CLI를 Docker 컨테이너 안에 격리하고, FastAPI로 HTTP API를 제공한다.

## 작동 원리

```
┌─────────────┐     HTTP      ┌──────────────┐   docker exec   ┌─────────────────────┐
│  클라이언트   │ ──────────▶  │  FastAPI 서버  │ ─────────────▶ │  Docker 컨테이너      │
│  (웹, 봇 등) │ ◀──────────  │  (큐 워커)     │ ◀───────────── │  (Claude Code CLI)  │
└─────────────┘   JSON 응답   └──────────────┘    stdout       └─────────────────────┘
                                    │                                    │
                                    │                           ┌────────┴────────┐
                                    │                           │ /workspace (RO) │ ← 파일 격리
                                    │                           │ /root/.claude   │ ← 인증 정보 (영속 볼륨)
                                    │                           └─────────────────┘
                                    │
                              asyncio.Queue
                              (순차 처리, rate limit 방지)
```

### 핵심 구조

1. **Docker 컨테이너** (`sleep infinity`로 상주)
   - Claude Code CLI가 설치된 Node.js 컨테이너
   - 호스트 파일시스템 접근 불가 (보안 격리)
   - `/workspace`만 마운트 (read-only)

2. **FastAPI 서버** (호스트에서 실행)
   - HTTP 요청을 받아 `docker exec`로 컨테이너에 명령 전달
   - `asyncio.Queue`로 요청을 순차 처리 (Claude Max rate limit 방지)
   - 파일 업로드 → workspace에 임시 저장 → 작업 완료 후 삭제

3. **인증** — 컨테이너 독립 OAuth 세션
   - `POST /login` → OAuth URL 반환 → 브라우저에서 인증 → 코드 입력
   - credential은 Docker 영속 볼륨(`/root/.claude`)에 저장
   - 컨테이너 재시작해도 유지, 로컬 Claude Code와 토큰 충돌 없음

## 요구사항

- Docker
- Claude Max 구독

## 빠른 시작

```bash
git clone <repo-url> && cd claude-docker-sandbox
./setup.sh
```

`setup.sh`가 알아서 처리합니다:
- `.env` 생성 및 API_KEY 자동 생성
- Telegram 봇 설정 (선택)
- Docker 빌드 및 실행

## 인증

컨테이너가 자체 OAuth 세션을 갖도록 수동 PKCE 플로우로 인증합니다.
로컬 Claude Code와 독립적이라 토큰 회전 충돌이 없습니다.

### 방법: 수동 PKCE OAuth

`claude auth login`은 Docker 컨테이너 안에서 stdin 문제로 코드 입력이 안 되므로,
직접 PKCE 코드를 생성하고 `api.anthropic.com`에서 토큰을 교환합니다.

```bash
# 1. PKCE 코드 생성 + OAuth URL 발급
python3 -c "
import secrets, hashlib, base64, json
v = secrets.token_urlsafe(64)
c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b'=').decode()
s = secrets.token_urlsafe(32)
json.dump({'code_verifier': v, 'state': s}, open('/tmp/pkce.json', 'w'))
print(f'https://claude.ai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fconsole.anthropic.com%2Foauth%2Fcode%2Fcallback&scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference+user%3Asessions%3Aclaude_code+user%3Amcp_servers+user%3Afile_upload&code_challenge={c}&code_challenge_method=S256&state={s}')
"

# 2. 브라우저에서 URL 열기 → 로그인 → 코드 복사 (code#state 형식)

# 3. 토큰 교환 (api.anthropic.com 사용 — console.anthropic.com은 Cloudflare 차단)
CODE="붙여넣은_코드"
STATE=$(python3 -c "import json; print(json.load(open('/tmp/pkce.json'))['state'])")
VERIFIER=$(python3 -c "import json; print(json.load(open('/tmp/pkce.json'))['code_verifier'])")

curl -s -X POST https://api.anthropic.com/v1/oauth/token \
  -H "Content-Type: application/json" \
  -d "{\"grant_type\":\"authorization_code\",\"code\":\"$CODE\",\"state\":\"$STATE\",\"client_id\":\"9d1c250a-e61b-44d9-88ed-5944d1962f5e\",\"redirect_uri\":\"https://console.anthropic.com/oauth/code/callback\",\"code_verifier\":\"$VERIFIER\"}"

# 4. 응답의 access_token, refresh_token을 컨테이너에 credential로 저장

# 인증 상태 확인
curl http://서버주소:8000/auth -H "X-API-Key: <your-key>"
```

### OAuth 엔드포인트 주의사항

| 엔드포인트 | 상태 |
|-----------|------|
| `api.anthropic.com/v1/oauth/token` | 작동 (권장) |
| `console.anthropic.com/v1/oauth/token` | Cloudflare 429 차단 |
| `platform.claude.com/v1/oauth/token` | Cloudflare 429 차단 |

비공식 클라이언트(curl 등)에서 토큰 교환 시 `console.anthropic.com`과
`platform.claude.com`은 Cloudflare가 429로 차단합니다.
`api.anthropic.com`을 사용해야 합니다.

> **주의**: 이전에는 `.env`에 `CLAUDE_CODE_OAUTH_TOKEN`을 설정하여 폴백으로 사용했으나,
> stale 토큰이 컨테이너 자체 credentials를 덮어쓰는 문제가 있어 제거했습니다.
> 컨테이너 독립 로그인만 사용합니다.

## API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/login` | OAuth URL 반환 |
| `GET` | `/auth` | 인증 상태 확인 |
| `POST` | `/sessions/clear` | 대화 세션 초기화 |
| `POST` | `/ask` | 텍스트 프롬프트 |
| `POST` | `/ask/file` | 파일 + 프롬프트 |
| `GET` | `/task/{id}` | 작업 결과 조회 |
| `GET` | `/health` | 서버 상태 |

### 사용 예시

```bash
# 질문
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "파이썬으로 퀵소트 구현해줘"}'
# → {"task_id": "abc123", "status": "queued"}

# 결과 조회
curl http://localhost:8000/task/abc123
# → {"task_id": "abc123", "status": "completed", "result": "..."}

# 파일 분석
curl -X POST http://localhost:8000/ask/file \
  -F "prompt=이 코드 리뷰해줘" \
  -F "files=@my_code.py"

# 시스템 프롬프트 지정
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "코드 리뷰해줘", "system_prompt": "한국어로 답하고 보안 취약점 위주로 봐줘"}'
```

## 보안

| 계층 | 보호 내용 |
|------|----------|
| Docker 격리 | Claude가 호스트 파일시스템에 접근 불가 |
| read-only 마운트 | workspace 파일 변조 불가 |
| 리소스 제한 | 메모리 512MB, CPU 1코어 |
| 임시 파일 정리 | 작업 완료 즉시 workspace 삭제 |
| 큐 순차 처리 | rate limit 초과 방지 |

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `API_KEY` | (없음) | API 인증 키 |
| `CLAUDE_TIMEOUT` | `120` | CLI 실행 타임아웃 (초) |
| `TELEGRAM_BOT_TOKEN` | (없음) | Telegram 봇 토큰 |
| `TELEGRAM_CHAT_ID` | `0` | Telegram 채팅 ID |
| ~~`CLAUDE_CODE_OAUTH_TOKEN`~~ | — | 제거됨 (stale 토큰 문제) |
| ~~`CLAUDE_CODE_REFRESH_TOKEN`~~ | — | 제거됨 (stale 토큰 문제) |
