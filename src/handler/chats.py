"""Account-scoped durable conversations and atomically claimed model turns."""

import os
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from models import timestamp
from repository import Conflict, NotFound

MAX_TURNS = 60
DAILY_LIMIT = 50


def uuid(value):
    if not isinstance(value, str):
        raise ValueError("Expected a UUID")
    return str(UUID(value))


def query(repository, user_id, prefix):
    items, cursor = [], {}
    while True:
        page = repository.client.query(
            TableName=repository.table_name,
            ConsistentRead=True,
            KeyConditionExpression="userId = :u AND begins_with(itemId, :p)",
            ExpressionAttributeValues=repository.encode({":u": user_id, ":p": prefix}),
            **cursor,
        )
        items.extend(repository.decode(raw) for raw in page["Items"])
        if "LastEvaluatedKey" not in page:
            return items
        cursor = {"ExclusiveStartKey": page["LastEvaluatedKey"]}


def transaction(repository, writes):
    try:
        repository.client.transact_write_items(TransactItems=writes)
    except ClientError as error:
        if error.response["Error"]["Code"] == "TransactionCanceledException" and any(
            r.get("Code") == "ConditionalCheckFailed"
            for r in error.response.get("CancellationReasons", [])
        ):
            raise Conflict(
                "Chat changed or its limit was reached. Reload and try again."
            ) from error
        raise


def put(repository, item, **kwargs):
    return {
        "Put": {
            "TableName": repository.table_name,
            "Item": repository.encode(item),
            **kwargs,
        }
    }


def update(repository, user_id, item_id, expression, condition, values, names=None):
    result = {
        "TableName": repository.table_name,
        "Key": repository.encode({"userId": user_id, "itemId": item_id}),
        "UpdateExpression": expression,
        "ConditionExpression": condition,
        "ExpressionAttributeValues": repository.encode(values),
    }
    if names:
        result["ExpressionAttributeNames"] = names
    return {"Update": result}


def approved(repository, user_id):
    return bool((repository.get(user_id, "CHAT_ACCESS") or {}).get("approved"))


def public_chat(item):
    return {
        key: item[key]
        for key in ("chatId", "title", "createdAt", "updatedAt", "turnCount")
    }


def list_chats(repository, user_id):
    chats = sorted(
        query(repository, user_id, "CHAT#"), key=lambda x: x["updatedAt"], reverse=True
    )
    return {
        "chats": [public_chat(c) for c in chats],
        "available": approved(repository, user_id),
    }


def create_chat(repository, user_id, request, now):
    if not isinstance(request, dict) or set(request) != {"chatId"}:
        raise ValueError("Expected chatId")
    chat_id = uuid(request["chatId"])
    existing = repository.get(user_id, f"CHAT#{chat_id}")
    if existing:
        return public_chat(existing)
    if not approved(repository, user_id):
        raise ValueError("Chat is not enabled for this account yet")
    item = {
        "userId": user_id,
        "itemId": f"CHAT#{chat_id}",
        "chatId": chat_id,
        "title": "New conversation",
        "createdAt": timestamp(now),
        "updatedAt": timestamp(now),
        "turnCount": 0,
    }
    transaction(
        repository,
        [
            put(repository, item, ConditionExpression="attribute_not_exists(itemId)"),
            update(
                repository,
                user_id,
                "CHAT_COUNT",
                "ADD #n :one",
                "attribute_not_exists(#n) OR #n < :max",
                {":one": 1, ":max": 100},
                {"#n": "count"},
            ),
        ],
    )
    return public_chat(item)


def finish(repository, user_id, chat_id, turn, now, *, answer=None, error=None):
    transaction(
        repository,
        [
            update(
                repository,
                user_id,
                f"TURN#{chat_id}#{turn['messageId']}",
                (
                    "SET #s = :done, answer = "
                    + (
                        "if_not_exists(answer, :answer)"
                        if error and not answer
                        else ":answer"
                    )
                    + ", errorMessage = :error"
                ),
                "#s = :queued OR #s = :generating",
                {
                    ":done": "FAILED" if error else "COMPLETE",
                    ":answer": answer or "",
                    ":error": error or "",
                    ":queued": "QUEUED",
                    ":generating": "GENERATING",
                },
                {"#s": "status"},
            ),
            update(
                repository,
                user_id,
                f"CHAT#{chat_id}",
                "SET updatedAt = :now REMOVE pendingId",
                "pendingId = :id",
                {":now": timestamp(now), ":id": turn["messageId"]},
            ),
        ],
    )


