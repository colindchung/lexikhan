"""Bind an SES-verified email to its verified Cognito account.

Does not opt in or send; the learner enables reminders in the app.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/handler"))
from models import timestamp  # noqa: E402
from repository import Repository  # noqa: E402


def approve(session, stage, email):
    outputs = {
        v["OutputKey"]: v["OutputValue"]
        for v in session.client("cloudformation").describe_stacks(
            StackName=f"Lexikhan-{stage}"
        )["Stacks"][0]["Outputs"]
    }
    user = session.client("cognito-idp").admin_get_user(
        UserPoolId=outputs["UserPoolId"], Username=email
    )
    attrs = {a["Name"]: a["Value"] for a in user["UserAttributes"]}
    if attrs.get("email_verified") != "true" or user["UserStatus"] != "CONFIRMED":
        raise ValueError("The Cognito account must have a verified email")
    if attrs.get("email", "").lower() != email.lower():
        raise ValueError("Recipient must match the verified Cognito account email")
    email = attrs["email"]
    identity = session.client("sesv2").get_email_identity(EmailIdentity=email)
    if not identity.get("VerifiedForSendingStatus"):
        raise ValueError("Verify this destination in SES first")
    repository = Repository(session.client("dynamodb"), outputs["HistoryTableName"])
    user_id = f"USER#{attrs['sub']}"
    # Unique email ownership stops multiple public accounts sharing one allowance.
    repository.client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": repository.table_name,
                    "Item": repository.encode(
                        {
                            "userId": f"EMAIL_ADDRESS#{email}",
                            "itemId": "OWNER",
                            "owner": user_id,
                        }
                    ),
                    "ConditionExpression": (
                        "attribute_not_exists(itemId) OR #o = :owner"
                    ),
                    "ExpressionAttributeNames": {"#o": "owner"},
                    "ExpressionAttributeValues": repository.encode({":owner": user_id}),
                }
            },
            {
                "Put": {
                    "TableName": repository.table_name,
                    "Item": repository.encode(
                        {
                            "userId": user_id,
                            "itemId": "EMAIL_ACCESS",
                            "approved": True,
                            "email": email,
                            "approvedAt": timestamp(datetime.now(UTC)),
                        }
                    ),
                    "ConditionExpression": (
                        "attribute_not_exists(itemId) OR email = :email"
                    ),
                    "ExpressionAttributeValues": repository.encode({":email": email}),
                }
            },
        ]
    )
    print(
        "Email address approved. Sign in and enable reminders; "
        "no message has been sent."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["dev", "prod"], required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--profile", default="personal")
    parser.add_argument("--region", default="us-east-2")
    args = parser.parse_args()
    approve(
        boto3.Session(profile_name=args.profile, region_name=args.region),
        args.stage,
        args.email,
    )
