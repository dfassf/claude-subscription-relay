import asyncio
import shutil
import tempfile
from pathlib import Path

from app.config import settings

CONTAINER_NAME = "claude-sandbox"


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
    token = settings.get_oauth_token()

    # 고정 workspace가 지정되면 사용, 아니면 임시 생성
    use_temp = workspace_dir is None
    if use_temp:
        workspace = Path(tempfile.mkdtemp(dir=settings.workspace_base))
    else:
        workspace = Path(settings.workspace_base) / workspace_dir
        workspace.mkdir(parents=True, exist_ok=True)
    workspace_name = workspace.name

    try:
        # 파일이 있으면 workspace에 복사하고 프롬프트에 경로 추가
        if files:
            file_names = []
            for f in files:
                shutil.copy2(f, workspace / f.name)
                file_names.append(f"/workspace/{workspace_name}/{f.name}")
            prompt = f"다음 파일을 참고해서 답해줘: {', '.join(file_names)}\n\n{prompt}"

        # 프롬프트를 파일로 저장 후 stdin으로 전달 (CLI 인자 노출 방지)
        prompt_file = workspace / ".prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        # docker exec 명령 조립
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
            raise RuntimeError(f"Claude 실행 실패: {error_msg}")

        raw = stdout.decode().strip()

        # JSON 출력에서 session_id와 결과 텍스트 추출
        import json as _json
        session_id = None
        result_text = raw
        try:
            data = _json.loads(raw)
            session_id = data.get("session_id")
            result_text = data.get("result", raw)
        except (_json.JSONDecodeError, TypeError):
            pass

        return result_text, session_id

    finally:
        if use_temp:
            shutil.rmtree(workspace, ignore_errors=True)


# 진행 중인 로그인 프로세스 (콜백 대기용)
_login_proc: asyncio.subprocess.Process | None = None


async def run_login() -> str:
    """컨테이너 안에서 claude auth login을 실행하고 OAuth URL을 반환한다.
    프로세스는 백그라운드에서 콜백을 대기하며, 브라우저 인증 완료 시 자연 종료된다."""
    import re

    global _login_proc

    # 이전 로그인 프로세스가 있으면 정리
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

    # stdout에서 한 줄씩 읽으면서 URL을 찾는다
    # URL 출력 후 프로세스는 콜백 대기 상태로 남아있음
    collected = ""
    try:
        while True:
            line = await asyncio.wait_for(
                proc.stdout.readline(), timeout=15
            )
            if not line:
                break
            decoded = line.decode().strip()
            collected += decoded + "\n"
            match = re.search(r"(https://claude\.ai/oauth/authorize\S+)", decoded)
            if match:
                # URL을 찾았으면 반환 (프로세스는 백그라운드에서 콜백 대기)
                return match.group(1)
    except asyncio.TimeoutError:
        pass

    # URL을 못 찾았으면 프로세스 정리
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


async def check_auth() -> dict:
    """컨테이너 안의 Claude 인증 상태를 확인한다."""
    token = settings.get_oauth_token()

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

    import json
    try:
        return json.loads(stdout.decode().strip())
    except json.JSONDecodeError:
        return {"loggedIn": False, "raw": stdout.decode().strip()}
