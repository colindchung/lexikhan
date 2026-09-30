from aws_cdk import App
from aws_cdk.assertions import Match, Template

from infra.application_stack import ApplicationStack
from infra.config import StageConfig


def synthesize_template(*, protect_history: bool = False) -> Template:
    app = App()
    stack = ApplicationStack(
        app,
        "TestStack",
        stage_name="test",
        config=StageConfig(
            region="us-east-2",
            schedule_expression="rate(1 hour)",
            protect_history=protect_history,
        ),
    )
    return Template.from_stack(stack)


def test_stack_contains_worker_api_and_schedule():
    template = synthesize_template()

    template.resource_count_is("AWS::Lambda::Function", 1)
    template.resource_count_is("AWS::ApiGatewayV2::Api", 1)
    template.resource_count_is("AWS::ApiGatewayV2::Route", 4)
    template.resource_count_is("AWS::Scheduler::Schedule", 1)
    template.resource_count_is("AWS::SQS::Queue", 1)
    template.resource_count_is("AWS::CloudWatch::Alarm", 2)
    template.resource_count_is("AWS::DynamoDB::Table", 1)
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [
                {"AttributeName": "userId", "KeyType": "HASH"},
                {"AttributeName": "itemId", "KeyType": "RANGE"},
            ],
            "SSESpecification": {"SSEEnabled": True},
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": {
                    "HISTORY_TABLE_NAME": {"Ref": Match.any_value()},
                    "STAGE": "test",
                }
            }
        },
    )
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": Match.array_with(
                                    ["dynamodb:GetItem", "dynamodb:PutItem"]
                                ),
                                "Effect": "Allow",
                            }
                        )
                    ]
                )
            }
        },
    )
    template.has_resource_properties(
        "AWS::Scheduler::Schedule",
        {
            "ScheduleExpression": "rate(1 hour)",
            "State": "DISABLED",
            "Target": {
                "RetryPolicy": {
                    "MaximumEventAgeInSeconds": 3600,
                    "MaximumRetryAttempts": 2,
                },
                "DeadLetterConfig": {"Arn": Match.any_value()},
            },
        },
    )


def test_protected_history_is_retained_and_recoverable():
    template = synthesize_template(protect_history=True)

    template.has_resource(
        "AWS::DynamoDB::Table",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
            "Properties": {
                "DeletionProtectionEnabled": True,
                "PointInTimeRecoverySpecification": {
                    "PointInTimeRecoveryEnabled": True,
                },
            },
        },
    )


def test_learning_routes_are_protected_and_index_is_sparse():
    template = synthesize_template()
    for route in ("GET /session", "GET /cards/{cardId}/answer", "POST /reviews"):
        template.has_resource_properties(
            "AWS::ApiGatewayV2::Route",
            {"RouteKey": route, "AuthorizationType": "AWS_IAM"},
        )
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Route",
        {"RouteKey": "GET /health", "AuthorizationType": "NONE"},
    )
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "due-index",
                    "Projection": {"ProjectionType": "ALL"},
                    "KeySchema": [
                        {"AttributeName": "dueUserId", "KeyType": "HASH"},
                        {"AttributeName": "dueAt", "KeyType": "RANGE"},
                    ],
                }
            ]
        },
    )
