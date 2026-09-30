"""Seed temporary English-to-French fixtures; never overwrite existing progress."""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "handler"))
from models import Card  # noqa: E402
from repository import Repository  # noqa: E402

DECK = [
    ("Hello", "Bonjour"),
    ("Thank you", "Merci"),
    ("See you tomorrow", "À demain"),
    ("Please", "S'il vous plaît"),
    ("I forgot my umbrella", "J'ai oublié mon parapluie"),
]


def seed(client, table: str, user_sub: str) -> int:
    metadata = client.describe_table(TableName=table)["Table"]
    tags = client.list_tags_of_resource(ResourceArn=metadata["TableArn"])["Tags"]
    if {t["Key"]: t["Value"] for t in tags}.get("Stage") != "dev":
        raise ValueError("Seeding requires a table tagged Stage=dev")
    repository = Repository(client, table)
    now = datetime.now(UTC)
    return sum(
        repository.create_card(
            Card.new(f"USER#{user_sub}", f"dev-{index}", prompt, answer, now)
        )
        for index, (prompt, answer) in enumerate(DECK, start=1)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True)
    parser.add_argument(
        "--user-sub",
        required=True,
        help="Cognito sub of the development learner",
    )
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-2")
    args = parser.parse_args()
    client = boto3.Session(profile_name=args.profile, region_name=args.region).client(
        "dynamodb"
    )
    print(f"Created {seed(client, args.table, args.user_sub)} development cards")


if __name__ == "__main__":
    main()
