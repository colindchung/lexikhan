import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from main import handler
from models import timestamp
from onboarding import enroll
from reminders import next_time, public_settings, save_settings
from repository import Conflict
from service import LearningService
from sms_worker import process, run
from test_handler import SUB, event
from test_onboarding import SETTINGS

NOW = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
USER = f"USER#{SUB}"
PHONE = "+14165550123"
REQUEST = {
    "enabled": True,
    "time": "18:00",
    "timezone": "America/Toronto",
    "version": 0,
}


def prepare(repository):
    enroll(repository, USER, SETTINGS, NOW - timedelta(days=1))
    repository.client.put_item(
        TableName=repository.table_name,
        Item=repository.encode(
            {"userId": USER, "itemId": "SMS_ACCESS", "approved": True, "phone": PHONE}
        ),
    )
    save_settings(repository, USER, REQUEST, NOW - timedelta(minutes=1))
    return repository.get(USER, "REMINDER")


def test_preferences_are_scoped_validated_idempotent_and_disable(repository):
    enroll(repository, USER, SETTINGS, NOW)
    assert public_settings(repository, USER)["available"] is False
    with pytest.raises(ValueError):
        save_settings(repository, USER, REQUEST, NOW)
    prepare(repository)
    first = public_settings(repository, USER)
    assert save_settings(repository, USER, REQUEST, NOW) == first
    with pytest.raises(Conflict):
        save_settings(repository, USER, {**REQUEST, "time": "19:00"}, NOW)
    result = save_settings(
        repository, USER, {**REQUEST, "enabled": False, "version": 1}, NOW
    )
    assert not result["enabled"] and result["nextReminderAt"] is None
    assert "reminderGroup" not in repository.get(USER, "REMINDER")
    assert public_settings(repository, "USER#other")["phone"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": "true"},
        {"version": True},
        {"time": "25:00"},
        {"timezone": "wrong"},
        {"phone": PHONE},
    ],
)
def test_settings_reject_invalid_inputs(repository, changes):
    with pytest.raises(ValueError):
        save_settings(repository, USER, {**REQUEST, **changes}, NOW)


def test_send_once_and_keep_learning_state_unchanged(repository):
    settings = prepare(repository)
    before = repository.get(USER, "CARD#ur-en-v1-01")
    sns = Mock()
    sns.check_if_phone_number_is_opted_out.return_value = {"isOptedOut": False}
    sns.publish.return_value = {"MessageId": "provider-id"}
    assert process(repository, sns, settings, NOW, "https://example.com") == "accepted"
    assert process(repository, sns, settings, NOW, "https://example.com") == "duplicate"
    assert sns.publish.call_count == 1
    assert "3 cards" in sns.publish.call_args.kwargs["Message"]
    assert "https://example.com/" in sns.publish.call_args.kwargs["Message"]
    assert repository.get(USER, "SMS#2026-10-01")["status"] == "ACCEPTED"
    assert repository.get(USER, "CARD#ur-en-v1-01") == before


def test_ambiguous_send_never_retries(repository):
    settings = prepare(repository)
    sns = Mock()
    sns.check_if_phone_number_is_opted_out.return_value = {"isOptedOut": False}
    sns.publish.side_effect = TimeoutError("phone should not be logged")
    with pytest.raises(RuntimeError):
        process(repository, sns, settings, NOW, "https://example.com")
    assert repository.get(USER, "SMS#2026-10-01")["status"] == "UNKNOWN"
    assert process(repository, sns, settings, NOW, "https://example.com") == "duplicate"
    assert sns.publish.call_count == 1


def test_opt_out_and_revocation_prevent_send(repository):
    settings = prepare(repository)
    sns = Mock()
    sns.check_if_phone_number_is_opted_out.return_value = {"isOptedOut": True}
    assert process(repository, sns, settings, NOW, "https://example.com") == "opted_out"
    sns.publish.assert_not_called()
    repository.client.delete_item(
        TableName=repository.table_name,
        Key=repository.encode({"userId": USER, "itemId": "SMS_ACCESS"}),
    )
    assert (
        process(repository, sns, settings, NOW, "https://example.com") == "unapproved"
    )
    sns.publish.assert_not_called()


