# Lexikhan

Lexikhan is a Python AWS CDK application that deploys an HTTP API and an
EventBridge Scheduler schedule backed by the same Lambda function.

## Architecture

- `GET /health` reports service health.
- `GET /session` returns due prompts, with reviews before new cards.
- `POST /reviews` atomically records a review and its FSRS schedule.
- Learning routes require AWS SigV4/IAM authentication; `/run` is removed.
- EventBridge Scheduler is disabled in every stage until reminders exist.
- Scheduler retries failed delivery twice and sends exhausted deliveries to an
  encrypted SQS dead-letter queue.
- CloudWatch Logs are retained for one month.
- CloudWatch alarms detect worker errors and messages in the dead-letter queue.
- DynamoDB stores cards and append-only review events by `userId` and `itemId` using on-demand
  billing and AWS-managed encryption.

Production enables DynamoDB deletion protection, point-in-time recovery, and a
retain-on-delete policy. Development leaves those protections off so ephemeral
stacks can be removed cleanly.

The HTTP adapter lives in `src/handler/main.py`, FSRS scheduling in
`src/handler/service.py`, and transactional persistence in `src/handler/repository.py`.
The sparse `due-index` contains only cards. Review IDs are unique per learner;
identical retries return the stored result, conflicting reuse or stale versions
return HTTP 409.

## Local setup

Python 3.12 or newer and the AWS CDK CLI are required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
npm install --global aws-cdk
```

Build the Lambda asset before tests, synthesis, or deployment (and rebuild after
handler edits). The build bundles pinned FSRS and typing-extensions wheels for
Python 3.12/ARM64; boto3 is supplied by the Lambda runtime.

```bash
python scripts/build_lambda.py
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

## Development learning flow

Cognito is a later phase. For now, sign learning requests with AWS SigV4 for
service `execute-api`, region `us-east-2`; the caller needs `execute-api:Invoke`
on the learning routes. API Gateway rejects unsigned requests. The handler uses
only the verified `requestContext.authorizer.iam.userArn`, prefixed with `USER#`.
Use a stable IAM user for temporary development data: assumed-role session ARNs
can change between sessions. Cognito will require an explicit identity migration.
Never submit a user ID in a request body or query parameter.

Seed the deployed **development** table manually:

```bash
python scripts/seed_dev.py --table DEV_TABLE_NAME \
  --user-arn arn:aws:iam::ACCOUNT_ID:user/USER_NAME --profile personal
```

The script requires the table's `Stage=dev` tag and inserts five temporary
English-to-French fixtures only when absent. Re-running it preserves progress.
Tests seed these cards in mocked DynamoDB; no seed runs automatically on deploy.

`GET /session?reviewLimit=20&newLimit=3` accepts limits of 0–50 and 0–10.
Limits apply per request, not per calendar day. Answers and FSRS internals are
excluded. The index is eventually consistent, so freshly seeded cards may take
a moment to appear; authoritative reads exclude cards already rescheduled.

`POST /reviews` accepts:

```json
{
  "reviewId": "686aa345-58ce-42ed-bbb7-2bdf936c7120",
  "cardId": "dev-1",
  "version": 1,
  "rating": "GOOD",
  "typedAnswer": "optional learner response"
}
```

Ratings are `AGAIN`, `HARD`, `GOOD`, or `EASY`. Keep the same UUID and payload
when retrying. Successful responses include the incremented version and
`nextDueAt`. HTTP 409 requires refreshing the session; 503 can be retried.
The answer-reveal endpoint and browser UI remain future work.

Each review stores the original request/result and before/after FSRS snapshots.
The review key is `REVIEW#<uuid>` (rather than a timestamp key), allowing direct
idempotency lookup across retries. `reviewedAt` retains the event timestamp.
FSRS state is stored as its complete JSON representation to preserve learning
steps, stability, difficulty, and last-review time without float conversion.
Tests use Moto and make no production data requests.
