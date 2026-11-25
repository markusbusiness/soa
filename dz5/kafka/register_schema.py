from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

REGISTRY = os.environ["SCHEMA_REGISTRY_URL"].rstrip("/")
SUBJECT = os.environ.get("SCHEMA_SUBJECT", "movie-events-value")
COMPATIBILITY = os.environ.get("SCHEMA_COMPATIBILITY", "BACKWARD")
SCHEMA_FILES = os.environ.get("SCHEMA_FILES", "/schemas/movie_event.v1.avsc").split(",")
CONTENT_TYPE = "application/vnd.schemaregistry.v1+json"


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{REGISTRY}{path}", data=data, method=method,
                                     headers={"Content-Type": CONTENT_TYPE})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def wait_ready() -> None:
    for attempt in range(60):
        try:
            call("GET", "/subjects")
            return
        except (urllib.error.URLError, ConnectionError) as exc:
            print(f"schema registry not ready ({exc}), retry {attempt + 1}", flush=True)
            time.sleep(2)
    sys.exit("schema registry is unavailable")


def subject_exists() -> bool:
    try:
        call("GET", f"/subjects/{SUBJECT}/versions")
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def main() -> None:
    wait_ready()
    call("PUT", f"/config/{SUBJECT}", {"compatibility": COMPATIBILITY})
    for path in SCHEMA_FILES:
        with open(path) as f:
            body = {"schemaType": "AVRO", "schema": json.dumps(json.load(f))}
        if subject_exists():
            check = call("POST", f"/compatibility/subjects/{SUBJECT}/versions/latest?verbose=true", body)
            if not check["is_compatible"]:
                sys.exit(f"{path} is not {COMPATIBILITY}-compatible: {check.get('messages')}")
        registered = call("POST", f"/subjects/{SUBJECT}/versions", body)
        found = call("POST", f"/subjects/{SUBJECT}", body)
        print(f"{path}: subject={SUBJECT} schema_id={registered['id']} version={found['version']} "
              f"compatibility={COMPATIBILITY}", flush=True)


if __name__ == "__main__":
    main()
