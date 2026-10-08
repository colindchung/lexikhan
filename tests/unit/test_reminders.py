import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from email_worker import process, run
from main import handler
from models import timestamp
from onboarding import enroll
from reminders import next_time, public_settings, save_settings
from repository import Conflict
from service import LearningService
from test_handler import SUB, event
from test_onboarding import SETTINGS


@pytest.fixture(autouse=True)
def sender(monkeypatch):
    monkeypatch.setenv("REMINDER_FROM_EMAIL", "reminders@example.test")


NOW = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
USER = f"USER#{SUB}"
EMAIL = "person@example.test"
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
            {"userId": USER, "itemId": "EMAIL_ACCESS", "approved": True, "email": EMAIL}
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
    assert public_settings(repository, "USER#other")["email"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": "true"},
        {"version": True},
        {"time": "25:00"},
        {"timezone": "wrong"},
        {"email": EMAIL},
    ],
)
def test_settings_reject_invalid_inputs(repository, changes):
    with pytest.raises(ValueError):
        save_settings(repository, USER, {**REQUEST, **changes}, NOW)


def test_send_once_and_keep_learning_state_unchanged(repository):
    settings = prepare(repository)
    before = repository.get(USER, "CARD#ur-en-v1-01")
    ses = Mock()
    ses.get_suppressed_destination.side_effect = ClientError(
        {"Error": {"Code": "NotFoundException"}}, "GetSuppressedDestination"
    )
    ses.send_email.return_value = {"MessageId": "provider-id"}
    assert process(repository, ses, settings, NOW, "https://example.com") == "accepted"
    assert process(repository, ses, settings, NOW, "https://example.com") == "duplicate"
    assert ses.send_email.call_count == 1
    assert (
        "Your daily Urdu"
        in ses.send_email.call_args.kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]
    )
    assert (
        "https://example.com/"
        in ses.send_email.call_args.kwargs["Content"]["Simple"]["Body"]["Text"]["Data"]
    )
    assert (
        ses.send_email.call_args.kwargs["FromEmailAddress"] == "reminders@example.test"
    )
    assert ses.send_email.call_args.kwargs["Destination"] == {"ToAddresses": [EMAIL]}
    assert repository.get(USER, "EMAIL#2026-10-01")["status"] == "ACCEPTED"
    assert repository.get(USER, "CARD#ur-en-v1-01") == before


def test_ambiguous_send_never_retries(repository):
    settings = prepare(repository)
    ses = Mock()
    ses.get_suppressed_destination.side_effect = ClientError(
        {"Error": {"Code": "NotFoundException"}}, "GetSuppressedDestination"
    )
    ses.send_email.side_effect = TimeoutError("email should not be logged")
    with pytest.raises(RuntimeError):
        process(repository, ses, settings, NOW, "https://example.com")
    assert repository.get(USER, "EMAIL#2026-10-01")["status"] == "UNKNOWN"
    assert process(repository, ses, settings, NOW, "https://example.com") == "duplicate"
    assert ses.send_email.call_count == 1


def test_opt_out_and_revocation_prevent_send(repository):
    settings = prepare(repository)
    ses = Mock()
    ses.get_suppressed_destination.return_value = {
        "SuppressedDestination": {"Reason": "BOUNCE"}
    }
    assert (
        process(repository, ses, settings, NOW, "https://example.com") == "suppressed"
    )
    ses.send_email.assert_not_called()
    repository.client.delete_item(
        TableName=repository.table_name,
        Key=repository.encode({"userId": USER, "itemId": "EMAIL_ACCESS"}),
    )
    assert (
        process(repository, ses, settings, NOW, "https://example.com") == "unapproved"
    )
    ses.send_email.assert_not_called()


def test_stale_worker_cannot_send_after_disable(repository):
    stale = prepare(repository)
    save_settings(repository, USER, {**REQUEST, "enabled": False, "version": 1}, NOW)
    ses = Mock()
    ses.get_suppressed_destination.side_effect = ClientError(
        {"Error": {"Code": "NotFoundException"}}, "GetSuppressedDestination"
    )
    assert process(repository, ses, stale, NOW, "https://example.com") == "duplicate"
    ses.send_email.assert_not_called()


def test_no_due_cards_still_sends_vocabulary(repository):
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
    ses = Mock()
    ses.get_suppressed_destination.side_effect = ClientError(
        {"Error": {"Code": "NotFoundException"}}, "GetSuppressedDestination"
    )
    ses.send_email.return_value = {"MessageId": "id"}
    assert process(repository, ses, settings, NOW, "https://example.com") == "accepted"
    ses.send_email.assert_called_once()
    assert repository.get(USER, "REMINDER")["nextReminderAt"] > timestamp(NOW)


def test_run_queries_sparse_index_and_rechecks_settings(repository):
    prepare(repository)
    ses = Mock()
    ses.get_suppressed_destination.side_effect = ClientError(
        {"Error": {"Code": "NotFoundException"}}, "GetSuppressedDestination"
    )
    ses.send_email.return_value = {"MessageId": "id"}
    assert run(repository, ses, NOW, "https://example.com") == {"accepted": 1}
    assert run(repository, ses, NOW, "https://example.com") == {"accepted": 0}


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
    assert json.loads(response["body"])["email"] == EMAIL
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
    ses = Mock()
    assert (
        process(
            repository, ses, settings, NOW + timedelta(hours=2), "https://example.com"
        )
        == "expired"
    )
    ses.send_email.assert_not_called()


def test_retry_after_database_failure_does_not_publish_twice(repository, monkeypatch):
    settings = prepare(repository)
    ses = Mock()
    ses.get_suppressed_destination.side_effect = ClientError(
        {"Error": {"Code": "NotFoundException"}}, "GetSuppressedDestination"
    )
    ses.send_email.return_value = {"MessageId": "id"}
    monkeypatch.setattr(
        repository.client,
        "update_item",
        Mock(side_effect=RuntimeError("database down")),
    )
    with pytest.raises(RuntimeError):
        process(repository, ses, settings, NOW, "https://example.com")
    assert repository.get(USER, "EMAIL#2026-10-01")["status"] == "CLAIMED"
    assert process(repository, ses, settings, NOW, "https://example.com") == "duplicate"
    assert ses.send_email.call_count == 1


def test_legacy_sms_consent_does_not_enable_email(repository):
    settings = prepare(repository)
    settings.pop("channel")
    repository.client.put_item(
        TableName=repository.table_name, Item=repository.encode(settings)
    )
    assert not public_settings(repository, USER)["enabled"]
    ses = Mock()
    assert process(repository, ses, settings, NOW, "https://example.com") == "skipped"
    ses.send_email.assert_not_called()
    result = save_settings(repository, USER, {**REQUEST, "version": 1}, NOW)
    assert result["enabled"]


def test_suppression_lookup_failure_does_not_send(repository):
    settings = prepare(repository)
    ses = Mock()
    ses.get_suppressed_destination.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException"}}, "GetSuppressedDestination"
    )
    with pytest.raises(ClientError):
        process(repository, ses, settings, NOW, "https://example.com")
    ses.send_email.assert_not_called()
