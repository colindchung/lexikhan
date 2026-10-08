from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from email_worker import advance, process
from test_reminders import NOW, USER, prepare
from vocabulary import catalog, choose, email_content


@pytest.fixture(autouse=True)
def sender(monkeypatch):
    monkeypatch.setenv("REMINDER_FROM_EMAIL", "reminders@example.test")


def provider():
    ses = Mock()
    ses.get_suppressed_destination.side_effect = ClientError(
        {"Error": {"Code": "NotFoundException"}}, "GetSuppressedDestination"
    )
    ses.send_email.return_value = {"MessageId": "id"}
    return ses


def test_daily_pool_never_repeats_and_pauses_when_exhausted(repository):
    prepare(repository)
    ses = provider()
    sent = []
    for day in range(len(catalog("ur")) + 1):
        now = datetime.fromisoformat(repository.get(USER, "REMINDER")["nextReminderAt"])
        result = process(
            repository,
            ses,
            repository.get(USER, "REMINDER"),
            now,
            "https://example.com",
        )
        if day == len(catalog("ur")):
            assert result == "vocabulary_exhausted"
        else:
            assert result == "accepted"
            attempt = repository.get(USER, f"EMAIL#{now.date().isoformat()}")
            sent.append(attempt["vocabulary"]["id"])
    assert len(sent) == len(set(sent)) == 70
    assert ses.send_email.call_count == 70
    assert choose(repository, "USER#another", "ur") is not None


def test_word_claim_collision_rolls_back_daily_attempt_and_schedule(repository):
    settings = prepare(repository)
    entry = catalog("ur")[0]
    attempt = {
        "userId": USER,
        "itemId": "EMAIL#first",
        "vocabulary": entry,
    }
    assert advance(repository, settings, NOW, attempt)
    current = repository.get(USER, "REMINDER")
    assert not advance(
        repository,
        current,
        NOW + timedelta(days=1),
        {**attempt, "itemId": "EMAIL#second"},
    )
    assert repository.get(USER, "EMAIL#second") is None
    assert repository.get(USER, "REMINDER") == current


def test_ambiguous_send_consumes_word_permanently(repository):
    settings = prepare(repository)
    ses = provider()
    ses.send_email.side_effect = TimeoutError()
    with pytest.raises(RuntimeError):
        process(repository, ses, settings, NOW, "https://example.com")
    entry = repository.get(USER, "EMAIL#2026-10-01")["vocabulary"]
    assert repository.get(USER, f"VOCAB#{entry['id']}")
    for _ in range(100):
        assert choose(repository, USER, "ur")["id"] != entry["id"]


def test_history_query_follows_pages(repository, monkeypatch):
    prepare(repository)
    for entry in catalog("ur")[:-1]:
        repository.client.put_item(
            TableName=repository.table_name,
            Item=repository.encode(
                {
                    "userId": USER,
                    "itemId": f"VOCAB#{entry['id']}",
                    "vocabularyId": entry["id"],
                }
            ),
        )
    query = repository.client.query
    monkeypatch.setattr(
        repository.client, "query", lambda **kwargs: query(**kwargs, Limit=10)
    )
    assert choose(repository, USER, "ur") == catalog("ur")[-1]


def test_email_contains_word_meaning_pronunciation_and_rtl():
    entry = catalog("ur")[0]
    content = email_content(entry, "https://example.com")
    plain = content["Body"]["Text"]["Data"]
    assert all(entry[k] in plain for k in ("text", "meaning", "pronunciation"))
    assert 'dir="rtl"' in content["Body"]["Html"]["Data"]
    assert "cards ready" not in plain
    hostile = {**entry, "text": "<script>bad</script>"}
    assert (
        "<script>"
        not in email_content(hostile, "https://example.com")["Body"]["Html"]["Data"]
    )


def test_catalog_ids_and_text_are_unique_and_spanish_is_supported():
    for language in ("ur", "es"):
        entries = catalog(language)
        assert len({e["id"] for e in entries}) == len(entries) == 70
        assert len({e["text"] for e in entries}) == len(entries)
        assert all(e["meaning"] and e["text"] for e in entries)
    assert (
        "Spanish"
        in email_content(catalog("es")[0], "https://example.com")["Subject"]["Data"]
    )
    assert catalog("unknown") == []
