#!/bin/sh
# .claude.json이 없으면 최신 백업에서 복원
if [ ! -f /root/.claude.json ] && [ -d /root/.claude/backups ]; then
    LATEST=$(ls -t /root/.claude/backups/.claude.json.backup.* 2>/dev/null | head -1)
    if [ -n "$LATEST" ]; then
        cp "$LATEST" /root/.claude.json
        echo "Restored .claude.json from backup: $LATEST"
    fi
fi

exec "$@"
