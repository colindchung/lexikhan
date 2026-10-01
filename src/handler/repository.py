"""DynamoDB persistence with atomic card updates and durable idempotency."""

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError
from models import Card, Review


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class Repository:
    def __init__(self, client, table_name: str):
        self.client = client
        self.table_name = table_name

    @staticmethod
    def encode(item: dict) -> dict:
        return {k: TypeSerializer().serialize(v) for k, v in item.items()}

    @staticmethod
    def decode(item: dict) -> dict:
        return {k: TypeDeserializer().deserialize(v) for k, v in item.items()}

    def get(self, user_id: str, item_id: str) -> dict | None:
        response = self.client.get_item(
            TableName=self.table_name,
            ConsistentRead=True,
            Key=self.encode({"userId": user_id, "itemId": item_id}),
        )
        return self.decode(response["Item"]) if "Item" in response else None

    def create_card(self, card: Card) -> bool:
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item=self.encode(card.item()),
                ConditionExpression="attribute_not_exists(itemId)",
            )
            return True
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def session(
        self,
        user_id: str,
        now: str,
        review_limit: int,
        new_limit: int,
        *,
        consistent: bool = False,
    ) -> list:
        reviews, new = [], []
        cursor = {}
        while True:
            query = {
                "IndexName": "due-index",
                "KeyConditionExpression": "dueUserId = :user AND dueAt <= :now",
                "ExpressionAttributeValues": self.encode(
                    {":user": user_id, ":now": now}
                ),
            }
            if consistent:
                # A just-enrolled deck must be visible before its GSI catches up.
                query = {
                    "ConsistentRead": True,
                    "KeyConditionExpression": (
                        "userId = :user AND begins_with(itemId, :card)"
                    ),
                    "ExpressionAttributeValues": self.encode(
                        {":user": user_id, ":card": "CARD#"}
                    ),
                }
            response = self.client.query(
                TableName=self.table_name,
                ScanIndexForward=True,
                **query,
                **cursor,
            )
            for raw in response["Items"]:
                indexed = self.decode(raw)
                # The GSI is eventually consistent. Recheck the authoritative record.
                item = self.get(user_id, indexed["itemId"])
                if not item or item["dueAt"] > now:
                    continue
                card = Card.from_item(item)
                target, limit = (
                    (new, new_limit) if card.state == "NEW" else (reviews, review_limit)
                )
                if consistent or len(target) < limit:
                    target.append(card)
            if (
                not consistent
                and len(reviews) >= review_limit
                and len(new) >= new_limit
                or "LastEvaluatedKey" not in response
            ):
                break
            cursor = {"ExclusiveStartKey": response["LastEvaluatedKey"]}
        return [
            c.public()
            for c in sorted(reviews, key=lambda c: (c.dueAt, c.itemId))[:review_limit]
            + sorted(new, key=lambda c: (c.dueAt, c.itemId))[:new_limit]
        ]

    def next_review_at(self, user_id: str) -> str | None:
        """Earliest scheduled learned card (new cards are not scheduled reviews)."""
        cursor = {}
        while True:
            page = self.client.query(
                TableName=self.table_name,
                IndexName="due-index",
                KeyConditionExpression="dueUserId = :user",
                ExpressionAttributeValues=self.encode({":user": user_id}),
                ScanIndexForward=True,
                Limit=50,
                **cursor,
            )
            for raw in page["Items"]:
                indexed = self.decode(raw)
                item = self.get(user_id, indexed["itemId"])
                if (
                    item
                    and item["state"] != "NEW"
                    and item["dueAt"] == indexed["dueAt"]
                ):
                    return item["dueAt"]
            if "LastEvaluatedKey" not in page:
                return None
            cursor = {"ExclusiveStartKey": page["LastEvaluatedKey"]}

    def saved_review(self, user_id: str, review_id: str, request: dict) -> dict | None:
        saved = self.get(user_id, f"REVIEW#{review_id}")
        if saved:
            if saved["request"] != request:
                raise Conflict("reviewId was already used for a different request")
            return saved["result"]
        return None

    def record_review(self, card: Card, review: Review, expected_version: int) -> dict:
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": self.encode(card.item()),
                            "ConditionExpression": "#version = :version",
                            "ExpressionAttributeNames": {"#version": "version"},
                            "ExpressionAttributeValues": self.encode(
                                {":version": expected_version}
                            ),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": self.encode(review.item()),
                            "ConditionExpression": "attribute_not_exists(itemId)",
                        }
                    },
                ]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            saved = self.saved_review(
                card.userId, review.itemId.removeprefix("REVIEW#"), review.request
            )
            if saved is not None:
                return saved
            reasons = error.response.get("CancellationReasons", [])
            if any(r["Code"] == "ConditionalCheckFailed" for r in reasons):
                raise Conflict("Card version is stale; refresh the session") from error
            raise
        return review.result
