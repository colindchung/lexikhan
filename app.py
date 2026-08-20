#!/usr/bin/env python3

import os

from aws_cdk import App, Environment

from infra.application_stack import ApplicationStack
from infra.config import STAGE_CONFIG

app = App()
stage_name = app.node.try_get_context("stage") or "dev"

if stage_name not in STAGE_CONFIG:
    valid_stages = ", ".join(sorted(STAGE_CONFIG))
    raise ValueError(f"Unknown stage {stage_name!r}. Expected one of: {valid_stages}")

config = STAGE_CONFIG[stage_name]
account = app.node.try_get_context("account") or os.getenv("CDK_DEFAULT_ACCOUNT")
region = (
    app.node.try_get_context("region")
    or os.getenv("CDK_DEFAULT_REGION")
    or config.region
)

ApplicationStack(
    app,
    f"Lexikhan-{stage_name}",
    stage_name=stage_name,
    config=config,
    env=Environment(account=account, region=region),
    description=f"Lexikhan API and scheduled worker ({stage_name})",
)

app.synth()
