FROM node:20-slim

# Claude Code 설치
RUN npm install -g @anthropic-ai/claude-code

# 작업 디렉토리
RUN mkdir -p /workspace
WORKDIR /workspace

# credential 디렉토리
RUN mkdir -p /root/.claude

# 시작 시 .claude.json 자동 복원
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
