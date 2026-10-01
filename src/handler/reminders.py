"""Account-scoped reminder preferences and timezone-aware daily scheduling."""

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from botocore.exceptions import ClientError
from models import timestamp
from repository import Conflict, NotFound


def next_time(now, zone, clock):
    tz = ZoneInfo(zone)
    hour, minute = map(int, clock.split(":"))
    local = now.astimezone(tz)
    for offset in range(3):
        day = local.date() + timedelta(days=offset)
        # First occurrence during fall-back; spring gaps shift forward by the gap.
        candidate = datetime.combine(day, time(hour, minute), tzinfo=tz)
        candidate = candidate.astimezone(UTC)
        if candidate > now:
            return timestamp(candidate)
    raise ValueError("Unable to schedule reminder")


def public_settings(repository, user_id):
    settings = repository.get(user_id, "REMINDER")
    profile = repository.get(user_id, "PROFILE")
    access = repository.get(user_id, "SMS_ACCESS")
    return {
        "enabled": bool(settings and settings["enabled"]),
        "phone": access["phone"] if access and access.get("approved") else None,
        "available": bool(access and access.get("approved")),
        "time": settings["time"] if settings else "18:00",
        "timezone": settings["timezone"]
        if settings
        else (profile or {}).get("timezone", "UTC"),
        "version": int(settings["version"]) if settings else 0,
        "nextReminderAt": settings.get("nextReminderAt") if settings else None,
    }


def save_settings(repository, user_id, request, now):
    if not isinstance(request, dict) or set(request) != {
        "enabled",
        "time",
        "timezone",
        "version",
    }:
        raise ValueError("Expected enabled, time, timezone, and version")
    if (
        type(request["enabled"]) is not bool
        or type(request["version"]) is not int
        or request["version"] < 0
    ):
        raise ValueError("Invalid reminder settings")
    if not isinstance(request["time"], str) or not re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", request["time"]
    ):
        raise ValueError("Choose a time in HH:MM format")
    if not isinstance(request["timezone"], str):
        raise ValueError("Choose an IANA timezone")
    try:
        ZoneInfo(request["timezone"])
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Choose an IANA timezone") from None
    if not repository.get(user_id, "PROFILE"):
        raise NotFound("Complete onboarding first")
    access = repository.get(user_id, "SMS_ACCESS")
    if request["enabled"] and not (access and access.get("approved")):
        raise ValueError(
            "A verified SMS number must be approved for this account first"
        )
    current = repository.get(user_id, "REMINDER")
    # A lost HTTP response may be retried with the same previous version.
    if (
        current
        and int(current["version"]) == request["version"] + 1
        and all(current[k] == request[k] for k in ("enabled", "time", "timezone"))
    ):
        return public_settings(repository, user_id)
    item = {
        "userId": user_id,
        "itemId": "REMINDER",
        "recordType": "REMINDER",
        **request,
        "version": request["version"] + 1,
        "updatedAt": timestamp(now),
    }
    if request["enabled"]:
        item.update(
            phone=access["phone"],
            consentAt=timestamp(now),
            reminderGroup="ENABLED",
            nextReminderAt=next_time(now, request["timezone"], request["time"]),
        )
    put = {
        "TableName": repository.table_name,
        "Item": repository.encode(item),
        "ConditionExpression": "attribute_not_exists(itemId)"
        if request["version"] == 0
        else "#v = :v",
    }
    if request["version"]:
        put.update(
            ExpressionAttributeNames={"#v": "version"},
            ExpressionAttributeValues=repository.encode({":v": request["version"]}),
        )
    writes = [{"Put": put}]
    if request["enabled"]:
        writes.append(
            {
                "ConditionCheck": {
                    "TableName": repository.table_name,
                    "Key": repository.encode(
                        {"userId": user_id, "itemId": "SMS_ACCESS"}
                    ),
                    "ConditionExpression": "approved = :yes AND phone = :phone",
                    "ExpressionAttributeValues": repository.encode(
                        {":yes": True, ":phone": access["phone"]}
                    ),
                }
            }
        )
    try:
        repository.client.transact_write_items(TransactItems=writes)
    except ClientError as error:
        if error.response["Error"]["Code"] == "TransactionCanceledException" and any(
            r.get("Code") == "ConditionalCheckFailed"
            for r in error.response.get("CancellationReasons", [])
        ):
            raise Conflict("Reminder settings changed; reload and try again") from error
        raise
    return public_settings(repository, user_id)
