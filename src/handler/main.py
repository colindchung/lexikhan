"""HTTP adapter. Learning routes require API Gateway's verified Cognito JWT context."""

import base64
import json
import logging
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import boto3
import chats
from botocore.exceptions import ClientError
from models import timestamp
from onboarding import catalog, enroll, public_profile
from reminders import public_settings, save_settings
from repository import Conflict, NotFound, Repository
from service import LearningService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(
            body, default=lambda x: int(x) if isinstance(x, Decimal) else str(x)
        ),
    }


def _limit(params: dict, key: str, default: int, maximum: int) -> int:
    value = params.get(key, str(default))
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,3}", value):
        raise ValueError(f"{key} must be an integer between 0 and {maximum}")
    if not 0 <= int(value) <= maximum:
        raise ValueError(f"{key} must be between 0 and {maximum}")
    return int(value)


def _body(event: dict):
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body, validate=True).decode("utf-8")
    if len(body) > 16000:
        raise ValueError("Request body is too large")
    return json.loads(body)


def _review_request(event: dict) -> dict:
    request = _body(event)
    required = {"reviewId", "cardId", "version", "rating"}
    if (
        not isinstance(request, dict)
        or not required <= request.keys()
        or request.keys() - required - {"typedAnswer"}
    ):
        raise ValueError(
            "Expected reviewId, cardId, version, rating and optional typedAnswer"
        )
    if not isinstance(request["reviewId"], str):
        raise ValueError("reviewId must be a UUID")
    request["reviewId"] = str(UUID(request["reviewId"]))
    if not isinstance(request["cardId"], str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", request["cardId"]
    ):
        raise ValueError("Invalid cardId")
    if type(request["version"]) is not int or request["version"] < 1:
        raise ValueError("version must be a positive integer")
    if request["rating"] not in ("AGAIN", "HARD", "GOOD", "EASY"):
        raise ValueError("Invalid rating")
    if "typedAnswer" in request and (
        not isinstance(request["typedAnswer"], str)
        or len(request["typedAnswer"]) > 4000
    ):
        raise ValueError("typedAnswer must be a string of at most 4000 characters")
    return request


def handler(event: dict, context) -> dict:
    http = event.get("requestContext", {}).get("http", {})
    route = (http.get("method"), http.get("path"))
    if route == ("GET", "/health"):
        return _response(200, {"status": "ok", "stage": os.getenv("STAGE")})
    answer_route = (
        re.fullmatch(r"/cards/([^/]+)/answer", route[1] or "")
        if route[0] == "GET"
        else None
    )
    chat_route = re.fullmatch(r"/chats/([^/]+)(/messages)?", route[1] or "")
    chat_allowed = (
        route in (("GET", "/chats"), ("POST", "/chats"))
        or chat_route
        and (
            route[0] == "GET"
            and not chat_route.group(2)
            or route[0] == "POST"
            and chat_route.group(2)
        )
    )
    if (
        not chat_allowed
        and not answer_route
        and route
        not in (
            ("GET", "/session"),
            ("POST", "/reviews"),
            ("GET", "/profile"),
            ("GET", "/reminders"),
            ("POST", "/reminders"),
            ("POST", "/onboarding"),
        )
    ):
        return _response(404, {"error": {"code": "not_found", "message": "Not found"}})
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    user_id = claims.get("sub")
    if (
        not isinstance(user_id, str)
        or not user_id
        or claims.get("token_use") != "access"
    ):
        return _response(
            401,
            {
                "error": {
                    "code": "unauthorized",
                    "message": "Verified Cognito access token required",
                }
            },
        )
    try:
        repository = Repository(
            boto3.client("dynamodb"), os.environ["HISTORY_TABLE_NAME"]
        )
        now = datetime.now(UTC)
        if route == ("GET", "/chats"):
            result = chats.list_chats(repository, f"USER#{user_id}")
        elif route == ("POST", "/chats"):
            result = chats.create_chat(repository, f"USER#{user_id}", _body(event), now)
        elif chat_allowed and chat_route:
            if route[0] == "GET":
                result = chats.get_chat(
                    repository, f"USER#{user_id}", chat_route.group(1), now
                )
            else:
                result = chats.send(
                    repository,
                    f"USER#{user_id}",
                    chat_route.group(1),
                    _body(event),
                    now,
                )
        elif route == ("GET", "/reminders"):
            result = public_settings(repository, f"USER#{user_id}")
        elif route == ("POST", "/reminders"):
            result = save_settings(repository, f"USER#{user_id}", _body(event), now)
        elif route == ("GET", "/profile"):
            result = {
                "profile": public_profile(repository.get(f"USER#{user_id}", "PROFILE")),
                "decks": catalog(),
            }
        elif route == ("POST", "/onboarding"):
            result = {
                "profile": enroll(repository, f"USER#{user_id}", _body(event), now)
            }
        elif answer_route:
            card_id = answer_route.group(1)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", card_id):
                raise ValueError("Invalid cardId")
            result = LearningService(repository).answer(f"USER#{user_id}", card_id)
        elif route[0] == "GET":
            params = event.get("queryStringParameters") or {}
            profile = repository.get(f"USER#{user_id}", "PROFILE")
            goal = int(profile["dailyGoal"]) if profile else None
            cards = repository.session(
                f"USER#{user_id}",
                timestamp(now),
                _limit(params, "reviewLimit", goal or 20, 50),
                _limit(params, "newLimit", goal or 3, 10),
                consistent=bool(
                    profile
                    and (
                        now - datetime.fromisoformat(profile["createdAt"])
                    ).total_seconds()
                    < 60
                ),
            )
            if goal:
                cards = cards[:goal]
            result = {
                "cards": cards,
                "nextReviewAt": repository.next_review_at(f"USER#{user_id}"),
            }
        else:
            result = LearningService(repository).review(
                f"USER#{user_id}", _review_request(event), now
            )
        logger.info(json.dumps({"route": route[1], "status": 200}))
        return _response(200, result)
    except (ValueError, UnicodeError) as error:
        status, code, message = 400, "invalid_request", str(error)
    except Conflict as error:
        status, code, message = 409, "conflict", str(error)
    except NotFound as error:
        status, code, message = 404, "not_found", str(error)
    except ClientError:
        status, code, message = (
            503,
            "unavailable",
            "Storage unavailable; retry the request",
        )
    logger.info(json.dumps({"route": route[1], "status": status, "code": code}))
    return _response(status, {"error": {"code": code, "message": message}})
