"""HTTP adapter. Learning routes require API Gateway's verified IAM context."""

import base64
import json
import logging
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from models import timestamp
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


def _review_request(event: dict) -> dict:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body, validate=True).decode("utf-8")
    if len(body) > 16000:
        raise ValueError("Request body is too large")
    request = json.loads(body)
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
    if route not in (("GET", "/session"), ("POST", "/reviews")):
        return _response(404, {"error": {"code": "not_found", "message": "Not found"}})
    # IAM is a temporary bridge until Cognito/JWT lands. Never accept client user IDs.
    identity = event.get("requestContext", {}).get("authorizer", {}).get("iam", {})
    user_id = identity.get("userArn")
    if not isinstance(user_id, str) or not user_id:
        return _response(
            401,
            {
                "error": {
                    "code": "unauthorized",
                    "message": "Verified IAM identity required",
                }
            },
        )
    try:
        repository = Repository(
            boto3.client("dynamodb"), os.environ["HISTORY_TABLE_NAME"]
        )
        now = datetime.now(UTC)
        if route[0] == "GET":
            params = event.get("queryStringParameters") or {}
            cards = repository.session(
                f"USER#{user_id}",
                timestamp(now),
                _limit(params, "reviewLimit", 20, 50),
                _limit(params, "newLimit", 3, 10),
            )
            result = {"cards": cards}
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
