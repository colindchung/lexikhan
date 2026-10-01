from dataclasses import dataclass


@dataclass(frozen=True)
class StageConfig:
    region: str
    schedule_expression: str
    lambda_memory_mb: int = 512
    lambda_timeout_seconds: int = 30
    protect_history: bool = False
    reminders_enabled: bool = False
    web_domain_name: str | None = None
    web_certificate_arn: str | None = None


# Non-secret, environment-specific configuration belongs here so a given commit
# synthesizes a predictable template for each stage.
STAGE_CONFIG: dict[str, StageConfig] = {
    "dev": StageConfig(
        region="us-east-2",
        schedule_expression="rate(1 hour)",
    ),
    "prod": StageConfig(
        region="us-east-2",
        schedule_expression="rate(5 minutes)",
        reminders_enabled=True,
        protect_history=True,
        web_domain_name="lexikhan.colindchung.com",
        web_certificate_arn=(
            "arn:aws:acm:us-east-1:437755619780:certificate/"
            "3a3472f1-a9fb-4d1d-9066-f9b1012efee6"
        ),
    ),
}
