from datetime import UTC, datetime
from typing import Any


def run_job(*, source: str, event: dict[str, Any]) -> dict[str, Any]:
    """Run the application operation shared by API and scheduled invocations."""
    # Replace this stub with the application's idempotent business logic.
    return {
        "accepted": True,
        "source": source,
        "processedAt": datetime.now(UTC).isoformat(),
    }
