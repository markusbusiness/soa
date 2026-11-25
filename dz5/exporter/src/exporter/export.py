import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import asyncpg
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

log = logging.getLogger("exporter")

PENDING_SQL = """
SELECT a.metric_date
FROM aggregate_dates a
LEFT JOIN s3_exports e USING (metric_date)
WHERE a.metric_date < $1
  AND (e.metric_date IS NULL OR e.source_updated_at < a.updated_at)
ORDER BY a.metric_date
"""


class NoAggregates(Exception):
    pass


@dataclass(frozen=True)
class ExportResult:
    metric_date: date
    object_key: str
    size_bytes: int


class S3Bucket:
    def __init__(self, endpoint_url: str, access_key: str, secret_key: str, bucket: str):
        self.bucket = bucket
        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url, aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}, connect_timeout=3, read_timeout=10,
                          retries={"max_attempts": 3, "mode": "standard"}),
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket"}:
                raise
            self._client.create_bucket(Bucket=self.bucket)
            log.info("bucket created: %s", self.bucket)

    def put_json(self, key: str, body: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType="application/json")


class Exporter:
    def __init__(self, pool: asyncpg.Pool, s3: S3Bucket, prefix: str):
        self._pool = pool
        self._s3 = s3
        self._prefix = prefix.strip("/")
        self._bucket_ready = False
        self._lock = asyncio.Lock()

    def object_key(self, metric_date: date) -> str:
        return f"{self._prefix}/{metric_date.isoformat()}/aggregates.json"

    async def _snapshot(self, metric_date: date) -> tuple[dict, datetime]:
        async with self._pool.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
            header = await conn.fetchrow("SELECT computed_at, updated_at FROM aggregate_dates WHERE metric_date = $1",
                                         metric_date)
            if header is None:
                raise NoAggregates(metric_date)
            metrics = await conn.fetch("SELECT metric_name, value, computed_at FROM daily_metrics "
                                       "WHERE metric_date = $1 ORDER BY metric_name", metric_date)
            movies = await conn.fetch("SELECT rank, movie_id, views, unique_viewers, finished, likes, computed_at "
                                      "FROM top_movies WHERE metric_date = $1 ORDER BY rank", metric_date)
            devices = await conn.fetch("SELECT device_type, users, views, computed_at FROM device_daily "
                                       "WHERE metric_date = $1 ORDER BY device_type", metric_date)
            cohort = await conn.fetch("SELECT day_offset, cohort_size, retained_users, retention_rate, computed_at "
                                      "FROM retention_cohorts WHERE cohort_date = $1 ORDER BY day_offset",
                                      metric_date)
        document = {
            "date": metric_date.isoformat(),
            "computed_at": header["computed_at"].isoformat(),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "metrics": [_row(r) for r in metrics],
            "top_movies": [_row(r) for r in movies],
            "devices": [_row(r) for r in devices],
            "retention_cohort": [_row(r) for r in cohort],
        }
        return document, header["updated_at"]

    async def export_date(self, metric_date: date) -> ExportResult:
        async with self._lock:
            if not self._bucket_ready:
                await asyncio.to_thread(self._s3.ensure_bucket)
                self._bucket_ready = True
            document, source_updated_at = await self._snapshot(metric_date)
            key = self.object_key(metric_date)
            body = json.dumps(document, ensure_ascii=False, indent=2).encode()
            await asyncio.to_thread(self._s3.put_json, key, body)
            await self._pool.execute(
                "INSERT INTO s3_exports (metric_date, object_key, source_updated_at, exported_at) "
                "VALUES ($1, $2, $3, now()) ON CONFLICT (metric_date) DO UPDATE "
                "SET object_key = EXCLUDED.object_key, source_updated_at = EXCLUDED.source_updated_at, "
                "exported_at = EXCLUDED.exported_at",
                metric_date, key, source_updated_at)
            log.info("exported date=%s to s3://%s/%s bytes=%d metrics=%d",
                     metric_date, self._s3.bucket, key, len(body), len(document["metrics"]))
            return ExportResult(metric_date, key, len(body))

    async def export_pending(self) -> None:
        today = datetime.now(timezone.utc).date()
        try:
            dates = await self._pool.fetch(PENDING_SQL, today)
        except Exception:
            log.exception("export run failed: cannot read pending dates from PostgreSQL, retry next run")
            return
        if not dates:
            log.info("export run: nothing to export")
            return
        log.info("export run started dates=%d", len(dates))
        exported = 0
        for row in dates:
            try:
                await self.export_date(row["metric_date"])
                exported += 1
            except Exception:
                log.exception("export failed date=%s, retry next run", row["metric_date"])
        log.info("export run finished exported=%d failed=%d", exported, len(dates) - exported)


def _row(record: asyncpg.Record) -> dict:
    return {k: v.isoformat() if isinstance(v, (datetime, date)) else v for k, v in record.items()}
