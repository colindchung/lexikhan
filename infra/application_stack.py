import json

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_apigatewayv2 as apigwv2,
)
from aws_cdk import (
    aws_apigatewayv2_integrations as integrations,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_scheduler as scheduler,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from constructs import Construct

from infra.config import StageConfig


class ApplicationStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        stage_name: str,
        config: StageConfig,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        function_name = f"lexikhan-worker-{stage_name}"
        worker_logs = logs.LogGroup(
            self,
            "WorkerLogs",
            log_group_name=f"/aws/lambda/{function_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=(
                RemovalPolicy.RETAIN
                if stage_name == "prod"
                else RemovalPolicy.DESTROY
            ),
        )

        worker = lambda_.Function(
            self,
            "Worker",
            function_name=function_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="main.handler",
            code=lambda_.Code.from_asset("src/handler"),
            timeout=Duration.seconds(config.lambda_timeout_seconds),
            memory_size=config.lambda_memory_mb,
            tracing=lambda_.Tracing.ACTIVE,
            log_group=worker_logs,
            environment={"STAGE": stage_name},
        )

        api = apigwv2.HttpApi(
            self,
            "Api",
            api_name=f"lexikhan-{stage_name}",
            description=f"Lexikhan HTTP API ({stage_name})",
        )
        integration = integrations.HttpLambdaIntegration(
            "WorkerIntegration",
            worker,
        )
        api.add_routes(
            path="/health",
            methods=[apigwv2.HttpMethod.GET],
            integration=integration,
        )
        api.add_routes(
            path="/run",
            methods=[apigwv2.HttpMethod.POST],
            integration=integration,
        )

        schedule_dlq = sqs.Queue(
            self,
            "ScheduleDeadLetterQueue",
            queue_name=f"lexikhan-schedule-dlq-{stage_name}",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )

        scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
            description=f"Allows the {stage_name} schedule to invoke the worker",
        )
        worker.grant_invoke(scheduler_role)
        schedule_dlq.grant_send_messages(scheduler_role)

        scheduler.CfnSchedule(
            self,
            "WorkerSchedule",
            name=f"lexikhan-worker-{stage_name}",
            description=f"Runs the Lexikhan worker for {stage_name}",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF"
            ),
            schedule_expression=config.schedule_expression,
            state="ENABLED",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=worker.function_arn,
                role_arn=scheduler_role.role_arn,
                input=json.dumps(
                    {
                        "source": "scheduler",
                        "stage": stage_name,
                    }
                ),
                dead_letter_config=scheduler.CfnSchedule.DeadLetterConfigProperty(
                    arn=schedule_dlq.queue_arn
                ),
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_event_age_in_seconds=3600,
                    maximum_retry_attempts=2,
                ),
            ),
        )

        cloudwatch.Alarm(
            self,
            "WorkerErrorsAlarm",
            alarm_name=f"lexikhan-worker-errors-{stage_name}",
            alarm_description="The Lexikhan worker returned an error.",
            metric=worker.metric_errors(period=Duration.minutes(5)),
            evaluation_periods=1,
            threshold=1,
            comparison_operator=(
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD
            ),
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        cloudwatch.Alarm(
            self,
            "ScheduleDeadLettersAlarm",
            alarm_name=f"lexikhan-schedule-dead-letters-{stage_name}",
            alarm_description="A scheduled invocation reached the dead-letter queue.",
            metric=schedule_dlq.metric_approximate_number_of_messages_visible(
                period=Duration.minutes(5)
            ),
            evaluation_periods=1,
            threshold=1,
            comparison_operator=(
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD
            ),
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        CfnOutput(self, "ApiUrl", value=api.api_endpoint)
        CfnOutput(self, "WorkerFunctionName", value=worker.function_name)
        CfnOutput(self, "ScheduleDeadLetterQueueUrl", value=schedule_dlq.queue_url)
