import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from recovery.recovery import DlqRecovery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dlq-recovery")


async def schedule(recovery: DlqRecovery, interval: float) -> None:
    while True:
        try:
            await asyncio.to_thread(recovery.run_once)
        except Exception:
            log.exception("dlq recovery run failed, retry next run")
        await asyncio.sleep(interval)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        recovery = await asyncio.to_thread(
            DlqRecovery, os.environ["CLICKHOUSE_HOST"], os.environ["CLICKHOUSE_USER"],
            os.environ["CLICKHOUSE_PASSWORD"], os.environ["SCHEMA_REGISTRY_URL"],
            int(os.getenv("DLQ_BATCH_SIZE", "5000")))
        app.state.recovery = recovery
        task = asyncio.create_task(schedule(recovery, float(os.getenv("DLQ_RECOVERY_INTERVAL_SECONDS", "30"))))
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="DLQ Recovery", lifespan=lifespan)

    @app.post("/run")
    async def run(request: Request):
        result = await asyncio.to_thread(request.app.state.recovery.run_once)
        return result.__dict__

    @app.get("/health")
    async def health(request: Request):
        try:
            ok = await asyncio.to_thread(request.app.state.recovery.ping)
        except Exception as exc:
            return JSONResponse(status_code=503, content={"status": "unavailable", "error": repr(exc)})
        return {"status": "ok"} if ok else JSONResponse(status_code=503, content={"status": "unavailable"})

    return app
