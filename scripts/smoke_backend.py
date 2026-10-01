"""Exercise a deployed development stack with disposable Cognito/DynamoDB data."""

import argparse
import json
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "handler"))
from models import Card  # noqa: E402
from repository import Repository  # noqa: E402


def outputs_for(session, stack_name):
    stack = session.client("cloudformation").describe_stacks(StackName=stack_name)[
        "Stacks"
    ][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}


def http(api, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        api + path,
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def run(session, stack_name, *, browser=False):
    outputs = outputs_for(session, stack_name)
    if outputs.get("Stage") != "dev" or stack_name != "Lexikhan-dev":
        raise ValueError("Smoke tests only run against Lexikhan-dev with Stage=dev")
    cognito = session.client("cognito-idp")
    ddb = session.client("dynamodb")
    pool, client_id = outputs["UserPoolId"], outputs["UserPoolClientId"]
    table = outputs["HistoryTableName"]
    arn = ddb.describe_table(TableName=table)["Table"]["TableArn"]
    tags = ddb.list_tags_of_resource(ResourceArn=arn)["Tags"]
    if {tag["Key"]: tag["Value"] for tag in tags}.get("Stage") != "dev":
        raise ValueError("Refusing to write to a table without Stage=dev")
    username = f"smoke-{uuid4()}@example.invalid"
    password = secrets.token_urlsafe(32) + "aA1!"
    user_id = None
    created = False
    repository = Repository(ddb, table)
    api = outputs["ApiUrl"]
    try:
        user = cognito.admin_create_user(
            UserPoolId=pool,
            Username=username,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": username},
                {"Name": "email_verified", "Value": "true"},
            ],
        )["User"]
        created = True
        sub = next(a["Value"] for a in user["Attributes"] if a["Name"] == "sub")
        user_id = f"USER#{sub}"
        cognito.admin_set_user_password(
            UserPoolId=pool, Username=username, Password=password, Permanent=True
        )
        tokens = cognito.admin_initiate_auth(
            UserPoolId=pool,
            ClientId=client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )["AuthenticationResult"]
        token = tokens["AccessToken"]
        assert http(api, "/health")[0] == 200
        assert http(api, "/session")[0] == 401
        assert http(api, "/reviews", body={})[0] == 401
        assert http(api, "/session", "invalid-token")[0] == 401
        assert http(api, "/session", tokens["IdToken"])[0] in (401, 403)
        repository.create_card(
            Card.new(user_id, "smoke", "Hello", "Bonjour", datetime.now(UTC))
        )
        for _ in range(30):
            status, result = http(api, "/session", token)
            assert status == 200, f"Session request failed with {status}"
            if result["cards"]:
                break
            time.sleep(1)
        else:
            raise AssertionError("Seeded card did not appear in due-index")
        card = result["cards"][0]
        assert card["cardId"] == "smoke" and "answer" not in card
        assert http(api, "/cards/smoke/answer")[0] == 401
        status, answer = http(api, "/cards/smoke/answer", token)
        assert status == 200 and answer["answer"] == "Bonjour"
        assert http(api, "/cards/missing/answer", token)[0] == 404
        payload = {
            "reviewId": str(uuid4()),
            "cardId": "smoke",
            "version": card["version"],
            "rating": "EASY",
        }
        status, result = http(api, "/reviews", token, payload)
        assert status == 200 and result["version"] == 2
        assert http(api, "/reviews", token, payload) == (200, result)
        assert (
            http(api, "/reviews", token, {**payload, "reviewId": str(uuid4())})[0]
            == 409
        )
        persisted = repository.get(user_id, "CARD#smoke")
        assert persisted["version"] == 2 and persisted["dueAt"] == result["nextDueAt"]
        assert (
            repository.get(user_id, f"REVIEW#{payload['reviewId']}")["result"] == result
        )
        assert http(api, "/session", token)[1]["cards"] == []
        if browser:
            assert http(api, "/profile")[0] == 401
            assert http(api, "/onboarding", body={})[0] == 401
            assert http(api, "/profile", token)[1]["profile"] is None
            subprocess.run(
                [
                    "node",
                    str(
                        Path(__file__).resolve().parents[1]
                        / "web/scripts/smoke-deployed.mjs"
                    ),
                ],
                input=json.dumps(
                    {
                        "url": outputs["WebUrl"],
                        "username": username,
                        "password": password,
                    }
                ),
                text=True,
                check=True,
                timeout=180,
            )
            settings = {
                "deckId": "ur-en-v1",
                "learningLanguage": "ur",
                "baseLanguage": "en",
                "timezone": "America/Toronto",
                "dailyGoal": 3,
            }
            profile = http(api, "/profile", token)[1]["profile"]
            assert all(profile[key] == value for key, value in settings.items())
            assert http(api, "/onboarding", token, settings) == (
                200,
                {"profile": profile},
            )
            for number in range(1, 4):
                assert (
                    repository.get(user_id, f"CARD#ur-en-v1-{number:02}")["version"]
                    == 2
                )
            assert repository.get(user_id, "CARD#ur-en-v1-04")["version"] == 1
        print(
            "PASS: authenticated session, reveal, review, retry, conflict, "
            "persisted schedule, anonymous rejection"
        )
    finally:
        # Delete only the partition owned by this generated test user.
        try:
            if user_id:
                resource = session.resource("dynamodb").Table(table)
                cursor = {}
                while True:
                    page = resource.query(
                        KeyConditionExpression="userId = :u",
                        ExpressionAttributeValues={":u": user_id},
                        ConsistentRead=True,
                        **cursor,
                    )
                    with resource.batch_writer() as batch:
                        for item in page["Items"]:
                            batch.delete_item(
                                Key={"userId": user_id, "itemId": item["itemId"]}
                            )
                    if "LastEvaluatedKey" not in page:
                        break
                    cursor = {"ExclusiveStartKey": page["LastEvaluatedKey"]}
        finally:
            if created:
                cognito.admin_delete_user(UserPoolId=pool, Username=username)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="Lexikhan-dev", choices=["Lexikhan-dev"])
    parser.add_argument("--profile")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Also verify the deployed web UI through hosted sign-in",
    )
    parser.add_argument("--region", default="us-east-2")
    args = parser.parse_args()
    run(
        boto3.Session(profile_name=args.profile, region_name=args.region),
        args.stack,
        browser=args.browser,
    )
