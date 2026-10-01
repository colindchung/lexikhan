"""Scheduled-only SMS worker. Claims each day's send before contacting SNS.

SNS has no idempotent Publish API. Ambiguous sends are never automatically retried:
we prefer a missed reminder to duplicate texts, and record UNKNOWN for operators.
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
                            {"userId": user_id, "itemId": "SMS_ACCESS"}
                        ),
                        "ConditionExpression": "approved = :yes AND phone = :phone",
                        "ExpressionAttributeValues": repository.encode(
                            {":yes": True, ":phone": settings["phone"]}
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


def process(repository, sns, settings, now, web_url):
    user_id = settings["userId"]
    if not settings.get("enabled") or settings.get("nextReminderAt", "z") > timestamp(
        now
    ):
        return "skipped"
    if (
        now - datetime.fromisoformat(settings["nextReminderAt"])
    ).total_seconds() > 3600:
        advance(repository, settings, now)
        return "expired"
    access = repository.get(user_id, "SMS_ACCESS")
    if (
        not access
        or not access.get("approved")
        or access.get("phone") != settings.get("phone")
    ):
        advance(repository, settings, now)
        return "unapproved"
    day = now.astimezone(ZoneInfo(settings["timezone"])).date().isoformat()
    attempt_id = f"SMS#{day}"
    if repository.get(user_id, attempt_id):
        advance(repository, settings, now)
        return "duplicate"
    profile = repository.get(user_id, "PROFILE")
    goal = int((profile or {}).get("dailyGoal", 3))
    cards = repository.session(user_id, timestamp(now), goal, goal)[:goal]
    if not cards:
        advance(repository, settings, now)
        return "no_cards"
    if sns.check_if_phone_number_is_opted_out(phoneNumber=settings["phone"])[
        "isOptedOut"
    ]:
        advance(repository, settings, now)
        return "opted_out"
    attempt = {
        "userId": user_id,
        "itemId": attempt_id,
        "recordType": "SMS_ATTEMPT",
        "status": "CLAIMED",
        "attemptedAt": timestamp(now),
        "cardCount": len(cards),
    }
    if not advance(repository, settings, now, attempt):
        return "duplicate"
    try:
        result = sns.publish(
            PhoneNumber=settings["phone"],
            Message=(
                f"Lexikhan: {len(cards)} cards ready, "
                f"about {(len(cards) + 2) // 3} min. "
                f"{web_url}/\nReply STOP to opt out."
            ),
            MessageAttributes={
                "AWS.SNS.SMS.SMSType": {
                    "DataType": "String",
                    "StringValue": "Transactional",
                }
            },
        )
        status, message_id = "ACCEPTED", result["MessageId"]
    except Exception:
        # Do not log provider exceptions, which can contain recipient phone numbers.
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
            "SMS provider acceptance unknown; automatic resend suppressed"
        )
    return "accepted"


def run(repository, sns, now, web_url, context=None):
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
                outcome = process(repository, sns, settings, now, web_url)
                logger.info("reminder outcome=%s", outcome)
                count += outcome == "accepted"
            except Exception:
                logger.error(
                    "Reminder failed; inspect SMS_ATTEMPT records; recipient omitted"
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
    # Disable SDK Publish retries: a timeout can occur after a provider accepts an SMS.
    sns = boto3.client(
        "sns",
        config=Config(
            retries={"total_max_attempts": 1}, connect_timeout=3, read_timeout=5
        ),
    )
    return run(repository, sns, datetime.now(UTC), os.environ["WEB_URL"], context)
