import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from chat_worker import generate, process
from chats import create_chat, get_chat, list_chats, send
from main import handler
from repository import Conflict, NotFound
from test_handler import SUB, event

NOW = datetime.now(UTC)
USER = f"USER#{SUB}"


def setup(repository):
    repository.client.put_item(
        TableName=repository.table_name,
        Item=repository.encode(
            {"userId": USER, "itemId": "CHAT_ACCESS", "approved": True}
        ),
    )
    chat_id = str(uuid4())
    create_chat(repository, USER, {"chatId": chat_id}, NOW)
    return chat_id


def submit(repository, chat_id, text="What does acha mean?", dispatch=None):
    request = {"messageId": str(uuid4()), "text": text}
    send(repository, USER, chat_id, request, NOW, dispatch or Mock())
    return request, {
        "source": "lexikhan.chat",
        "userId": USER,
        "chatId": chat_id,
        "messageId": request["messageId"],
    }


def test_account_isolation_and_private_access(repository):
    chat_id = setup(repository)
    assert list_chats(repository, "USER#other") == {"chats": [], "available": False}
    with pytest.raises(NotFound):
        get_chat(repository, "USER#other", chat_id, NOW)
    with pytest.raises(NotFound):
        send(
            repository,
            "USER#other",
            chat_id,
            {"messageId": str(uuid4()), "text": "hello"},
            NOW,
            Mock(),
        )
    with pytest.raises(ValueError):
        create_chat(repository, "USER#other", {"chatId": str(uuid4())}, NOW)
    assert create_chat(repository, USER, {"chatId": chat_id}, NOW)["chatId"] == chat_id
    assert repository.get(USER, "CHAT_COUNT")["count"] == 1


def test_reply_is_durable_and_followup_has_context(repository):
    chat_id = setup(repository)
    request, payload = submit(repository, chat_id)
    responder = Mock(return_value="اچھا — acha — good; also okay.")
    assert (
        process(repository, payload, NOW, responder, lambda: "key")["status"]
        == "complete"
    )
    chat = get_chat(repository, USER, chat_id, NOW)
    assert chat["turns"][0]["answer"].startswith("اچھا")
    assert chat["pending"] is False
    assert list_chats(repository, USER)["chats"][0]["title"] == request["text"]
    _, second = submit(repository, chat_id, "Is that informal?")
    process(repository, second, NOW, responder, lambda: "key")
    messages, language, _ = responder.call_args.args
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[-1]["content"] == "Is that informal?"
    assert language == "Urdu"


def test_retry_and_duplicate_worker_do_not_generate_again(repository):
    chat_id = setup(repository)
    dispatch = Mock()
    request, payload = submit(repository, chat_id, dispatch=dispatch)
    send(repository, USER, chat_id, request, NOW, dispatch)
    assert dispatch.call_count == 1
    responder = Mock(return_value="answer")
    process(repository, payload, NOW, responder, lambda: "key")
    process(repository, payload, NOW, responder, lambda: "key")
    send(repository, USER, chat_id, request, NOW, dispatch)
    assert responder.call_count == dispatch.call_count == 1
    with pytest.raises(Conflict):
        send(repository, USER, chat_id, {**request, "text": "different"}, NOW, dispatch)


def test_concurrent_question_is_rejected(repository):
    chat_id = setup(repository)
    submit(repository, chat_id)
    with pytest.raises(Conflict):
        submit(repository, chat_id, "another")
    assert len(get_chat(repository, USER, chat_id, NOW)["turns"]) == 1


def test_failed_reply_is_saved_and_unlocks_conversation(repository):
    chat_id = setup(repository)
    _, payload = submit(repository, chat_id)
    responder = Mock(side_effect=TimeoutError("secret-sensitive-details"))
    process(repository, payload, NOW, responder, lambda: "key")
    chat = get_chat(repository, USER, chat_id, NOW)
    assert chat["turns"][0]["status"] == "FAILED"
    assert "secret" not in chat["turns"][0]["errorMessage"]
    assert not chat["pending"]
    process(repository, payload, NOW, responder, lambda: "key")
    assert responder.call_count == 1
    submit(repository, chat_id, "try again")


def test_lost_dispatch_response_keeps_one_claim_then_expires(repository):
    chat_id = setup(repository)
    dispatch = Mock(side_effect=TimeoutError())
    request, payload = submit(repository, chat_id, dispatch=dispatch)
    send(repository, USER, chat_id, request, NOW, dispatch)
    chat = get_chat(repository, USER, chat_id, NOW + timedelta(seconds=151))
    assert not chat["pending"]
    assert chat["turns"][0]["status"] == "FAILED"
    assert dispatch.call_count == 1
    responder = Mock()
    process(repository, payload, NOW + timedelta(seconds=152), responder, lambda: "key")
    responder.assert_not_called()


def test_revoked_access_blocks_queued_reply(repository):
    chat_id = setup(repository)
    _, payload = submit(repository, chat_id)
    repository.client.delete_item(
        TableName=repository.table_name,
        Key=repository.encode({"userId": USER, "itemId": "CHAT_ACCESS"}),
    )
    responder = Mock()
    process(repository, payload, NOW, responder, lambda: "key")
    responder.assert_not_called()


def test_quota_failure_does_not_store_question_or_lock(repository):
    chat_id = setup(repository)
    repository.client.put_item(
        TableName=repository.table_name,
        Item=repository.encode(
            {
                "userId": USER,
                "itemId": f"CHAT_QUOTA#{NOW.date().isoformat()}",
                "count": 50,
            }
        ),
    )
    with pytest.raises(Conflict):
        submit(repository, chat_id)
    chat = get_chat(repository, USER, chat_id, NOW)
    assert not chat["turns"] and not chat["pending"]


@pytest.mark.parametrize("text", ["", " ", "x" * 2001, 1])
def test_message_validation(repository, text):
    chat_id = setup(repository)
    with pytest.raises(ValueError):
        submit(repository, chat_id, text)


def test_http_routes_require_auth(repository):
    for method, path in [
        ("GET", "/chats"),
        ("POST", "/chats"),
        ("GET", f"/chats/{uuid4()}"),
        ("POST", f"/chats/{uuid4()}/messages"),
    ]:
        request = event(method, path)
        del request["requestContext"]["authorizer"]
        assert handler(request, None)["statusCode"] == 401
    chat_id = setup(repository)
    result = handler(event("GET", f"/chats/{chat_id}"), None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["chatId"] == chat_id


def test_provider_request_is_private_bounded_and_parses_text(monkeypatch):
    response = Mock()
    response.read.return_value = json.dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "chaabi means key"}],
                }
            ],
        }
    ).encode()
    opener = Mock()
    opener.return_value.__enter__ = Mock(return_value=response)
    opener.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("chat_worker.urlopen", opener)
    assert (
        generate([{"role": "user", "content": "چابی"}], "Urdu", "secret")
        == "chaabi means key"
    )
    payload = json.loads(opener.call_args.args[0].data)
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 1000
    assert "Roman Urdu" in payload["instructions"]
    assert opener.call_args.kwargs["timeout"] == 45
