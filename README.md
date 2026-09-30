# Lexikhan

Lexikhan is a Python AWS CDK application that deploys an HTTP API and an
EventBridge Scheduler schedule backed by the same Lambda function.

## Architecture

- `GET /health` reports service health.
- `POST /run` invokes the application operation through API Gateway HTTP API.
- EventBridge Scheduler invokes the same operation on a stage-specific interval.
- Scheduler retries failed delivery twice and sends exhausted deliveries to an
  encrypted SQS dead-letter queue.
- CloudWatch Logs are retained for one month.
- CloudWatch alarms detect worker errors and messages in the dead-letter queue.
- DynamoDB stores delivery history by `userId` and `itemId` using on-demand
  billing and AWS-managed encryption.

Production enables DynamoDB deletion protection, point-in-time recovery, and a
retain-on-delete policy. Development leaves those protections off so ephemeral
stacks can be removed cleanly.

The handler contains only trigger-specific adaptation. Shared application logic
lives in `src/handler/service.py`; split the API and scheduler into separate
Lambda handlers if their workflows diverge.

## Local setup

Python 3.12 or newer and the AWS CDK CLI are required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm install --global aws-cdk
```

Run checks:

```bash
ruff check .
pytest
cdk synth -c stage=dev
```

## Deployments

Stage configuration is committed in `infra/config.py`. The default stage is
`dev`; valid stages are `dev` and `prod`.

Bootstrap each target account and region once:

```bash
cdk bootstrap \
  aws://ACCOUNT_ID/us-east-2 \
  --profile personal \
  --termination-protection
```

Review and deploy development:

```bash
cdk diff -c stage=dev --profile personal
cdk deploy -c stage=dev --profile personal
```

Review and deploy production:

```bash
cdk diff -c stage=prod --profile personal
cdk deploy -c stage=prod --profile personal --require-approval broadening
```

For CI, pin the expected account and region so synthesis cannot accidentally
target the credentials for another account:

```bash
cdk deploy \
  -c stage=prod \
  -c account=ACCOUNT_ID \
  -c region=us-east-2 \
  --require-approval never
```

Put application secret values in AWS Secrets Manager or Systems Manager
Parameter Store, not in `infra/config.py` or Lambda environment configuration.

### GitHub Actions

The repository includes pull-request checks and a deployment workflow. Every
push to `main`, including a merged pull request, deploys all stacks in the
`prod` CDK stage. The workflow can also be run manually from the Actions page.

In **Settings → Environments**, create a GitHub environment named `personal`.
Configure these environment secrets:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Configure these environment variables:

- `AWS_ACCOUNT_ID`: expected 12-digit personal AWS account ID
- `AWS_REGION`: deployment region, set to `us-east-2`

The keys must belong to an IAM principal in the personal account, never the
root user. The workflow validates the account ID before deploying, serializes
deployments, runs lint and tests again, and deploys every stack with
`cdk deploy --all`.

In **Settings → Rules**, add a branch ruleset for `main` that requires pull
requests and requires the `test` job from the **CI** workflow to pass. Do not
add a required reviewer to the `personal` environment if deployment should
begin immediately after a merge.

Static access keys are supported, but GitHub OIDC with a short-lived AWS role
is the preferred long-term setup. Rotate or revoke these keys if they are ever
exposed, and migrate the workflow to OIDC when convenient.

## Repository map

- `docs/IMPLEMENTATION_PLAN.md`: product architecture and sequenced MVP plan.
- `app.py`: CDK entry point and deployment environment selection.
- `infra/application_stack.py`: deployable infrastructure unit.
- `infra/config.py`: non-secret stage configuration.
- `src/handler/main.py`: API and scheduler event adapter.
- `src/handler/service.py`: shared business logic.
- `tests/unit`: Lambda behavior tests.
- `tests/infrastructure`: synthesized-template assertions.
