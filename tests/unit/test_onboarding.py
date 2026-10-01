import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from main import handler
from models import Card
from onboarding import DECKS, enroll
from repository import Conflict
from service import LearningService
from test_handler import SUB, event

SETTINGS = {
    "deckId": "ur-en-v1",
    "learningLanguage": "ur",
    "baseLanguage": "en",
    "timezone": "America/Toronto",
    "dailyGoal": 3,
}


def test_enrollment_review_retry_and_isolation(repository):
    user = f"USER#{SUB}"
    repository.create_card(Card.new(user, "existing", "Old", "Card", datetime.now(UTC)))
    response = handler(event(path="/profile"), None)
    assert json.loads(response["body"])["profile"] is None
    assert json.loads(response["body"])["decks"][0]["learningLanguage"] == "ur"
    value = event("POST", "/onboarding", body=json.dumps(SETTINGS))
    first = handler(value, None)
    assert first["statusCode"] == 200
    assert handler(value, None) == first
    assert repository.client.scan(TableName=repository.table_name)["Count"] == 14
    card = repository.get(user, "CARD#ur-en-v1-01")
    assert card["explanation"].startswith("Assalaam")
    LearningService(repository).review(
        user,
        {
            "reviewId": str(uuid4()),
            "cardId": "ur-en-v1-01",
            "version": 1,
            "rating": "GOOD",
        },
        datetime.now(UTC),
    )
    reviewed = repository.get(user, "CARD#ur-en-v1-01")
    assert handler(value, None) == first
    assert repository.get(user, "CARD#ur-en-v1-01") == reviewed
    assert len(json.loads(handler(event(), None)["body"])["cards"]) == 3
    assert repository.get("USER#other", "PROFILE") is None
    assert repository.get(user, "CARD#existing")["version"] == 1
    assert (
        json.loads(handler(event(path="/profile"), None)["body"])["profile"]["timezone"]
        == "America/Toronto"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"dailyGoal": True},
        {"dailyGoal": 4},
        {"timezone": "Invalid/Zone"},
        {"deckId": []},
        {"deckId": "missing"},
        {"baseLanguage": "fr"},
        {"userId": "other"},
    ],
)
def test_invalid_settings_write_nothing(repository, changes):
    response = handler(
        event("POST", "/onboarding", body=json.dumps({**SETTINGS, **changes})), None
    )
    assert response["statusCode"] == 400
    assert repository.client.scan(TableName=repository.table_name)["Count"] == 0


def test_conflicting_retry_preserves_profile(repository):
    now = datetime.now(UTC)
    saved = enroll(repository, "USER#one", SETTINGS, now)
    with pytest.raises(Conflict):
        enroll(repository, "USER#one", {**SETTINGS, "dailyGoal": 5}, now)
    assert repository.get("USER#one", "PROFILE")["dailyGoal"] == saved["dailyGoal"]


def test_partial_collision_rolls_back_entire_enrollment(repository):
    user = "USER#one"
    repository.create_card(
        Card.new(user, "ur-en-v1-02", "Existing", "Keep", datetime.now(UTC))
    )
    with pytest.raises(Conflict):
        enroll(repository, user, SETTINGS, datetime.now(UTC))
    assert repository.get(user, "PROFILE") is None
    assert repository.get(user, "CARD#ur-en-v1-01") is None
    assert repository.get(user, "CARD#ur-en-v1-02")["answer"] == "Keep"


def test_concurrent_identical_enrollment_returns_winner(repository, monkeypatch):
    original = repository.client.transact_write_items
    triggered = False

    def race(**kwargs):
        nonlocal triggered
        if not triggered:
            triggered = True
            original(**kwargs)
        return original(**kwargs)

    monkeypatch.setattr(repository.client, "transact_write_items", race)
    assert (
        enroll(repository, "USER#one", SETTINGS, datetime.now(UTC))["deckId"]
        == "ur-en-v1"
    )
    assert repository.client.scan(TableName=repository.table_name)["Count"] == 13


def test_new_endpoints_require_auth():
    for method, path in [("GET", "/profile"), ("POST", "/onboarding")]:
        request = event(method, path)
        del request["requestContext"]["authorizer"]
        assert handler(request, None)["statusCode"] == 401


def test_catalog_never_exposes_answers(repository):
    body = json.loads(handler(event(path="/profile"), None)["body"])
    for deck in body["decks"]:
        assert "phrases" not in deck
        assert deck["cardCount"] == len(DECKS[deck["id"]]["phrases"])


def test_first_session_works_before_due_index_catches_up(repository, monkeypatch):
    enroll(repository, f"USER#{SUB}", SETTINGS, datetime.now(UTC))
    query = repository.client.query

    def lagging_index(**kwargs):
        if kwargs.get("IndexName") == "due-index":
            return {"Items": []}
        return query(**kwargs)

    monkeypatch.setattr(repository.client, "query", lagging_index)
    cards = json.loads(handler(event(), None)["body"])["cards"]
    assert [c["cardId"] for c in cards] == [f"ur-en-v1-{n:02}" for n in range(1, 4)]
    assert all("answer" not in c for c in cards)


@pytest.mark.parametrize("body", ["null", "[]", "{}", "{"])
def test_invalid_onboarding_body(repository, body):
    assert handler(event("POST", "/onboarding", body=body), None)["statusCode"] == 400
    assert repository.client.scan(TableName=repository.table_name)["Count"] == 0
