import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from exporter.export import Exporter, NoAggregates, S3Bucket

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("exporter")


async def schedule(exporter: Exporter, interval: float) -> None:
    while True:
        await exporter.export_pending()
        await asyncio.sleep(interval)


def create_app() -> FastAPI:
    interval = float(os.getenv("EXPORT_INTERVAL_SECONDS", "300"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3,
                                         server_settings={"timezone": "UTC"})
        s3 = S3Bucket(os.environ["S3_ENDPOINT_URL"], os.environ["S3_ACCESS_KEY"], os.environ["S3_SECRET_KEY"],
                      os.getenv("S3_BUCKET", "movie-analytics"))
        exporter = Exporter(pool, s3, os.getenv("S3_PREFIX", "daily"))
        app.state.pool, app.state.s3, app.state.exporter = pool, s3, exporter
        task = asyncio.create_task(schedule(exporter, interval), name="export-scheduler")
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await pool.close()

    app = FastAPI(title="S3 Export Service", lifespan=lifespan)

    @app.post("/export")
    async def export(request: Request, metric_date: date = Query(alias="date")):
        try:
            result = await request.app.state.exporter.export_date(metric_date)
        except NoAggregates:
            raise HTTPException(404, "no aggregates for this date")
        except Exception as exc:
            log.exception("manual export failed date=%s", metric_date)
            return JSONResponse(status_code=503, content={"error": "EXPORT_FAILED", "details": repr(exc)})
        return {"date": metric_date.isoformat(), "object": f"s3://{request.app.state.s3.bucket}/{result.object_key}",
                "size_bytes": result.size_bytes}

    @app.get("/health")
    async def health(request: Request):
        try:
            await request.app.state.pool.fetchval("SELECT 1")
        except Exception as exc:
            return JSONResponse(status_code=503, content={"status": "unavailable", "error": repr(exc)})
        return {"status": "ok"}

    return app