def test_stale_worker_cannot_send_after_disable(repository):
    stale = prepare(repository)
    save_settings(repository, USER, {**REQUEST, "enabled": False, "version": 1}, NOW)
    sns = Mock()
    sns.check_if_phone_number_is_opted_out.return_value = {"isOptedOut": False}
    assert process(repository, sns, stale, NOW, "https://example.com") == "duplicate"
    sns.publish.assert_not_called()


def test_no_due_cards_skips_message(repository):
    settings = prepare(repository)
    for number in range(1, 13):
        LearningService(repository).review(
            USER,
            {
                "reviewId": str(uuid4()),
                "cardId": f"ur-en-v1-{number:02}",
                "version": 1,
                "rating": "EASY",
            },
            NOW,
        )
    sns = Mock()
    assert process(repository, sns, settings, NOW, "https://example.com") == "no_cards"
    sns.publish.assert_not_called()
    assert repository.get(USER, "REMINDER")["nextReminderAt"] > timestamp(NOW)


def test_run_queries_sparse_index_and_rechecks_settings(repository):
    prepare(repository)
    sns = Mock()
    sns.check_if_phone_number_is_opted_out.return_value = {"isOptedOut": False}
    sns.publish.return_value = {"MessageId": "id"}
    assert run(repository, sns, NOW, "https://example.com") == {"accepted": 1}
    assert run(repository, sns, NOW, "https://example.com") == {"accepted": 0}


def test_dst_gap_and_fold():
    assert (
        next_time(datetime(2026, 3, 8, 6, tzinfo=UTC), "America/Toronto", "02:30")
        == "2026-03-08T07:30:00.000000Z"
    )
    assert (
        next_time(datetime(2026, 11, 1, 4, tzinfo=UTC), "America/Toronto", "01:30")
        == "2026-11-01T05:30:00.000000Z"
    )
    assert (
        next_time(datetime(2026, 11, 1, 5, 45, tzinfo=UTC), "America/Toronto", "01:30")
        == "2026-11-02T06:30:00.000000Z"
    )


def test_api_requires_auth_and_persists_settings(repository):
    prepare(repository)
    for method in ("GET", "POST"):
        request = event(method, "/reminders")
        del request["requestContext"]["authorizer"]
        assert handler(request, None)["statusCode"] == 401
    response = handler(event(path="/reminders"), None)
    assert json.loads(response["body"])["phone"] == PHONE
    response = handler(
        event(
            "POST",
            "/reminders",
            body=json.dumps({**REQUEST, "enabled": False, "version": 1}),
        ),
        None,
    )
    assert response["statusCode"] == 200
    assert not json.loads(response["body"])["enabled"]


def test_outage_does_not_send_hours_late(repository):
    settings = prepare(repository)
    sns = Mock()
    assert (
        process(
            repository, sns, settings, NOW + timedelta(hours=2), "https://example.com"
        )
        == "expired"
    )
    sns.publish.assert_not_called()


def test_retry_after_database_failure_does_not_publish_twice(repository, monkeypatch):
    settings = prepare(repository)
    sns = Mock()
    sns.check_if_phone_number_is_opted_out.return_value = {"isOptedOut": False}
    sns.publish.return_value = {"MessageId": "id"}
    monkeypatch.setattr(
        repository.client,
        "update_item",
        Mock(side_effect=RuntimeError("database down")),
    )
    with pytest.raises(RuntimeError):
        process(repository, sns, settings, NOW, "https://example.com")
    assert repository.get(USER, "SMS#2026-10-01")["status"] == "CLAIMED"
    assert process(repository, sns, settings, NOW, "https://example.com") == "duplicate"
    assert sns.publish.call_count == 1
