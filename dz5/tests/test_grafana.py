from datetime import datetime, time, timedelta, timezone

DATASOURCE = {"type": "grafana-clickhouse-datasource", "uid": "clickhouse"}


def dashboard(grafana) -> dict:
    response = grafana.get("/api/dashboards/uid/movie-analytics")
    assert response.status_code == 200, response.text
    return response.json()["dashboard"]


def test_clickhouse_datasource_is_healthy(grafana):
    body = grafana.get("/api/datasources/uid/clickhouse/health").json()
    assert body["status"] == "OK", body


def test_dashboard_panels_read_only_aggregates(grafana):
    panels = dashboard(grafana)["panels"]
    assert panels[0]["title"] == "Retention Cohort Heatmap"
    assert len(panels) >= 3
    for panel in panels:
        sql = panel["targets"][0]["rawSql"]
        assert "analytics.agg_" in sql, panel["title"]
        assert "movie_events" not in sql, panel["title"]


def test_cohort_heatmap_query(grafana, cohort_dataset):
    day = cohort_dataset["day"]
    panel = dashboard(grafana)["panels"][0]
    start = datetime.combine(day, time(), tzinfo=timezone.utc)
    body = {
        "from": str(int(start.timestamp() * 1000)),
        "to": str(int((start + timedelta(days=8)).timestamp() * 1000)),
        "queries": [{**panel["targets"][0], "datasource": DATASOURCE}],
    }
    response = grafana.post("/api/ds/query", json=body)
    assert response.status_code == 200, response.text
    frame = response.json()["results"]["A"]["frames"][0]
    names = [f["name"] for f in frame["schema"]["fields"]]
    rows = list(zip(*frame["data"]["values"]))
    cohort = dict(zip(names, next(r for r in rows if r[0] == day.isoformat())))
    assert cohort["Пользователей"] == 4
    assert [cohort[f"Day {k}"] for k in range(8)] == [100, 50, 0, 0, 0, 0, 0, 50]
