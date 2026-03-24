#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Claude Docker Sandbox 초기 설정 ==="
echo

# 1. .env 파일 생성
if [ -f .env ]; then
    echo "[.env] 이미 존재합니다. 건너뜁니다."
else
    cp .env.example .env
    echo "[.env] .env.example에서 복사했습니다."
fi

# 2. API_KEY 자동 생성
if grep -q "^API_KEY=$" .env 2>/dev/null; then
    API_KEY=$(openssl rand -hex 16)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/^API_KEY=$/API_KEY=${API_KEY}/" .env
    else
        sed -i "s/^API_KEY=$/API_KEY=${API_KEY}/" .env
    fi
    echo "[API_KEY] 자동 생성: $API_KEY"
fi

# 3. OAuth 토큰 설정
echo
echo "Claude OAuth 토큰 설정:"
echo "  1) macOS Keychain에서 자동 추출"
echo "  2) 직접 입력"
echo "  3) 나중에 /login으로 설정"
read -rp "선택 [1/2/3]: " TOKEN_CHOICE

case "$TOKEN_CHOICE" in
    1)
        if [[ "$OSTYPE" != "darwin"* ]]; then
            echo "macOS에서만 사용 가능합니다."
            exit 1
        fi
        RAW=$(security find-generic-password -s "claude-code-credentials" -w 2>/dev/null || true)
        if [ -z "$RAW" ]; then
            echo "Keychain에 claude-code-credentials가 없습니다."
            echo "먼저 claude auth login을 실행하세요."
            exit 1
        fi
        ACCESS=$(echo "$RAW" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('accessToken',''))")
        REFRESH=$(echo "$RAW" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('refreshToken',''))")

        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^CLAUDE_CODE_OAUTH_TOKEN=.*|CLAUDE_CODE_OAUTH_TOKEN=${ACCESS}|" .env
            sed -i '' "s|^CLAUDE_CODE_REFRESH_TOKEN=.*|CLAUDE_CODE_REFRESH_TOKEN=${REFRESH}|" .env
        else
            sed -i "s|^CLAUDE_CODE_OAUTH_TOKEN=.*|CLAUDE_CODE_OAUTH_TOKEN=${ACCESS}|" .env
            sed -i "s|^CLAUDE_CODE_REFRESH_TOKEN=.*|CLAUDE_CODE_REFRESH_TOKEN=${REFRESH}|" .env
        fi
        echo "Keychain에서 토큰을 추출하여 .env에 저장했습니다."
        ;;
    2)
        read -rp "CLAUDE_CODE_OAUTH_TOKEN: " USER_TOKEN
        read -rp "CLAUDE_CODE_REFRESH_TOKEN (없으면 Enter): " USER_REFRESH
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^CLAUDE_CODE_OAUTH_TOKEN=.*|CLAUDE_CODE_OAUTH_TOKEN=${USER_TOKEN}|" .env
            [ -n "$USER_REFRESH" ] && sed -i '' "s|^CLAUDE_CODE_REFRESH_TOKEN=.*|CLAUDE_CODE_REFRESH_TOKEN=${USER_REFRESH}|" .env
        else
            sed -i "s|^CLAUDE_CODE_OAUTH_TOKEN=.*|CLAUDE_CODE_OAUTH_TOKEN=${USER_TOKEN}|" .env
            [ -n "$USER_REFRESH" ] && sed -i "s|^CLAUDE_CODE_REFRESH_TOKEN=.*|CLAUDE_CODE_REFRESH_TOKEN=${USER_REFRESH}|" .env
        fi
        echo "토큰을 .env에 저장했습니다."
        ;;
    3)
        echo "나중에 POST /login으로 인증하세요."
        ;;
    *)
        echo "잘못된 선택입니다."
        exit 1
        ;;
esac

# 4. Telegram 설정 (선택)
echo
read -rp "Telegram 봇을 설정하시겠습니까? [y/N]: " TG_CHOICE
if [[ "$TG_CHOICE" =~ ^[Yy]$ ]]; then
    read -rp "TELEGRAM_BOT_TOKEN: " TG_TOKEN
    read -rp "TELEGRAM_CHAT_ID: " TG_CHAT
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${TG_TOKEN}|" .env
        sed -i '' "s|^TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=${TG_CHAT}|" .env
    else
        sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${TG_TOKEN}|" .env
        sed -i "s|^TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=${TG_CHAT}|" .env
    fi
    echo "Telegram 설정을 .env에 저장했습니다."
fi

# 5. tokens 디렉토리 생성
mkdir -p tokens
echo
echo "[tokens/] 디렉토리 생성 완료 (OAuth 토큰 영속 저장용)"

# 6. Docker build & up
echo
echo "Docker 컨테이너 빌드 및 실행..."
docker compose build
docker compose up -d

echo
echo "=== 설정 완료 ==="
echo "API: http://localhost:8000"
echo "Health: http://localhost:8000/health"
[ -n "${API_KEY:-}" ] && echo "API Key: $API_KEY"
