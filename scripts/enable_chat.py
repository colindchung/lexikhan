"""Enable private paid chat for one verified Cognito account; no model call."""

import argparse
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/handler"))
from repository import Repository  # noqa: E402
from smoke_backend import outputs_for  # noqa: E402


def enable(session, stage, email):
    outputs = outputs_for(session, f"Lexikhan-{stage}")
    if outputs.get("Stage") != stage:
        raise ValueError("Stage mismatch")
    user = session.client("cognito-idp").admin_get_user(
        UserPoolId=outputs["UserPoolId"], Username=email
    )
    attrs = {a["Name"]: a["Value"] for a in user["UserAttributes"]}
    if user["UserStatus"] != "CONFIRMED" or attrs.get("email_verified") != "true":
        raise ValueError("Verified account required")
    repository = Repository(session.client("dynamodb"), outputs["HistoryTableName"])
    repository.client.put_item(
        TableName=repository.table_name,
        Item=repository.encode(
            {
                "userId": f"USER#{attrs['sub']}",
                "itemId": "CHAT_ACCESS",
                "approved": True,
            }
        ),
    )
    print(f"Private chat enabled in {stage}; no model request sent.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["dev", "prod"], required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--profile", default="personal")
    parser.add_argument("--region", default="us-east-2")
    args = parser.parse_args()
    enable(
        boto3.Session(profile_name=args.profile, region_name=args.region),
        args.stage,
        args.email,
    )
