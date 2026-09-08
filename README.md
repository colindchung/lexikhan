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
cdk bootstrap aws://ACCOUNT_ID/us-west-2 --profile dev
cdk bootstrap aws://ACCOUNT_ID/us-west-2 --profile prod
```

Review and deploy development:

```bash
cdk diff -c stage=dev --profile dev
cdk deploy -c stage=dev --profile dev
```

Review and deploy production:

```bash
cdk diff -c stage=prod --profile prod
cdk deploy -c stage=prod --profile prod --require-approval broadening
```

For CI, pin the expected account and region so synthesis cannot accidentally
target the credentials for another account:

```bash
cdk deploy \
  -c stage=prod \
  -c account=ACCOUNT_ID \
  -c region=us-west-2 \
  --require-approval never
```

Use a short-lived CI role (for example, GitHub Actions OIDC) rather than stored
AWS access keys. Put secret values in AWS Secrets Manager or Systems Manager
Parameter Store, not in `infra/config.py` or Lambda environment configuration.

### GitHub Actions

The repository includes pull-request checks and a deployment workflow. A push
to `main` deploys `dev`; `prod` is deployed manually with the **Deploy**
workflow. Create GitHub environments named `dev` and `prod`, then configure
each with:

- Environment secret `AWS_DEPLOY_ROLE_ARN`: IAM role trusted for GitHub OIDC.
- Environment variable `AWS_ACCOUNT_ID`: expected 12-digit AWS account ID.
- Environment variable `AWS_REGION`: deployment region, such as `us-west-2`.

Protect the `prod` GitHub environment with required reviewers. The workflow
uses `allowed-account-ids` as an additional guard against deploying with
credentials for the wrong account.

## Repository map

- `app.py`: CDK entry point and deployment environment selection.
- `infra/application_stack.py`: deployable infrastructure unit.
- `infra/config.py`: non-secret stage configuration.
- `src/handler/main.py`: API and scheduler event adapter.
- `src/handler/service.py`: shared business logic.
- `tests/unit`: Lambda behavior tests.
- `tests/infrastructure`: synthesized-template assertions.
