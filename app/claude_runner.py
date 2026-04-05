import asyncio
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path

from app import token_manager
from app.config import settings

logger = logging.getLogger(__name__)

CONTAINER_NAME = "claude-sandbox"
INSTRUCTIONS_FILE = Path("context/instructions.md")
MEMORY_FILE = Path("context/memory.md")
MEMORY_TAG_RE = re.compile(r"<memory>(.*?)</memory>", re.DOTALL)


def _is_auth_error(msg: str) -> bool:
    m = msg.lower()
    return any(x in m for x in ("401", "unauthorized", "oauth", "expired", "authenticate"))


async def run_claude(
    prompt: str,
    *,
    system_prompt: str | None = None,
    output_format: str = "text",
    timeout: int | None = None,
    files: list[Path] | None = None,
    resume_session: str | None = None,
    workspace_dir: str | None = None,
) -> tuple[str, str | None]:
    """상주 컨테이너에 docker exec로 Claude CLI를 실행하고 응답을 반환한다."""

    timeout = timeout or settings.claude_timeout

    # instructions.md + memory.md를 system prompt로 조립
    base_prompt = ""
    if INSTRUCTIONS_FILE.exists():
        base_prompt = INSTRUCTIONS_FILE.read_text().strip()
        memory = MEMORY_FILE.read_text().strip() if MEMORY_FILE.exists() else "(없음)"
        base_prompt = base_prompt.replace("{{MEMORY}}", memory)
    if base_prompt and system_prompt:
        system_prompt = f"{base_prompt}\n\n---\n\n{system_prompt}"
    elif base_prompt:
        system_prompt = base_prompt

    use_temp = workspace_dir is None
    if use_temp:
        workspace = Path(tempfile.mkdtemp(dir=settings.workspace_base))
    else:
        workspace = Path(settings.workspace_base) / workspace_dir
        workspace.mkdir(parents=True, exist_ok=True)
    workspace_name = workspace.name

    try:
        if files:
            file_names = []
            for f in files:
                shutil.copy2(f, workspace / f.name)
                file_names.append(f"/workspace/{workspace_name}/{f.name}")
            prompt = f"다음 파일을 참고해서 답해줘: {', '.join(file_names)}\n\n{prompt}"

        prompt_file = workspace / ".prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        for attempt in range(2):
            token = await token_manager.get_token()

            cmd = ["docker", "exec", "-i"]
            if token:
                cmd.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={token}"])
            cmd.extend([
                "-w", f"/workspace/{workspace_name}",
                CONTAINER_NAME,
                "claude", "-p", "-",
                "--output-format", "json",
            ])

            if resume_session:
                cmd.extend(["--resume", resume_session])
            if system_prompt:
                cmd.extend(["--system-prompt", system_prompt])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()), timeout=timeout
            )

            if proc.returncode != 0:
                error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
                # JSON 출력에서도 auth error 체크
                try:
                    data = json.loads(stdout.decode().strip())
                    if data.get("is_error") and data.get("result"):
                        error_msg = data["result"]
                except (json.JSONDecodeError, TypeError):
                    pass

                if attempt == 0 and _is_auth_error(error_msg):
                    logger.warning("인증 오류 감지, 토큰 갱신 후 재시도: %s", error_msg[:100])
                    token_manager.force_expire()
                    continue
                raise RuntimeError(f"Claude 실행 실패: {error_msg}")

            raw = stdout.decode().strip()

            session_id = None
            result_text = raw
            try:
                data = json.loads(raw)
                session_id = data.get("session_id")
                result_text = data.get("result", raw)
            except (json.JSONDecodeError, TypeError):
                pass

            # <memory> 태그 파싱 → memory.md에 저장
            memories = MEMORY_TAG_RE.findall(result_text)
            if memories:
                with open(MEMORY_FILE, "a", encoding="utf-8") as f:
                    for mem in memories:
                        f.write(f"\n{mem.strip()}\n")
                logger.info("메모리 %d건 저장", len(memories))
                result_text = MEMORY_TAG_RE.sub("", result_text).strip()

            return result_text, session_id

    finally:
        if use_temp:
            shutil.rmtree(workspace, ignore_errors=True)


_login_proc: asyncio.subprocess.Process | None = None


async def run_login() -> str:
    """컨테이너 안에서 claude auth login을 실행하고 OAuth URL을 반환한다."""
    import re

    global _login_proc

    if _login_proc and _login_proc.returncode is None:
        _login_proc.kill()
        await _login_proc.communicate()

    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", CONTAINER_NAME,
        "claude", "auth", "login",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _login_proc = proc

    collected = ""
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
            if not line:
                break
            decoded = line.decode().strip()
            collected += decoded + "\n"
            match = re.search(r"(https://claude\.ai/oauth/authorize\S+)", decoded)
            if match:
                return match.group(1)
    except asyncio.TimeoutError:
        pass

    proc.kill()
    await proc.communicate()
    raise RuntimeError(f"OAuth URL을 찾을 수 없습니다: {collected}")


async def clear_workspace_sessions(workspace_dir: str):
    """컨테이너 안의 워크스페이스 세션 데이터를 모두 삭제한다."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", CONTAINER_NAME,
        "sh", "-c",
        "rm -rf /root/.claude/projects/*/sessions "
        f"/workspace/{workspace_dir}/.claude",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(proc.communicate(), timeout=10)


async def clear_all_sessions():
    """컨테이너의 모든 Claude 대화 세션을 삭제한다."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", CONTAINER_NAME,
        "sh", "-c",
        "rm -rf /root/.claude/projects/*/sessions /root/.claude/.sessions",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(proc.communicate(), timeout=10)
    if proc.returncode != 0:
        raise RuntimeError("세션 삭제 실패")


async def check_auth() -> dict:
    """컨테이너 안의 Claude 인증 상태를 확인한다."""
    token = await token_manager.get_token()

    cmd = ["docker", "exec"]
    if token:
        cmd.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={token}"])
    cmd.extend([CONTAINER_NAME, "claude", "auth", "status"])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)

    try:
        return json.loads(stdout.decode().strip())
    except json.JSONDecodeError:
        return {"loggedIn": False, "raw": stdout.decode().strip()}
