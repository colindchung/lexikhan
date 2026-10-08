"""Scheduled-only email worker. Claims each day's send before contacting SES.

SES has no idempotent SendEmail API. Ambiguous sends are never automatically retried:
we prefer a missed reminder to duplicate emails, and record UNKNOWN for operators.
"""

import logging
import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from models import timestamp
from reminders import next_time
from repository import Repository

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def advance(repository, settings, now, attempt=None):
    user_id = settings["userId"]
    update = {
        "TableName": repository.table_name,
        "Key": repository.encode({"userId": user_id, "itemId": "REMINDER"}),
        "UpdateExpression": "SET nextReminderAt = :next",
        "ConditionExpression": "enabled = :yes AND #v = :v AND nextReminderAt = :old",
        "ExpressionAttributeNames": {"#v": "version"},
        "ExpressionAttributeValues": repository.encode(
            {
                ":next": next_time(now, settings["timezone"], settings["time"]),
                ":yes": True,
                ":v": settings["version"],
                ":old": settings["nextReminderAt"],
            }
        ),
    }
    writes = [{"Update": update}]
    if attempt:
        writes.extend(
            [
                {
                    "Put": {
                        "TableName": repository.table_name,
                        "Item": repository.encode(attempt),
                        "ConditionExpression": "attribute_not_exists(itemId)",
                    }
                },
                {
                    "ConditionCheck": {
                        "TableName": repository.table_name,
                        "Key": repository.encode(
                            {"userId": user_id, "itemId": "EMAIL_ACCESS"}
                        ),
                        "ConditionExpression": "approved = :yes AND email = :email",
                        "ExpressionAttributeValues": repository.encode(
                            {":yes": True, ":email": settings["email"]}
                        ),
                    }
                },
            ]
        )
    try:
        repository.client.transact_write_items(TransactItems=writes)
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "TransactionCanceledException" and any(
            r.get("Code") == "ConditionalCheckFailed"
            for r in error.response.get("CancellationReasons", [])
        ):
            return False
        raise


def process(repository, ses, settings, now, web_url):
    user_id = settings["userId"]
    if (
        settings.get("channel") != "email"
        or not settings.get("enabled")
        or settings.get("nextReminderAt", "z") > timestamp(now)
    ):
        return "skipped"
    if (
        now - datetime.fromisoformat(settings["nextReminderAt"])
    ).total_seconds() > 3600:
        advance(repository, settings, now)
        return "expired"
    access = repository.get(user_id, "EMAIL_ACCESS")
    if (
        not access
        or not access.get("approved")
        or access.get("email") != settings.get("email")
    ):
        advance(repository, settings, now)
        return "unapproved"
    day = now.astimezone(ZoneInfo(settings["timezone"])).date().isoformat()
    attempt_id = f"EMAIL#{day}"
    if repository.get(user_id, attempt_id):
        advance(repository, settings, now)
        return "duplicate"
    profile = repository.get(user_id, "PROFILE")
    goal = int((profile or {}).get("dailyGoal", 3))
    cards = repository.session(user_id, timestamp(now), goal, goal)[:goal]
    if not cards:
        advance(repository, settings, now)
        return "no_cards"
    try:
        ses.get_suppressed_destination(EmailAddress=settings["email"])
    except ClientError as error:
        if error.response["Error"]["Code"] != "NotFoundException":
            raise
    else:
        advance(repository, settings, now)
        return "suppressed"
    attempt = {
        "userId": user_id,
        "itemId": attempt_id,
        "recordType": "EMAIL_ATTEMPT",
        "status": "CLAIMED",
        "attemptedAt": timestamp(now),
        "cardCount": len(cards),
    }
    if not advance(repository, settings, now, attempt):
        return "duplicate"
    try:
        result = ses.send_email(
            FromEmailAddress=os.environ["REMINDER_FROM_EMAIL"],
            Destination={"ToAddresses": [settings["email"]]},
            Content={
                "Simple": {
                    "Subject": {
                        "Data": "Your Lexikhan practice is ready",
                        "Charset": "UTF-8",
                    },
                    "Body": {
                        "Text": {
                            "Data": (
                                f"Lexikhan: {len(cards)} cards ready, "
                                f"about {(len(cards) + 2) // 3} min.\n\n"
                                f"Start practicing: {web_url}/\n\n"
                                "To stop these daily emails, sign in and turn off "
                                f"reminders under Reminders: {web_url}/"
                            ),
                            "Charset": "UTF-8",
                        }
                    },
                }
            },
        )
        status, message_id = "ACCEPTED", result["MessageId"]
    except Exception:
        # Do not log provider exceptions, which can contain recipient email addresses.
        status, message_id = "UNKNOWN", ""
    repository.client.update_item(
        TableName=repository.table_name,
        Key=repository.encode({"userId": user_id, "itemId": attempt_id}),
        UpdateExpression="SET #s = :s, messageId = :id",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=repository.encode({":s": status, ":id": message_id}),
    )
    if status == "UNKNOWN":
        raise RuntimeError(
            "EMAIL provider acceptance unknown; automatic resend suppressed"
        )
    return "accepted"


def run(repository, ses, now, web_url, context=None):
    cursor = {}
    failed = False
    count = 0
    while True:
        page = repository.client.query(
            TableName=repository.table_name,
            IndexName="reminder-index",
            KeyConditionExpression="reminderGroup = :group AND nextReminderAt <= :now",
            ExpressionAttributeValues=repository.encode(
                {":group": "ENABLED", ":now": timestamp(now)}
            ),
            Limit=25,
            **cursor,
        )
        for raw in page["Items"]:
            if context and context.get_remaining_time_in_millis() < 10000:
                raise RuntimeError(
                    "Reminder batch nearing timeout; remaining users resume next tick"
                )
            indexed = repository.decode(raw)
            settings = repository.get(indexed["userId"], "REMINDER")
            if not settings:
                continue
            try:
                outcome = process(repository, ses, settings, now, web_url)
                logger.info("reminder outcome=%s", outcome)
                count += outcome == "accepted"
            except Exception:
                logger.error(
                    "Reminder failed; inspect EMAIL_ATTEMPT records; recipient omitted"
                )
                failed = True
        if "LastEvaluatedKey" not in page:
            break
        cursor = {"ExclusiveStartKey": page["LastEvaluatedKey"]}
    if failed:
        raise RuntimeError("One or more reminders failed")
    return {"accepted": count}


def handler(event, context):
    if event.get("source") != "lexikhan.reminders":
        raise ValueError("Scheduled invocation required")
    repository = Repository(boto3.client("dynamodb"), os.environ["HISTORY_TABLE_NAME"])
    # No SDK retries: a timeout can happen after SES accepts an email.
    ses = boto3.client(
        "sesv2",
        config=Config(
            retries={"total_max_attempts": 1}, connect_timeout=3, read_timeout=5
        ),
    )
    return run(repository, ses, datetime.now(UTC), os.environ["WEB_URL"], context)
