import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from main import handler
from models import Card

ARN = "arn:aws:iam::123456789012:user/test"


def event(method="GET", path="/session", **extra):
    return {
        "requestContext": {
            "http": {"method": method, "path": path},
            "authorizer": {"iam": {"userArn": ARN}},
        },
        **extra,
    }


def test_health_route(monkeypatch):
    monkeypatch.setenv("STAGE", "test")
    response = handler(event(path="/health"), None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"status": "ok", "stage": "test"}


def test_removed_entry_points():
    assert handler(event("POST", "/run"), None)["statusCode"] == 404
    assert handler({"source": "scheduler"}, None)["statusCode"] == 404


def test_anonymous_access_denied():
    for method, path in [
        ("GET", "/session"),
        ("GET", "/cards/one/answer"),
        ("POST", "/reviews"),
    ]:
        value = event(method, path)
        del value["requestContext"]["authorizer"]
        assert handler(value, None)["statusCode"] == 401


def test_http_lifecycle(repository):
    repository.create_card(
        Card.new(f"USER#{ARN}", "one", "Hello", "Bonjour", datetime.now(UTC))
    )
    response = handler(event(), None)
    cards = json.loads(response["body"])["cards"]
    assert len(cards) == 1 and "answer" not in cards[0]
    before = repository.get(f"USER#{ARN}", "CARD#one")
    revealed = handler(event(path="/cards/one/answer"), None)
    assert revealed["statusCode"] == 200
    assert revealed["headers"]["cache-control"] == "no-store"
    assert json.loads(revealed["body"]) == {
        "cardId": "one",
        "answer": "Bonjour",
        "examples": [],
    }
    assert handler(event(path="/cards/one/answer"), None) == revealed
    assert repository.get(f"USER#{ARN}", "CARD#one") == before
    assert repository.client.scan(TableName=repository.table_name)["Count"] == 1
    payload = {
        "reviewId": str(uuid4()),
        "cardId": cards[0]["cardId"],
        "version": cards[0]["version"],
        "rating": "GOOD",
    }
    value = event("POST", "/reviews", body=json.dumps(payload))
    first = handler(value, None)
    assert first["statusCode"] == 200
    assert handler(value, None) == first
    payload["reviewId"] = str(uuid4())
    assert (
        handler(event("POST", "/reviews", body=json.dumps(payload)), None)["statusCode"]
        == 409
    )
    assert json.loads(handler(event(), None)["body"])["cards"] == []


@pytest.mark.parametrize(
    "body", ["", "null", "[]", "{}", "{", '"text"', '{"userId":"other"}']
)
def test_invalid_body(repository, body):
    response = handler(event("POST", "/reviews", body=body), None)
    assert response["statusCode"] == 400


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 0},
        {"rating": []},
        {"rating": "bad"},
        {"reviewId": "bad"},
        {"cardId": "../x"},
        {"typedAnswer": 1},
        {"typedAnswer": "x" * 4001},
        {"userId": "foreign"},
    ],
)
def test_validation(repository, changes):
    payload = {
        "reviewId": str(uuid4()),
        "cardId": "one",
        "version": 1,
        "rating": "GOOD",
        **changes,
    }
    assert (
        handler(event("POST", "/reviews", body=json.dumps(payload)), None)["statusCode"]
        == 400
    )


@pytest.mark.parametrize(
    "params",
    [
        {"newLimit": "11"},
        {"reviewLimit": "51"},
        {"reviewLimit": "-1"},
        {"newLimit": "abc"},
    ],
)
def test_invalid_limits(repository, params):
    assert handler(event(queryStringParameters=params), None)["statusCode"] == 400


def test_storage_failure_is_retryable(repository, monkeypatch):
    from botocore.exceptions import ClientError
    from repository import Repository

    def fail(*args):
        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "Query"
        )

    monkeypatch.setattr(Repository, "session", fail)
    response = handler(event(), None)
    assert response["statusCode"] == 503
    assert json.loads(response["body"])["error"]["code"] == "unavailable"


def test_base64_request(repository):
    import base64

    payload = {
        "reviewId": str(uuid4()),
        "cardId": "missing",
        "version": 1,
        "rating": "GOOD",
    }
    response = handler(
        event(
            "POST",
            "/reviews",
            isBase64Encoded=True,
            body=base64.b64encode(json.dumps(payload).encode()).decode(),
        ),
        None,
    )
    assert response["statusCode"] == 404
    assert (
        handler(event("POST", "/reviews", isBase64Encoded=True, body="!"), None)[
            "statusCode"
        ]
        == 400
    )


def test_answer_is_scoped_to_verified_owner(repository):
    repository.create_card(
        Card.new("USER#other", "foreign", "Hi", "Secret", datetime.now(UTC))
    )
    missing = handler(event(path="/cards/missing/answer"), None)
    foreign = handler(
        event(path="/cards/foreign/answer", queryStringParameters={"userId": "other"}),
        None,
    )
    assert missing["statusCode"] == 404
    assert foreign == missing
    repository.create_card(
        Card.new(f"USER#{ARN}", "foreign", "Hi", "Owned", datetime.now(UTC))
    )
    owned = handler(event(path="/cards/foreign/answer"), None)
    assert json.loads(owned["body"])["answer"] == "Owned"


@pytest.mark.parametrize("card_id", ["..", "CARD%23one", "a" * 129, "hello.world"])
def test_answer_rejects_invalid_card_id(repository, card_id):
    assert handler(event(path=f"/cards/{card_id}/answer"), None)["statusCode"] == 400


def test_answer_optional_content_survives_review(repository, caplog):
    from dataclasses import replace

    card = replace(
        Card.new(f"USER#{ARN}", "one", "Hi", "Bonjour", datetime.now(UTC)),
        explanation="A greeting",
        examples=["Bonjour, mon ami"],
        audioUrl="https://example.com/bonjour.mp3",
    )
    repository.create_card(card)
    expected = {
        "cardId": "one",
        "answer": "Bonjour",
        "explanation": "A greeting",
        "examples": ["Bonjour, mon ami"],
        "audioUrl": card.audioUrl,
    }
    assert (
        json.loads(handler(event(path="/cards/one/answer"), None)["body"]) == expected
    )
    session = json.loads(handler(event(), None)["body"])["cards"][0]
    assert not {"answer", "explanation", "examples", "audioUrl"} & session.keys()
    payload = {
        "reviewId": str(uuid4()),
        "cardId": "one",
        "version": 1,
        "rating": "GOOD",
    }
    assert (
        handler(event("POST", "/reviews", body=json.dumps(payload)), None)["statusCode"]
        == 200
    )
    assert (
        json.loads(handler(event(path="/cards/one/answer"), None)["body"]) == expected
    )
    assert "Bonjour" not in caplog.text and "A greeting" not in caplog.text


def test_answer_supports_existing_cards_without_optional_fields(repository):
    item = Card.new(f"USER#{ARN}", "one", "Hi", "Bonjour", datetime.now(UTC)).item()
    for key in ("explanation", "examples", "audioUrl"):
        item.pop(key)
    repository.client.put_item(
        TableName=repository.table_name, Item=repository.encode(item)
    )
    response = handler(event(path="/cards/one/answer"), None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["examples"] == []
