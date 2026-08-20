from aws_cdk import App
from aws_cdk.assertions import Match, Template

from infra.application_stack import ApplicationStack
from infra.config import StageConfig


def synthesize_template() -> Template:
    app = App()
    stack = ApplicationStack(
        app,
        "TestStack",
        stage_name="test",
        config=StageConfig(
            region="us-west-2",
            schedule_expression="rate(1 hour)",
        ),
    )
    return Template.from_stack(stack)


def test_stack_contains_worker_api_and_schedule():
    template = synthesize_template()

    template.resource_count_is("AWS::Lambda::Function", 1)
    template.resource_count_is("AWS::ApiGatewayV2::Api", 1)
    template.resource_count_is("AWS::ApiGatewayV2::Route", 2)
    template.resource_count_is("AWS::Scheduler::Schedule", 1)
    template.resource_count_is("AWS::SQS::Queue", 1)
    template.resource_count_is("AWS::CloudWatch::Alarm", 2)
    template.has_resource_properties(
        "AWS::Scheduler::Schedule",
        {
            "ScheduleExpression": "rate(1 hour)",
            "Target": {
                "RetryPolicy": {
                    "MaximumEventAgeInSeconds": 3600,
                    "MaximumRetryAttempts": 2,
                },
                "DeadLetterConfig": {"Arn": Match.any_value()},
            },
        },
    )
