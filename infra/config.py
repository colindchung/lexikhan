from dataclasses import dataclass


@dataclass(frozen=True)
class StageConfig:
    region: str
    schedule_expression: str
    lambda_memory_mb: int = 512
    lambda_timeout_seconds: int = 30
    protect_history: bool = False


# Non-secret, environment-specific configuration belongs here so a given commit
# synthesizes a predictable template for each stage.
STAGE_CONFIG: dict[str, StageConfig] = {
    "dev": StageConfig(
        region="us-west-2",
        schedule_expression="rate(1 hour)",
    ),
    "prod": StageConfig(
        region="us-west-2",
        schedule_expression="rate(15 minutes)",
        protect_history=True,
    ),
}
