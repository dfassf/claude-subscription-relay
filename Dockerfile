FROM node:20-slim

# Claude Code 설치
RUN npm install -g @anthropic-ai/claude-code

# 작업 디렉토리
RUN mkdir -p /workspace
WORKDIR /workspace

# credential 디렉토리
RUN mkdir -p /root/.claude

# 상주 컨테이너로 유지 (sleep infinity)
CMD ["sleep", "infinity"]
