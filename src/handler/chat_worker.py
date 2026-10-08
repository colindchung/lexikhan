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


def generate(messages, language, key, on_text=None):
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
        "Answer directly without greetings, praise, motivational filler, "
        "summaries, or offers to help further. Do not use em dashes. "
        "Use concise Markdown: short paragraphs, bold key terms, "
        "and lists when useful. "
        "Avoid large headings, tables unless requested, and HTML. "
        "You cannot change settings or send reminders. No tools are available."
    )
    payload = json.dumps(
        {
            "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            "instructions": instructions,
            "input": messages,
            "max_output_tokens": 1000,
            "store": False,
            "stream": True,
        }
    ).encode()
    request = Request(
        "https://api.openai.com/v1/responses",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    text, completed = "", False
    started = time.monotonic()
    with urlopen(request, timeout=45) as response:
        # SSE events are UTF-8 JSON data lines, delimited by a blank line.
        data_lines = []
        for raw in response:
            if time.monotonic() - started > 60:
                raise TimeoutError("Stream exceeded deadline")
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif not line and data_lines:
                data = "\n".join(data_lines)
                data_lines = []
                if data == "[DONE]":
                    break
                event = json.loads(data)
                kind = event.get("type")
                if kind in {"response.output_text.delta", "response.refusal.delta"}:
                    text += event.get("delta", "").replace("\u2014", ",")
                    if len(text) > 10000:
                        raise ValueError("Stream exceeded length limit")
                    if on_text:
                        on_text(text)
                elif kind in {"response.completed", "response.incomplete"}:
                    if kind == "response.incomplete":
                        text += (
                            "\n\n*This reply reached its length limit. "
                            "Ask a follow-up to continue.*"
                        )
                    completed = True
                    break
                elif kind in {"error", "response.failed"}:
                    raise ValueError("Provider stream failed")
    if not completed or not text.strip():
        raise ValueError("Provider stream ended before completion")
    return text.strip()


def save_progress(repository, user_id, turn_key, text):
    # A delayed stream must never revive an expired or completed turn.
    repository.client.update_item(
        TableName=repository.table_name,
        Key=repository.encode({"userId": user_id, "itemId": turn_key}),
        UpdateExpression="SET answer = :text",
        ConditionExpression="#s = :generating",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=repository.encode(
            {":text": text, ":generating": "GENERATING"}
        ),
    )


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
    partial, last_write = "", 0.0

    def progress(text):
        nonlocal partial, last_write
        partial = text
        now_tick = time.monotonic()
        if now_tick - last_write >= 0.5:
            save_progress(repository, user_id, turn_key, text)
            last_write = now_tick

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
        if responder:
            answer = responder(messages, language, key)
        else:
            answer = generate(messages, language, key, on_text=progress)
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
            answer=answer or partial,
            error=error_message,
        )
    except Conflict:
        return {"status": "expired"}
    return {"status": "failed" if error_message else "complete"}


def handler(event, context):
    if event.get("source") != "lexikhan.chat":
        raise ValueError("Internal chat invocation required")
    repository = Repository(boto3.client("dynamodb"), os.environ["HISTORY_TABLE_NAME"])
    return process(repository, event, datetime.now(UTC))
