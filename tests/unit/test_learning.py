from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fsrs import Card as FSRSCard
from models import Card, timestamp
from repository import Conflict, NotFound, Repository
from seed_dev import seed
from service import LearningService

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
USER = "USER#arn:aws:iam::123456789012:user/test"


def request(**changes):
    return {
        "reviewId": str(uuid4()),
        "cardId": "one",
        "version": 1,
        "rating": "GOOD",
        **changes,
    }


def add_card(repository, card_id="one", **changes):
    card = replace(Card.new(USER, card_id, "Prompt", "Secret answer", NOW), **changes)
    repository.create_card(card)
    return card


@pytest.mark.parametrize("rating", ["AGAIN", "HARD", "GOOD", "EASY"])
def test_persistence_and_retry_after_restart(repository, rating):
    add_card(repository)
    payload = request(rating=rating, typedAnswer="My attempt")
    result = LearningService(repository).review(USER, payload, NOW)
    assert result["version"] == 2
    assert result["nextDueAt"] > timestamp(NOW)
    restarted = Repository(repository.client, repository.table_name)
    assert (
        LearningService(restarted).review(USER, payload, NOW + timedelta(days=1))
        == result
    )
    stored = restarted.get(USER, "CARD#one")
    assert stored["reviewCount"] == 1
    assert timestamp(FSRSCard.from_json(stored["scheduler"]).due) == result["nextDueAt"]
    assert repository.client.scan(TableName=repository.table_name)["Count"] == 2
    assert repository.session(USER, timestamp(NOW), 20, 3) == []
    event = restarted.get(USER, f"REVIEW#{payload['reviewId']}")
    assert event["request"]["typedAnswer"] == "My attempt"
    assert "dueUserId" not in event


def test_stale_version_and_reused_id_do_not_mutate(repository):
    add_card(repository)
    payload = request()
    service = LearningService(repository)
    service.review(USER, payload, NOW)
    with pytest.raises(Conflict):
        service.review(USER, request(), NOW)
    with pytest.raises(Conflict):
        service.review(USER, {**payload, "rating": "EASY"}, NOW)
    assert repository.get(USER, "CARD#one")["version"] == 2
    assert repository.client.scan(TableName=repository.table_name)["Count"] == 2


def test_interleaved_reviews_atomicity(repository, monkeypatch):
    add_card(repository)
    write = repository.record_review
    winner = request(rating="EASY")

    def competing_write(card, review, version):
        monkeypatch.setattr(repository, "record_review", write)
        LearningService(repository).review(USER, winner, NOW)
        return write(card, review, version)

    monkeypatch.setattr(repository, "record_review", competing_write)
    loser = request()
    with pytest.raises(Conflict):
        LearningService(repository).review(USER, loser, NOW)
    assert repository.get(USER, f"REVIEW#{loser['reviewId']}") is None
    assert repository.get(USER, "CARD#one")["version"] == 2


def test_interleaved_identical_retries_return_winner(repository, monkeypatch):
    add_card(repository)
    write = repository.record_review
    payload = request()
    results = []

    def competing_write(card, review, version):
        monkeypatch.setattr(repository, "record_review", write)
        results.append(LearningService(repository).review(USER, payload, NOW))
        return write(card, review, version)

    monkeypatch.setattr(repository, "record_review", competing_write)
    assert LearningService(repository).review(USER, payload, NOW) == results[0]
    assert repository.get(USER, "CARD#one")["reviewCount"] == 1


def test_due_order_limits_pagination_and_isolation(repository, monkeypatch):
    add_card(repository, "new", dueAt=timestamp(NOW - timedelta(days=10)))
    add_card(
        repository, "recent", state="REVIEW", dueAt=timestamp(NOW - timedelta(days=1))
    )
    add_card(
        repository, "old", state="REVIEW", dueAt=timestamp(NOW - timedelta(days=3))
    )
    add_card(repository, "future", dueAt=timestamp(NOW + timedelta(days=1)))
    add_card(repository, "foreign", userId="USER#other")
    query = repository.client.query
    monkeypatch.setattr(repository.client, "query", lambda **kw: query(**kw, Limit=1))
    cards = repository.session(USER, timestamp(NOW), 2, 1)
    assert [c["cardId"] for c in cards] == ["old", "recent", "new"]
    assert all("answer" not in c and "scheduler" not in c for c in cards)
    assert len(repository.session(USER, timestamp(NOW), 1, 0)) == 1
    assert repository.session(USER, timestamp(NOW), 0, 0) == []
    with pytest.raises(NotFound):
        LearningService(repository).review("USER#other", request(), NOW)


def test_seed_is_idempotent_and_dev_only(repository):
    arn = USER.removeprefix("USER#")
    assert seed(repository.client, repository.table_name, arn) == 5
    assert seed(repository.client, repository.table_name, arn) == 0
    table_arn = repository.client.describe_table(TableName=repository.table_name)[
        "Table"
    ]["TableArn"]
    repository.client.tag_resource(
        ResourceArn=table_arn, Tags=[{"Key": "Stage", "Value": "prod"}]
    )
    with pytest.raises(ValueError, match="Stage=dev"):
        seed(repository.client, repository.table_name, arn)


def test_retry_commits_between_idempotency_and_card_read(repository, monkeypatch):
    add_card(repository)
    payload = request()
    original_get = repository.get
    winner = []

    def racing_get(user_id, item_id):
        if item_id == "CARD#one":
            monkeypatch.setattr(repository, "get", original_get)
            winner.append(LearningService(repository).review(USER, payload, NOW))
        return original_get(user_id, item_id)

    monkeypatch.setattr(repository, "get", racing_get)
    assert LearningService(repository).review(USER, payload, NOW) == winner[0]


def test_stale_index_does_not_return_rescheduled_card(repository, monkeypatch):
    card = add_card(repository)
    LearningService(repository).review(USER, request(), NOW)
    monkeypatch.setattr(
        repository.client,
        "query",
        lambda **kw: {"Items": [repository.encode(card.item())]},
    )
    assert repository.session(USER, timestamp(NOW), 20, 3) == []
