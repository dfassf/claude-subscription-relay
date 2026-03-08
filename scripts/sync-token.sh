#!/bin/bash
# 로컬 macOS Keychain에서 Claude OAuth 토큰을 추출해 GCE VM에 동기화
set -euo pipefail

TOKEN=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null \
  | python3 -c "import sys,json; creds=json.loads(sys.stdin.read()); print(creds['claudeAiOauth']['accessToken'])")

if [ -z "$TOKEN" ]; then
  echo "[$(date)] 토큰 추출 실패" >&2
  exit 1
fi

gcloud compute ssh oci-script-runner \
  --zone=us-central1-a \
  --project=personal-projects-ss \
  --command="
cd ~/claude-subscription-relay && \
sed -i '/^CLAUDE_CODE_OAUTH_TOKEN=/d' .env && \
echo 'CLAUDE_CODE_OAUTH_TOKEN=$TOKEN' >> .env && \
sudo docker compose up -d --no-build
" 2>/dev/null

echo "[$(date)] 토큰 동기화 완료"
