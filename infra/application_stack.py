import json

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
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
from aws_cdk import aws_cognito as cognito
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import aws_lambda_destinations as destinations
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_scheduler as scheduler,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from aws_cdk.aws_apigatewayv2_authorizers import HttpJwtAuthorizer
from constructs import Construct

from infra.config import StageConfig
from infra.web_hosting import WebHosting


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
                RemovalPolicy.RETAIN if stage_name == "prod" else RemovalPolicy.DESTROY
            ),
        )

        history_table = dynamodb.Table(
            self,
            "HistoryTable",
            partition_key=dynamodb.Attribute(
                name="userId",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="itemId",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            deletion_protection=config.protect_history,
            point_in_time_recovery_specification=(
                dynamodb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=config.protect_history,
                )
            ),
            removal_policy=(
                RemovalPolicy.RETAIN
                if config.protect_history
                else RemovalPolicy.DESTROY
            ),
        )

        Tags.of(history_table).add("Stage", stage_name)
        history_table.add_global_secondary_index(
            index_name="due-index",
            partition_key=dynamodb.Attribute(
                name="dueUserId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="dueAt", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        history_table.add_global_secondary_index(
            index_name="reminder-index",
            partition_key=dynamodb.Attribute(
                name="reminderGroup", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="nextReminderAt", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY,
        )

        worker = lambda_.Function(
            self,
            "Worker",
            function_name=function_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="main.handler",
            code=lambda_.Code.from_asset("build/handler"),
            timeout=Duration.seconds(config.lambda_timeout_seconds),
            memory_size=config.lambda_memory_mb,
            tracing=lambda_.Tracing.ACTIVE,
            log_group=worker_logs,
            environment={
                "STAGE": stage_name,
                "HISTORY_TABLE_NAME": history_table.table_name,
            },
        )
        history_table.grant_read_write_data(worker)

        hosting = WebHosting(
            self,
            "Web",
            stage_name=stage_name,
            domain_name=config.web_domain_name,
            certificate_arn=config.web_certificate_arn,
        )
        origins = [hosting.url]
        if config.web_domain_name:
            origins.append(hosting.cloudfront_url)
        if stage_name == "dev":
            origins.append("http://localhost:5173")
        pool = cognito.UserPool(
            self,
            "Learners",
            user_pool_name=f"lexikhan-{stage_name}",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True)
            ),
            password_policy=cognito.PasswordPolicy(min_length=12),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN
            if stage_name == "prod"
            else RemovalPolicy.DESTROY,
        )
        Tags.of(pool).add("Stage", stage_name)
        client = pool.add_client(
            "Browser",
            generate_secret=False,
            prevent_user_existence_errors=True,
            auth_flows=cognito.AuthFlow(
                user_srp=True, admin_user_password=stage_name == "dev"
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.COGNITO_ADMIN,
                ],
                callback_urls=[f"{origin}/auth/callback" for origin in origins],
                logout_urls=[f"{origin}/" for origin in origins],
            ),
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
        )
        domain = pool.add_domain(
            "SignIn",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"lexikhan-{stage_name}-{self.account}"
            ),
        )
        authorizer = HttpJwtAuthorizer(
            "LearnerJwt",
            pool.user_pool_provider_url,
            jwt_audience=[client.user_pool_client_id],
        )

        api = apigwv2.HttpApi(
            self,
            "Api",
            api_name=f"lexikhan-{stage_name}",
            description=f"Lexikhan HTTP API ({stage_name})",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=origins,
                allow_methods=[apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.POST],
                allow_headers=["authorization", "content-type"],
                max_age=Duration.hours(1),
            ),
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
        for path, method in (
            ("/profile", apigwv2.HttpMethod.GET),
            ("/reminders", apigwv2.HttpMethod.GET),
            ("/reminders", apigwv2.HttpMethod.POST),
            ("/onboarding", apigwv2.HttpMethod.POST),
            ("/session", apigwv2.HttpMethod.GET),
            ("/cards/{cardId}/answer", apigwv2.HttpMethod.GET),
            ("/reviews", apigwv2.HttpMethod.POST),
        ):
            api.add_routes(
                path=path,
                methods=[method],
                integration=integration,
                authorizer=authorizer,
                authorization_scopes=["aws.cognito.signin.user.admin"],
            )

        schedule_dlq = sqs.Queue(
            self,
            "ScheduleDeadLetterQueue",
            queue_name=f"lexikhan-schedule-dlq-{stage_name}",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )

        reminder_worker = lambda_.Function(
            self,
            "ReminderWorker",
            function_name=f"lexikhan-reminders-{stage_name}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="sms_worker.handler",
            code=lambda_.Code.from_asset("build/handler"),
            timeout=Duration.seconds(60),
            memory_size=256,
            reserved_concurrent_executions=1,
            environment={
                "HISTORY_TABLE_NAME": history_table.table_name,
                "WEB_URL": hosting.url,
            },
            log_group=logs.LogGroup(
                self, "ReminderLogs", retention=logs.RetentionDays.ONE_MONTH
            ),
            retry_attempts=0,
            max_event_age=Duration.hours(1),
            on_failure=destinations.SqsDestination(schedule_dlq),
        )
        history_table.grant_read_write_data(reminder_worker)
        reminder_worker.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sns:Publish", "sns:CheckIfPhoneNumberIsOptedOut"],
                resources=["*"],
            )
        )
        reminder_worker.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "sms-voice:SendTextMessage",
                    "sms-voice:DescribeOptedOutNumbers",
                ],
                resources=["*"],
                conditions={"StringEquals": {"aws:CalledViaLast": "sns.amazonaws.com"}},
            )
        )
        cloudwatch.Alarm(
            self,
            "ReminderErrorsAlarm",
            alarm_name=f"lexikhan-reminder-errors-{stage_name}",
            metric=reminder_worker.metric_errors(period=Duration.minutes(5)),
            evaluation_periods=1,
            threshold=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
            description=f"Allows the {stage_name} schedule to invoke the worker",
        )
        reminder_worker.grant_invoke(scheduler_role)
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
            state="ENABLED" if config.reminders_enabled else "DISABLED",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=reminder_worker.function_arn,
                role_arn=scheduler_role.role_arn,
                input=json.dumps(
                    {
                        "source": "lexikhan.reminders",
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

        CfnOutput(self, "ReminderFunctionName", value=reminder_worker.function_name)
        CfnOutput(self, "Stage", value=stage_name)
        CfnOutput(self, "UserPoolId", value=pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=client.user_pool_client_id)
        CfnOutput(self, "AuthDomain", value=domain.base_url())
        CfnOutput(self, "AuthAuthority", value=pool.user_pool_provider_url)
        CfnOutput(self, "WebUrl", value=hosting.url)
        CfnOutput(self, "CloudFrontUrl", value=hosting.cloudfront_url)
        CfnOutput(self, "WebBucketName", value=hosting.bucket.bucket_name)
        CfnOutput(self, "DistributionId", value=hosting.distribution.distribution_id)
        CfnOutput(self, "ApiUrl", value=api.api_endpoint)
        CfnOutput(self, "HistoryTableName", value=history_table.table_name)
        CfnOutput(self, "WorkerFunctionName", value=worker.function_name)
        CfnOutput(self, "ScheduleDeadLetterQueueUrl", value=schedule_dlq.queue_url)
