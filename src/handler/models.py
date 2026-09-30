"""Persistent learning records; FSRS's JSON preserves every scheduler field."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from fsrs import Card as FSRSCard


def timestamp(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class Card:
    userId: str
    itemId: str
    prompt: str
    answer: str
    dueAt: str
    createdAt: str
    scheduler: str
    state: str = "NEW"
    version: int = 1
    reviewCount: int = 0
    lapseCount: int = 0
    recordType: str = "CARD"

    @classmethod
    def new(
        cls, user_id: str, card_id: str, prompt: str, answer: str, now: datetime
    ) -> "Card":
        return cls(
            user_id,
            f"CARD#{card_id}",
            prompt,
            answer,
            timestamp(now),
            timestamp(now),
            FSRSCard(due=now).to_json(),
        )

    def item(self) -> dict:
        return {**asdict(self), "dueUserId": self.userId}

    @classmethod
    def from_item(cls, item: dict) -> "Card":
        return cls(**{key: item[key] for key in cls.__dataclass_fields__})

    def public(self) -> dict:
        return {
            "cardId": self.itemId.removeprefix("CARD#"),
            "prompt": self.prompt,
            "state": self.state,
            "version": int(self.version),
            "dueAt": self.dueAt,
        }


@dataclass(frozen=True)
class Review:
    userId: str
    itemId: str
    cardId: str
    rating: str
    reviewedAt: str
    previousDueAt: str
    nextDueAt: str
    previousScheduler: str
    nextScheduler: str
    request: dict
    result: dict
    recordType: str = "REVIEW"

    def item(self) -> dict:
        return asdict(self)
