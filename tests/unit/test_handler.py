import json
import sys
from pathlib import Path

HANDLER_DIR = Path(__file__).parents[2] / "src" / "handler"
sys.path.insert(0, str(HANDLER_DIR))

from main import handler  # noqa: E402


def test_health_route(monkeypatch):
    monkeypatch.setenv("STAGE", "test")

    response = handler(
        {
            "requestContext": {
                "http": {"method": "GET", "path": "/health"}
            }
        },
        None,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"status": "ok", "stage": "test"}


def test_schedule_runs_job():
    result = handler({"source": "scheduler", "stage": "test"}, None)

    assert result["accepted"] is True
    assert result["source"] == "scheduler"
