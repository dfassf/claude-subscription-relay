import asyncio
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile

from app import token_manager
from app.claude_runner import check_auth, clear_all_sessions, run_login
from app.config import settings
from app.queue_worker import Task, worker
from app.schemas import AskRequest, AskResponse, HealthResponse, TaskResult
from app.telegram_bot import get_bot


async def verify_api_key(request: Request):
    if not settings.api_key:
        return  # API 키 미설정 시 인증 스킵 (로컬 개발용)
    key = request.headers.get("X-API-Key")
    if key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.workspace_base).mkdir(parents=True, exist_ok=True)

    # OAuth 토큰 매니저 초기화 + 백그라운드 갱신
    token_manager.init(
        access_token=settings.claude_code_oauth_token,
        refresh_token=settings.claude_code_refresh_token,
    )
    refresh_task = asyncio.create_task(token_manager.refresh_loop())

    worker_task = asyncio.create_task(worker.start())
    cleanup_task = asyncio.create_task(worker.cleanup_loop())

    telegram_task = None
    tg_bot = get_bot()
    if tg_bot:
        telegram_task = asyncio.create_task(tg_bot.start())

    yield

    if telegram_task:
        telegram_task.cancel()
    refresh_task.cancel()
    worker_task.cancel()
    cleanup_task.cancel()


app = FastAPI(title="Claude Sandbox API", version="0.1.0", lifespan=lifespan)


@app.post("/login", dependencies=[Depends(verify_api_key)])
async def login():
    """OAuth 로그인 URL을 반환한다. 브라우저에서 열어 인증 완료."""
    try:
        url = await run_login()
        return {"oauth_url": url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth", dependencies=[Depends(verify_api_key)])
async def auth():
    """컨테이너의 Claude 인증 상태를 확인한다."""
    return await check_auth()


@app.post("/sessions/clear", dependencies=[Depends(verify_api_key)])
async def sessions_clear():
    """모든 대화 세션을 삭제하고 새 대화를 시작할 수 있게 한다."""
    try:
        await clear_all_sessions()
        return {"message": "모든 세션이 초기화되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ask", response_model=AskResponse, dependencies=[Depends(verify_api_key)])
async def ask(req: AskRequest):
    """텍스트 프롬프트를 큐에 넣고 task_id를 반환한다."""
    task = Task(
        prompt=req.prompt,
        system_prompt=req.system_prompt,
        output_format=req.output_format,
        timeout=req.timeout,
    )
    worker.enqueue(task)
    return AskResponse(task_id=task.task_id)


@app.post("/ask/file", response_model=AskResponse, dependencies=[Depends(verify_api_key)])
async def ask_with_file(
    prompt: str = Form(...),
    system_prompt: str | None = Form(None),
    output_format: str = Form("text"),
    timeout: int | None = Form(None),
    files: list[UploadFile] = File(...),
):
    """파일과 함께 프롬프트를 큐에 넣는다."""
    tmp_dir = Path(tempfile.mkdtemp())
    saved_paths: list[Path] = []
    try:
        for f in files:
            dest = tmp_dir / f.filename
            with open(dest, "wb") as out:
                content = await f.read()
                out.write(content)
            saved_paths.append(dest)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    task = Task(
        prompt=prompt,
        system_prompt=system_prompt,
        output_format=output_format,
        timeout=timeout,
        files=saved_paths,
    )
    worker.enqueue(task)
    return AskResponse(task_id=task.task_id)


@app.get("/task/{task_id}", response_model=TaskResult, dependencies=[Depends(verify_api_key)])
async def get_task(task_id: str):
    """작업 상태와 결과를 조회한다."""
    task = worker.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return TaskResult(
        task_id=task.task_id,
        status=task.status,
        result=task.result,
        error=task.error,
        duration=task.duration,
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        queue_size=worker.queue_size,
        current_task=worker.current_task_id,
    )
