from dataclasses import replace
from datetime import datetime

from fsrs import Card as FSRSCard
from fsrs import Rating, Scheduler
from models import Card, Review, timestamp
from repository import Conflict, NotFound, Repository


class LearningService:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.scheduler = Scheduler()

    def answer(self, user_id: str, card_id: str) -> dict:
        # Scope the primary-key lookup to the authenticated learner, including
        # when another learner has a card with the same ID.
        item = self.repository.get(user_id, f"CARD#{card_id}")
        if item is None or item.get("recordType") != "CARD":
            raise NotFound("Card not found")
        card = Card.from_item(item)
        result = {"cardId": card_id, "answer": card.answer, "examples": card.examples}
        if card.explanation is not None:
            result["explanation"] = card.explanation
        if card.audioUrl is not None:
            result["audioUrl"] = card.audioUrl
        return result

    def review(self, user_id: str, request: dict, now: datetime) -> dict:
        saved = self.repository.saved_review(user_id, request["reviewId"], request)
        if saved is not None:
            return saved
        item = self.repository.get(user_id, f"CARD#{request['cardId']}")
        if item is None:
            raise NotFound("Card not found")
        card = Card.from_item(item)
        if card.version != request["version"]:
            # A concurrent retry may commit between our idempotency and card reads.
            saved = self.repository.saved_review(user_id, request["reviewId"], request)
            if saved is not None:
                return saved
            raise Conflict("Card version is stale; refresh the session")
        updated, _ = self.scheduler.review_card(
            FSRSCard.from_json(card.scheduler),
            Rating[request["rating"].title()],
            review_datetime=now,
        )
        next_card = replace(
            card,
            scheduler=updated.to_json(),
            dueAt=timestamp(updated.due),
            state=updated.state.name.upper(),
            version=card.version + 1,
            reviewCount=card.reviewCount + 1,
            lapseCount=card.lapseCount
            + int(card.state == "REVIEW" and request["rating"] == "AGAIN"),
        )
        result = {**next_card.public(), "nextDueAt": next_card.dueAt}
        review = Review(
            userId=user_id,
            itemId=f"REVIEW#{request['reviewId']}",
            cardId=card.itemId,
            rating=request["rating"],
            reviewedAt=timestamp(now),
            previousDueAt=card.dueAt,
            nextDueAt=next_card.dueAt,
            previousScheduler=card.scheduler,
            nextScheduler=next_card.scheduler,
            request=request,
            result=result,
        )
        return self.repository.record_review(next_card, review, card.version)