def get_chat(repository, user_id, chat_id, now):
    chat_id = uuid(chat_id)
    item = repository.get(user_id, f"CHAT#{chat_id}")
    if not item:
        raise NotFound("Conversation not found")
    if item.get("pendingId"):
        turn = repository.get(user_id, f"TURN#{chat_id}#{item['pendingId']}")
        if turn and int(turn["expiresAt"]) < int(now.timestamp()):
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
            item = repository.get(user_id, f"CHAT#{chat_id}")
    turns = query(repository, user_id, f"TURN#{chat_id}#")
    turns.sort(key=lambda t: int(t["sequence"]))
    return {
        **public_chat(item),
        "pending": bool(item.get("pendingId")),
        "turns": [
            {
                k: t[k]
                for k in (
                    "messageId",
                    "text",
                    "status",
                    "createdAt",
                    "answer",
                    "errorMessage",
                )
            }
            for t in turns
        ],
    }


def send(repository, user_id, chat_id, request, now, dispatch=None):
    chat_id = uuid(chat_id)
    if not isinstance(request, dict) or set(request) != {"messageId", "text"}:
        raise ValueError("Expected messageId and text")
    message_id = uuid(request["messageId"])
    text = request["text"]
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("Enter a question of 1–2000 characters")
    text = text.strip()
    chat = get_chat(repository, user_id, chat_id, now)
    existing = repository.get(user_id, f"TURN#{chat_id}#{message_id}")
    if existing:
        if existing["text"] != text:
            raise Conflict("This message ID was already used for a different question")
        return chat
    if not approved(repository, user_id):
        raise ValueError("Chat is not enabled for this account yet")
    if chat["pending"]:
        raise Conflict("Wait for the current reply before sending another question")
    if int(chat["turnCount"]) >= MAX_TURNS:
        raise ValueError("This conversation is full. Start a new chat to continue.")
    turn = {
        "userId": user_id,
        "itemId": f"TURN#{chat_id}#{message_id}",
        "messageId": message_id,
        "text": text,
        "status": "QUEUED",
        "createdAt": timestamp(now),
        "expiresAt": int(now.timestamp()) + 150,
        "sequence": int(chat["turnCount"]) + 1,
        "answer": "",
        "errorMessage": "",
    }
    access_check = {
        "ConditionCheck": {
            "TableName": repository.table_name,
            "Key": repository.encode({"userId": user_id, "itemId": "CHAT_ACCESS"}),
            "ConditionExpression": "approved = :yes",
            "ExpressionAttributeValues": repository.encode({":yes": True}),
        }
    }
    transaction(
        repository,
        [
            put(repository, turn, ConditionExpression="attribute_not_exists(itemId)"),
            update(
                repository,
                user_id,
                f"CHAT#{chat_id}",
                "SET pendingId = :id, updatedAt = :now, title = :title "
                "ADD turnCount :one",
                "attribute_not_exists(pendingId) AND turnCount = :count",
                {
                    ":id": message_id,
                    ":now": timestamp(now),
                    ":one": 1,
                    ":title": text[:70] if not chat["turnCount"] else chat["title"],
                    ":count": int(chat["turnCount"]),
                },
            ),
            update(
                repository,
                user_id,
                f"CHAT_QUOTA#{now.date().isoformat()}",
                "ADD #n :one",
                "attribute_not_exists(#n) OR #n < :max",
                {":one": 1, ":max": DAILY_LIMIT},
                {"#n": "count"},
            ),
            access_check,
        ],
    )
    if dispatch is None:
        dispatch = dispatch_turn
    try:
        dispatch(
            {
                "source": "lexikhan.chat",
                "userId": user_id,
                "chatId": chat_id,
                "messageId": message_id,
            }
        )
    except Exception:
        # A lost invoke response can mean delivery occurred. Keep the claim;
        # polling resolves it, or expiry releases it. Never invoke twice.
        pass
    return get_chat(repository, user_id, chat_id, now)


def dispatch_turn(payload):
    import json

    from botocore.config import Config

    boto3.client(
        "lambda",
        config=Config(
            connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1}
        ),
    ).invoke(
        FunctionName=os.environ["CHAT_FUNCTION_NAME"],
        InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )
