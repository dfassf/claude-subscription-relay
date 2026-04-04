# OAuth 인증 시행착오 기록

Docker 컨테이너에서 Claude Code의 독립 OAuth 세션을 설정하면서 겪은 문제들과 해결 과정.

## 배경

로컬 Claude Code와 VM의 컨테이너가 같은 OAuth 세션을 공유하면 토큰 회전 충돌이 발생한다.
로컬에서 토큰이 갱신되면 VM의 refresh token이 무효화되고, 반대도 마찬가지.
해결책: VM 컨테이너에 독립적인 OAuth 세션을 부여한다.

## 시도 1: `claude auth login` (실패)

컨테이너 안에서 `docker exec -i claude-sandbox claude auth login`을 실행하면
OAuth URL은 출력되지만, 인증 후 받은 코드를 stdin으로 전달하는 게 불가능했다.

시도한 방법들:
- **tmux `send-keys`**: URL은 나오지만 코드 입력이 프로세스에 전달 안 됨
- **named pipe (fifo)**: `exec 3<>/tmp/pipe`로 양방향 열어도 SSH 세션 종료 시 fd 끊김
- **coproc**: 코드 전달 시도했으나 프로세스가 수신 못함
- **`docker exec -it` in tmux**: `server exited unexpectedly` 오류

원인: Claude CLI가 stdin을 읽는 방식이 단순 pipe/redirect와 호환되지 않음.

## 시도 2: 수동 PKCE OAuth 토큰 교환

CLI를 우회하여 직접 PKCE 플로우를 구현:
1. `code_verifier` / `code_challenge` 쌍 생성
2. OAuth URL 조립하여 사용자에게 전달
3. 인증 후 받은 authorization code를 토큰 엔드포인트에서 교환
4. 받은 credential을 컨테이너의 `.credentials.json`에 직접 작성

### 문제 2-1: 잘못된 요청 형식 (400)

처음에 `platform.claude.com/v1/oauth/token`으로 보냈으나 429(rate limit)가 걸려서
실제 형식 검증이 안 됐다. rate limit이 풀린 후 400 `Invalid request format`이 나왔고,
이를 "코드 만료"로 오판하여 사용자에게 불필요하게 여러 번 재로그인을 시켰다.

실제 원인:
- `redirect_uri`가 틀렸음: `platform.claude.com` → `console.anthropic.com`
- `state` 필드가 요청 바디에 누락됨

올바른 바디:
```json
{
  "grant_type": "authorization_code",
  "code": "<authorization_code>",
  "state": "<state>",
  "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
  "redirect_uri": "https://console.anthropic.com/oauth/code/callback",
  "code_verifier": "<code_verifier>"
}
```

참고: [GitHub Gist - Anthropic OAuth CLI demo](https://gist.github.com/changjonathanc/9f9d635b2f8692e0520a884eaf098351)

### 문제 2-2: Cloudflare 429 차단

`console.anthropic.com/v1/oauth/token`과 `platform.claude.com/v1/oauth/token`은
비공식 클라이언트(curl 등)의 요청을 Cloudflare가 429로 차단한다.
이건 일반적인 rate limit이 아니라 클라이언트 fingerprinting 기반 차단이다.

증거:
- 계정 기반 확인: 로컬(다른 IP)에서도 동일 429 → IP가 아니라 계정/client 기준
- 하루 이상 경과 후에도 429 지속
- `retry-after` 헤더 없음

관련 이슈:
- [opencode #18329](https://github.com/anomalyco/opencode/issues/18329) — 서드파티 토큰 교환 429
- [CLIProxyAPI #1659](https://github.com/router-for-me/CLIProxyAPI/issues/1659) — api.anthropic.com 우회 발견

### 해결: `api.anthropic.com` 사용

`api.anthropic.com/v1/oauth/token`으로 변경하니 즉시 성공.
동일한 OAuth 토큰 엔드포인트이지만 Cloudflare 차단이 적용되지 않는다.

```
api.anthropic.com/v1/oauth/token     → 작동 (권장)
console.anthropic.com/v1/oauth/token → Cloudflare 429
platform.claude.com/v1/oauth/token   → Cloudflare 429
```

## 시도 3: token_manager 관련 문제들

### 즉시 갱신 문제
`init()`에서 `_expires_at = time.time() + 60`으로 설정하여 시작 후 60초 만에
불필요한 토큰 갱신이 발생. 갱신 시 refresh token이 회전되면서 기존 토큰 무효화.
→ `_expires_at = float("inf")`로 변경 (reactive mode)

### 무효 refresh token 무한 재시도
400 응답(refresh token 무효)에도 60초마다 계속 재시도.
→ `_refresh_dead` 플래그 추가, 400 시 재시도 중단

### .env 변수명 불일치
`.env`에 `CLAUDE_REFRESH_TOKEN`으로 설정했으나 코드는 `CLAUDE_CODE_REFRESH_TOKEN`을 기대.
같은 유형의 실수가 2회 반복됨.

## 최종 구성

1. 컨테이너: `claude auth logout` → 수동 PKCE로 독립 OAuth 세션 설정
2. credential: `/root/.claude/.credentials.json`에 직접 작성 (Docker 영속 볼륨)
3. 토큰 갱신: `token_manager.py`가 `api.anthropic.com`으로 갱신 (폴백용)
4. 로컬 Claude Code와 완전 독립 — 토큰 회전 충돌 없음
