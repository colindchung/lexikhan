"""Bind an AWS sandbox-verified phone to a Cognito account; never sends an SMS.

Run after verifying the destination in the AWS SNS console. This administrative
binding is required even after leaving the sandbox. It does not opt the user in:
the learner must enable reminders in the app.
"""

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/handler"))
from models import timestamp  # noqa: E402
from repository import Repository  # noqa: E402


def approve(session, stage, email, phone):
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
        raise ValueError("Phone must be in E.164 format, e.g. +14165550123")
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
    sns = session.client("sns")
    verified = []
    cursor = {}
    while True:
        page = sns.list_sms_sandbox_phone_numbers(**cursor)
        verified.extend(
            p["PhoneNumber"] for p in page["PhoneNumbers"] if p["Status"] == "Verified"
        )
        if not page.get("NextToken"):
            break
        cursor = {"NextToken": page["NextToken"]}
    if phone not in verified:
        raise ValueError("Verify this destination in the SNS SMS sandbox first")
    if sns.check_if_phone_number_is_opted_out(phoneNumber=phone)["isOptedOut"]:
        raise ValueError("Recipient is opted out; approval will not override STOP")
    repository = Repository(session.client("dynamodb"), outputs["HistoryTableName"])
    user_id = f"USER#{attrs['sub']}"
    # Unique number ownership stops multiple public accounts sharing one allowance.
    repository.client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": repository.table_name,
                    "Item": repository.encode(
                        {
                            "userId": f"SMS_PHONE#{phone}",
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
                            "itemId": "SMS_ACCESS",
                            "approved": True,
                            "phone": phone,
                            "approvedAt": timestamp(datetime.now(UTC)),
                        }
                    ),
                    "ConditionExpression": (
                        "attribute_not_exists(itemId) OR phone = :phone"
                    ),
                    "ExpressionAttributeValues": repository.encode({":phone": phone}),
                }
            },
        ]
    )
    print(
        "SMS number approved. Sign in and enable reminders; no message has been sent."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["dev", "prod"], required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--profile", default="personal")
    parser.add_argument("--region", default="us-east-2")
    args = parser.parse_args()
    approve(
        boto3.Session(profile_name=args.profile, region_name=args.region),
        args.stage,
        args.email,
        args.phone,
    )
