import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "handler"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from repository import Repository  # noqa: E402


@pytest.fixture
def repository(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    monkeypatch.setenv("HISTORY_TABLE_NAME", "learning-test")
    with mock_aws():
        client = boto3.client("dynamodb")
        client.create_table(
            TableName="learning-test",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "userId", "KeyType": "HASH"},
                {"AttributeName": "itemId", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in (
                    "userId",
                    "itemId",
                    "dueUserId",
                    "dueAt",
                    "reminderGroup",
                    "nextReminderAt",
                )
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "reminder-index",
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                    "KeySchema": [
                        {"AttributeName": "reminderGroup", "KeyType": "HASH"},
                        {"AttributeName": "nextReminderAt", "KeyType": "RANGE"},
                    ],
                },
                {
                    "IndexName": "due-index",
                    "Projection": {"ProjectionType": "ALL"},
                    "KeySchema": [
                        {"AttributeName": "dueUserId", "KeyType": "HASH"},
                        {"AttributeName": "dueAt", "KeyType": "RANGE"},
                    ],
                },
            ],
            Tags=[{"Key": "Stage", "Value": "dev"}],
        )
        yield Repository(client, "learning-test")
