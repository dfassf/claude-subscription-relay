#!/bin/bash
set -e

# macOS Keychain에서 Claude OAuth 토큰 추출
CREDS_JSON=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null)

if [ -z "$CREDS_JSON" ]; then
    echo "Claude 인증 정보를 찾을 수 없습니다. 먼저 'claude login'을 실행하세요."
    exit 1
fi

# JSON에서 accessToken만 추출
ACCESS_TOKEN=$(echo "$CREDS_JSON" | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['claudeAiOauth']['accessToken'])")

if [ -z "$ACCESS_TOKEN" ]; then
    echo "accessToken을 추출할 수 없습니다."
    exit 1
fi

# 프롬프트를 인자로 받음
PROMPT="${1:-안녕하세요}"

echo "Docker 컨테이너 안에서 Claude 실행 중..."
echo "프롬프트: $PROMPT"
echo "---"

docker run --rm \
    -e CLAUDE_CODE_OAUTH_TOKEN="$ACCESS_TOKEN" \
    -v "$(pwd)/workspace:/workspace" \
    claude-sandbox \
    -p "$PROMPT"
