"""Versioned starter content and atomic, retry-safe enrollment."""

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from botocore.exceptions import ClientError
from models import Card, timestamp
from repository import Conflict

# Original, curated phrase pairs. IDs and content stay stable after publication.
DECKS = {
    "ur-en-v1": {
        "name": "Urdu essentials",
        "learningLanguage": "ur",
        "baseLanguage": "en",
        "phrases": [
            (
                "Hello (a respectful greeting)",
                "السلام علیکم",
                "Assalaam alaikum: a common respectful greeting.",
            ),
            ("Thank you", "شکریہ", "Shukriya."),
            ("Please", "براہ کرم", "Baraah-e-karam: a polite, formal request."),
            ("Yes", "جی ہاں", "Ji haan: a polite yes."),
            ("No", "نہیں", "Nahin."),
            (
                "How are you? (polite)",
                "آپ کیسے ہیں؟",
                "Aap kaise hain?: polite address; for a woman, "
                "آپ کیسی ہیں؟ (aap kaisi hain?).",
            ),
            ("I'm fine", "میں ٹھیک ہوں", "Main theek hoon."),
            (
                "What is your name? (polite)",
                "آپ کا نام کیا ہے؟",
                "Aap ka naam kya hai?",
            ),
            (
                "Please forgive me / excuse me",
                "معاف کیجیے",
                "Maaf kijiye: a polite apology or request to excuse you.",
            ),
            ("I need water", "مجھے پانی چاہیے", "Mujhe paani chahiye."),
            (
                "How much does this cost?",
                "یہ کتنے کا ہے؟",
                "Yeh kitne ka hai?: for an item with masculine grammatical gender.",
            ),
            ("Goodbye", "خدا حافظ", "Khuda haafiz."),
        ],
    },
    "es-en-v1": {
        "name": "Spanish essentials",
        "learningLanguage": "es",
        "baseLanguage": "en",
        "phrases": [
            ("Hello", "Hola"),
            ("Thank you", "Gracias"),
            ("Please", "Por favor"),
            ("Good morning", "Buenos días"),
            ("Good night", "Buenas noches"),
            ("See you later", "Hasta luego"),
            ("Excuse me", "Disculpe"),
            ("I'm sorry", "Lo siento"),
            ("I don't understand", "No entiendo"),
            ("Do you speak English?", "¿Habla inglés?"),
            ("How much does it cost?", "¿Cuánto cuesta?"),
            ("Where is the bathroom?", "¿Dónde está el baño?"),
        ],
    },
}


def catalog():
    return [
        {
            **{k: v for k, v in deck.items() if k != "phrases"},
            "id": key,
            "cardCount": len(deck["phrases"]),
        }
        for key, deck in DECKS.items()
    ]


def public_profile(item):
    if not item:
        return None
    return {
        key: item[key]
        for key in (
            "deckId",
            "learningLanguage",
            "baseLanguage",
            "timezone",
            "dailyGoal",
            "createdAt",
        )
    }


def enroll(repository, user_id: str, request: dict, now: datetime):
    fields = {"deckId", "learningLanguage", "baseLanguage", "timezone", "dailyGoal"}
    if not isinstance(request, dict) or set(request) != fields:
        raise ValueError("Expected deck, language, timezone, and daily goal settings")
    if not all(isinstance(request[k], str) for k in fields - {"dailyGoal"}):
        raise ValueError("Deck, languages, and timezone must be strings")
    deck = DECKS.get(request["deckId"])
    if not deck or any(
        request[k] != deck[k] for k in ("learningLanguage", "baseLanguage")
    ):
        raise ValueError("Choose an available starter deck and language pair")
    if type(request["dailyGoal"]) is not int or request["dailyGoal"] not in (3, 5, 10):
        raise ValueError("Daily goal must be 3, 5, or 10 cards")
    try:
        ZoneInfo(request["timezone"])
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Choose a valid IANA timezone") from None

    def existing():
        saved = repository.get(user_id, "PROFILE")
        if saved:
            if any(saved[key] != request[key] for key in fields):
                raise Conflict("Onboarding is already complete; reload your profile")
            return public_profile(saved)
        return None

    saved = existing()
    if saved:
        return saved
    profile = {
        "userId": user_id,
        "itemId": "PROFILE",
        "recordType": "PROFILE",
        **request,
        "createdAt": timestamp(now),
    }
    cards = [
        replace(
            Card.new(user_id, f"{request['deckId']}-{i:02}", phrase[0], phrase[1], now),
            explanation=phrase[2] if len(phrase) > 2 else None,
        ).item()
        for i, phrase in enumerate(deck["phrases"], 1)
    ]
    try:
        repository.client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": repository.table_name,
                        "Item": repository.encode(item),
                        "ConditionExpression": "attribute_not_exists(itemId)",
                    }
                }
                for item in [profile, *cards]
            ]
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "TransactionCanceledException":
            saved = existing()
            if saved:
                return saved
            if any(
                r.get("Code") == "ConditionalCheckFailed"
                for r in error.response.get("CancellationReasons", [])
            ):
                raise Conflict(
                    "Starter cards already exist; enrollment was not changed"
                ) from error
        raise
    return public_profile(profile)
