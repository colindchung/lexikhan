import json
import os
from typing import Any

from service import run_job


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Adapt HTTP API and EventBridge Scheduler invocations to application code."""
    http = event.get("requestContext", {}).get("http")

    if http:
        route = (http.get("method"), http.get("path"))
        if route == ("GET", "/health"):
            return _response(200, {"status": "ok", "stage": os.getenv("STAGE")})
        if route == ("POST", "/run"):
            return _response(202, run_job(source="api", event=event))
        return _response(404, {"message": "Not found"})

    if event.get("source") == "scheduler":
        return run_job(source="scheduler", event=event)

    raise ValueError("Unsupported event shape")
