import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from aggregator.clickhouse_jobs import ClickHouseAggregates
from aggregator.config import load_settings
from aggregator.pg_store import PostgresStore
from aggregator.service import AggregationService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app() -> FastAPI:
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5,
                                         server_settings={"timezone": "UTC"})
        clickhouse = await asyncio.to_thread(ClickHouseAggregates, settings)
        store = PostgresStore(pool)
        service = AggregationService(settings, clickhouse, store)
        app.state.pool, app.state.clickhouse, app.state.store, app.state.service = pool, clickhouse, store, service
        scheduler = asyncio.create_task(service.run_forever(), name="aggregation-scheduler")
        yield
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)
        await pool.close()

    app = FastAPI(title="Aggregation Service", lifespan=lifespan)

    @app.post("/recompute")
    async def recompute(request: Request, metric_date: date = Query(alias="date")):
        if metric_date > datetime.now(timezone.utc).date():
            raise HTTPException(400, "date is in the future")
        result = await request.app.state.service.recompute(metric_date)
        body = {
            "date": metric_date.isoformat(),
            "events_processed": result.events_processed,
            "rows_written": result.rows_written,
            "duration_seconds": round(result.duration_seconds, 3),
        }
        if result.failed_dates:
            return JSONResponse(status_code=503, content={**body, "error": "AGGREGATION_FAILED"})
        return body

    @app.get("/metrics")
    async def metrics(request: Request, metric_date: date = Query(alias="date")):
        pool: asyncpg.Pool = request.app.state.pool
        rows = await pool.fetch("SELECT metric_name, value, computed_at FROM daily_metrics "
                                "WHERE metric_date = $1 ORDER BY metric_name", metric_date)
        if not rows:
            raise HTTPException(404, "no aggregates for this date")
        movies = await pool.fetch("SELECT rank, movie_id, views, unique_viewers FROM top_movies "
                                  "WHERE metric_date = $1 ORDER BY rank", metric_date)
        cohort = await pool.fetch("SELECT day_offset, cohort_size, retained_users, retention_rate "
                                  "FROM retention_cohorts WHERE cohort_date = $1 ORDER BY day_offset", metric_date)
        return {
            "date": metric_date.isoformat(),
            "metrics": {r["metric_name"]: r["value"] for r in rows},
            "computed_at": max(r["computed_at"] for r in rows).isoformat(),
            "top_movies": [dict(r) for r in movies],
            "retention_cohort": [dict(r) for r in cohort],
        }

    @app.get("/health")
    async def health(request: Request):
        try:
            await request.app.state.store.ping()
            ok = await asyncio.to_thread(request.app.state.clickhouse.ping)
        except Exception as exc:
            return JSONResponse(status_code=503, content={"status": "unavailable", "error": repr(exc)})
        if not ok:
            return JSONResponse(status_code=503, content={"status": "unavailable", "error": "clickhouse ping failed"})
        return {"status": "ok"}

    return app
