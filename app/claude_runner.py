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
) -> str:
    """상주 컨테이너에 docker exec로 Claude CLI를 실행하고 응답을 반환한다."""

    timeout = timeout or settings.claude_timeout
    token = settings.get_oauth_token()

    # 요청별 임시 workspace 생성 (호스트의 workspace/ 아래)
    workspace = Path(tempfile.mkdtemp(dir=settings.workspace_base))
    workspace_name = workspace.name

    try:
        # 파일이 있으면 workspace에 복사하고 프롬프트에 경로 추가
        if files:
            file_names = []
            for f in files:
                shutil.copy2(f, workspace / f.name)
                file_names.append(f"/workspace/{workspace_name}/{f.name}")
            prompt = f"다음 파일을 참고해서 답해줘: {', '.join(file_names)}\n\n{prompt}"

        # docker exec 명령 조립
        cmd = ["docker", "exec"]
        if token:
            cmd.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={token}"])
        cmd.extend([
            "-w", f"/workspace/{workspace_name}",
            CONTAINER_NAME,
            "claude", "-p", prompt,
            "--output-format", output_format,
        ])

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
            raise RuntimeError(f"Claude 실행 실패: {error_msg}")

        return stdout.decode().strip()

    finally:
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
