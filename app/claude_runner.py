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
INTERNAL_OUTPUT_FORMAT = "json"
INSTRUCTIONS_FILE = Path("context/instructions.md")
MEMORY_FILE = Path("context/memory.md")
MEMORY_TAG_RE = re.compile(r"<memory>(.*?)</memory>", re.DOTALL)


def _is_auth_error(msg: str) -> bool:
    m = msg.lower()
    return any(x in m for x in ("401", "unauthorized", "oauth", "expired", "authenticate"))


def _load_system_prompt(system_prompt: str | None) -> str | None:
    if not INSTRUCTIONS_FILE.exists():
        return system_prompt

    base_prompt = INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()
    memory = MEMORY_FILE.read_text(encoding="utf-8").strip() if MEMORY_FILE.exists() else "(없음)"
    base_prompt = base_prompt.replace("{{MEMORY}}", memory)

    if not system_prompt:
        return base_prompt

    return f"{base_prompt}\n\n---\n\n{system_prompt}"


def _prepare_workspace(workspace_dir: str | None) -> tuple[Path, bool]:
    if workspace_dir is None:
        return Path(tempfile.mkdtemp(dir=settings.workspace_base)), True

    workspace = Path(settings.workspace_base) / workspace_dir
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace, False


def _copy_files_to_workspace(workspace: Path, files: list[Path] | None) -> list[str]:
    if not files:
        return []

    container_paths: list[str] = []
    workspace_name = workspace.name

    for file_path in files:
        dest = workspace / file_path.name
        shutil.copy2(file_path, dest)
        container_paths.append(f"/workspace/{workspace_name}/{dest.name}")

    return container_paths


def _build_prompt(prompt: str, container_paths: list[str]) -> str:
    if not container_paths:
        return prompt

    return f"다음 파일을 참고해서 답해줘: {', '.join(container_paths)}\n\n{prompt}"


def _build_claude_command(
    *,
    workspace_name: str,
    token: str | None,
    system_prompt: str | None,
    resume_session: str | None,
) -> list[str]:
    cmd = ["docker", "exec", "-i"]
    if token:
        cmd.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={token}"])
    cmd.extend([
        "-w",
        f"/workspace/{workspace_name}",
        CONTAINER_NAME,
        "claude",
        "-p",
        "-",
        "--output-format",
        INTERNAL_OUTPUT_FORMAT,
    ])

    if resume_session:
        cmd.extend(["--resume", resume_session])
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    return cmd


async def _run_subprocess(
    cmd: list[str],
    *,
    timeout: int,
    input_text: str | None = None,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(input=input_text.encode() if input_text is not None else None),
        timeout=timeout,
    )
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


def _parse_claude_response(raw: str) -> tuple[dict | None, str, str | None]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, raw, None

    if not isinstance(data, dict):
        return None, raw, None

    return data, data.get("result", raw), data.get("session_id")


def _extract_error_message(stdout: str, stderr: str, returncode: int) -> str:
    error_msg = stderr or f"exit code {returncode}"
    data, _, _ = _parse_claude_response(stdout)
    if data and data.get("is_error") and data.get("result"):
        return str(data["result"])
    return error_msg


def _store_memories(result_text: str) -> str:
    memories = MEMORY_TAG_RE.findall(result_text)
    if not memories:
        return result_text

    with open(MEMORY_FILE, "a", encoding="utf-8") as memory_file:
        for memory in memories:
            memory_file.write(f"\n{memory.strip()}\n")

    logger.info("메모리 %d건 저장", len(memories))
    return MEMORY_TAG_RE.sub("", result_text).strip()


def _serialize_result(
    *,
    parsed_data: dict | None,
    result_text: str,
    output_format: str,
) -> str:
    if output_format != "json" or not parsed_data:
        return result_text

    response = dict(parsed_data)
    response["result"] = result_text
    return json.dumps(response, ensure_ascii=False)


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
    system_prompt = _load_system_prompt(system_prompt)
    workspace, use_temp = _prepare_workspace(workspace_dir)
    workspace_name = workspace.name

    try:
        container_paths = _copy_files_to_workspace(workspace, files)
        prompt = _build_prompt(prompt, container_paths)

        for attempt in range(2):
            token = await token_manager.get_token()
            cmd = _build_claude_command(
                workspace_name=workspace_name,
                token=token,
                system_prompt=system_prompt,
                resume_session=resume_session,
            )
            returncode, stdout, stderr = await _run_subprocess(
                cmd,
                timeout=timeout,
                input_text=prompt,
            )

            if returncode != 0:
                error_msg = _extract_error_message(stdout, stderr, returncode)
                if attempt == 0 and _is_auth_error(error_msg):
                    logger.warning("인증 오류 감지, 토큰 갱신 후 재시도: %s", error_msg[:100])
                    token_manager.force_expire()
                    continue
                raise RuntimeError(f"Claude 실행 실패: {error_msg}")

            parsed_data, result_text, session_id = _parse_claude_response(stdout)
            result_text = _store_memories(result_text)
            return _serialize_result(
                parsed_data=parsed_data,
                result_text=result_text,
                output_format=output_format,
            ), session_id

    finally:
        if use_temp:
            shutil.rmtree(workspace, ignore_errors=True)


_login_proc: asyncio.subprocess.Process | None = None


async def run_login() -> str:
    """컨테이너 안에서 claude auth login을 실행하고 OAuth URL을 반환한다."""
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
    cmd = [
        "docker",
        "exec",
        CONTAINER_NAME,
        "sh",
        "-c",
        "rm -rf /root/.claude/projects/*/sessions "
        f"/workspace/{workspace_dir}/.claude",
    ]
    await _run_subprocess(
        cmd,
        timeout=10,
    )


async def clear_all_sessions():
    """컨테이너의 모든 Claude 대화 세션을 삭제한다."""
    cmd = [
        "docker",
        "exec",
        CONTAINER_NAME,
        "sh",
        "-c",
        "rm -rf /root/.claude/projects/*/sessions /root/.claude/.sessions",
    ]
    returncode, _, _ = await _run_subprocess(
        cmd,
        timeout=10,
    )
    if returncode != 0:
        raise RuntimeError("세션 삭제 실패")


async def check_auth() -> dict:
    """컨테이너 안의 Claude 인증 상태를 확인한다."""
    token = await token_manager.get_token()
    cmd = ["docker", "exec"]
    if token:
        cmd.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={token}"])
    cmd.extend([CONTAINER_NAME, "claude", "auth", "status"])
    _, stdout, _ = await _run_subprocess(cmd, timeout=10)

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"loggedIn": False, "raw": stdout}
