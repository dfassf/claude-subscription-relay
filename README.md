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

3. **인증**
   - `POST /login` → OAuth URL 반환 → 브라우저에서 인증
   - credential은 Docker 영속 볼륨에 저장 → 컨테이너 재시작해도 유지
   - CLI가 토큰 자동 갱신 처리

## 요구사항

- Docker
- Python 3.12+
- Claude Max 구독

## 설치 & 실행

```bash
# 1. 의존성 설치
uv sync

# 2. Claude 컨테이너 빌드 & 실행
docker compose up -d

# 3. API 서버 실행
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 인증 (최초 1회)

```bash
# 방법 1: OAuth 로그인 (원격 서버용 — SSH 불필요)
curl -X POST http://서버주소:8000/login
# → 반환된 oauth_url을 브라우저에서 열어 로그인

# 방법 2: 토큰 직접 설정 (로컬용)
# .env 파일에 CLAUDE_CODE_OAUTH_TOKEN 설정
# 또는 macOS Keychain에서 자동 추출
```

## API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/login` | OAuth URL 반환 |
| `GET` | `/auth` | 인증 상태 확인 |
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
| `CLAUDE_CODE_OAUTH_TOKEN` | (없음) | OAuth 토큰 직접 지정 |
| `CLAUDE_TIMEOUT` | `120` | CLI 실행 타임아웃 (초) |
| `WORKSPACE_BASE` | `./workspace` | 작업 파일 디렉토리 |
| `TASK_RETENTION` | `3600` | 완료 작업 보관 시간 (초) |
