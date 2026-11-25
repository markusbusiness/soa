import json
from datetime import timedelta

from conftest import wait_for

BUCKET = "movie-analytics"


def key_for(day) -> str:
    return f"daily/{day.isoformat()}/aggregates.json"


def read_object(s3, day) -> dict:
    return json.loads(s3.get_object(Bucket=BUCKET, Key=key_for(day))["Body"].read())


def test_manual_export_writes_all_metrics(cohort_dataset, exporter, s3):
    day = cohort_dataset["day"]
    response = exporter.post("/export", params={"date": day.isoformat()})
    assert response.status_code == 200, response.text
    assert response.json()["object"] == f"s3://{BUCKET}/{key_for(day)}"

    document = read_object(s3, day)
    assert document["date"] == day.isoformat()
    assert document["computed_at"]
    metrics = {m["metric_name"]: m["value"] for m in document["metrics"]}
    assert metrics["dau"] == 5
    assert metrics["view_conversion"] == 0.75
    assert metrics["retention_d1"] == 0.5
    assert metrics["retention_d7"] == 0.5
    assert all(m["computed_at"] for m in document["metrics"])
    assert [m["movie_id"] for m in document["top_movies"]] == list(cohort_dataset["movies"])
    assert len(document["retention_cohort"]) == 8


def test_reexport_overwrites_same_object(cohort_dataset, exporter, s3):
    day = cohort_dataset["day"]
    first = exporter.post("/export", params={"date": day.isoformat()})
    assert first.status_code == 200
    exported_first = read_object(s3, day)["exported_at"]
    second = exporter.post("/export", params={"date": day.isoformat()})
    assert second.status_code == 200
    objects = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"daily/{day.isoformat()}/")["Contents"]
    assert [o["Key"] for o in objects] == [key_for(day)]
    assert read_object(s3, day)["exported_at"] > exported_first


def test_scheduled_export_covers_closed_days(cohort_dataset, postgres, s3):
    day = cohort_dataset["day"] + timedelta(days=7)
    row = wait_for(lambda: postgres.execute(
        "SELECT e.object_key FROM s3_exports e JOIN aggregate_dates a USING (metric_date) "
        "WHERE metric_date = %s AND e.source_updated_at = a.updated_at", (day,)).fetchone(),
        timeout=60, message="scheduled export of D+7")
    assert row[0] == key_for(day)
    assert read_object(s3, day)["date"] == day.isoformat()


def test_export_unknown_date_returns_404(exporter):
    assert exporter.post("/export", params={"date": "2000-01-01"}).status_code == 404
