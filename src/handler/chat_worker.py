"""Generate one durably claimed reply; provider requests are never auto-retried."""

import json
import logging
import os
import time
from datetime import UTC, datetime
from urllib.request import Request, urlopen

import boto3
from botocore.exceptions import ClientError
from chats import approved, finish, query
from repository import Conflict, Repository

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_cached_key = None
_key_until = 0


def api_key():
    global _cached_key, _key_until
    if _cached_key and time.monotonic() < _key_until:
        return _cached_key
    value = boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ["OPENAI_SECRET_ARN"]
    )["SecretString"]
    key = json.loads(value).get("api_key", "")
    if not isinstance(key, str) or not key.startswith("sk-"):
        raise ValueError("Chat key not configured")
    _cached_key, _key_until = key, time.monotonic() + 300
    return key


def generate(messages, language, key):
    instructions = (
        "You are Lexikhan's language helper. Explain definitions, everyday vocabulary, "
        "casual phrases, grammar, pronunciation, register and cultural usage. "
        f"The learner's default language is {language}. "
        "Explain in English unless asked "
        "otherwise. For every Urdu word or phrase, include Urdu script, "
        "clear Roman Urdu "
        "pronunciation and English meaning. Give a natural usage example. "
        "Distinguish literal/idiomatic meanings and informal/polite wording. "
        "Ask for context if ambiguous. Be concise and candid about uncertainty. "
        "Use readable plain text with short paragraphs, not Markdown tables or HTML. "
        "You cannot change settings or send reminders. No tools are available."
    )
    payload = json.dumps(
        {
            "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            "instructions": instructions,
            "input": messages,
            "max_output_tokens": 1000,
            "store": False,
        }
    ).encode()
    request = Request(
        "https://api.openai.com/v1/responses",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=45) as response:
        data = json.loads(response.read(1_000_000))
    text = "\n".join(
        part.get("text", "")
        if part.get("type") == "output_text"
        else part.get("refusal", "")
        for item in data.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") in {"output_text", "refusal"}
    ).strip()
    if not text or data.get("status") not in {"completed", "incomplete"}:
        raise ValueError("No usable response")
    if data.get("status") == "incomplete":
        text += "\n\nThis reply reached its length limit. Ask a follow-up to continue."
    return text[:10000]


def process(repository, event, now, responder=None, key_loader=None):
    user_id, chat_id, message_id = event["userId"], event["chatId"], event["messageId"]
    turn_key = f"TURN#{chat_id}#{message_id}"
    turn = repository.get(user_id, turn_key)
    if not turn or turn["status"] != "QUEUED":
        return {"status": "skipped"}
    if int(turn["expiresAt"]) < int(now.timestamp()):
        try:
            finish(
                repository,
                user_id,
                chat_id,
                turn,
                now,
                error="This reply timed out. Please send your question again.",
            )
        except Conflict:
            pass
        return {"status": "expired"}
    try:
        repository.client.update_item(
            TableName=repository.table_name,
            Key=repository.encode({"userId": user_id, "itemId": turn_key}),
            UpdateExpression="SET #s = :generating",
            ConditionExpression="#s = :queued",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=repository.encode(
                {":queued": "QUEUED", ":generating": "GENERATING"}
            ),
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"status": "skipped"}
        raise
    answer, error_message = None, None
    try:
        if not approved(repository, user_id):
            raise ValueError("Access revoked")
        key = (key_loader or api_key)()
        previous = sorted(
            [
                t
                for t in query(repository, user_id, f"TURN#{chat_id}#")
                if t["status"] == "COMPLETE" and t["sequence"] < turn["sequence"]
            ],
            key=lambda t: t["sequence"],
        )[-8:]
        messages = []
        for past in previous:
            messages.extend(
                [
                    {"role": "user", "content": past["text"]},
                    {"role": "assistant", "content": past["answer"]},
                ]
            )
        messages.append({"role": "user", "content": turn["text"]})
        profile = repository.get(user_id, "PROFILE") or {}
        language = {"ur": "Urdu", "es": "Spanish"}.get(
            profile.get("learningLanguage"), "Urdu"
        )
        answer = (responder or generate)(messages, language, key)
    except Exception:
        # Neither keys nor questions, provider errors, or replies belong in logs.
        logger.warning("Chat response unavailable; message content omitted")
        error_message = "Chat couldn’t reply. Please try again in a moment."
    try:
        finish(
            repository,
            user_id,
            chat_id,
            turn,
            datetime.now(UTC),
            answer=answer,
            error=error_message,
        )
    except Conflict:
        return {"status": "expired"}
    return {"status": "complete" if answer else "failed"}


def handler(event, context):
    if event.get("source") != "lexikhan.chat":
        raise ValueError("Internal chat invocation required")
    repository = Repository(boto3.client("dynamodb"), os.environ["HISTORY_TABLE_NAME"])
    return process(repository, event, datetime.now(UTC))
