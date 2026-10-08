# Lexikhan

Lexikhan is a personal language-learning app: a React/TypeScript Vite frontend
on S3 and CloudFront, Cognito sign-in, and a Python Lambda/DynamoDB review API.
AWS infrastructure is managed with CDK.

## Architecture

- `GET /health` reports service health.
- `GET /session` returns due prompts, with reviews before new cards.
- `GET /cards/{cardId}/answer` reveals an owned card’s answer and optional context.
- `POST /reviews` atomically records a review and its FSRS schedule.
- Learning routes require Cognito access tokens validated by the API Gateway JWT
  authorizer. The browser signs in with authorization code + PKCE; no client secret.
- A private S3 bucket serves the Vite app through CloudFront origin access control.
- EventBridge Scheduler checks production reminders every five minutes; development stays disabled.
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

Python 3.12 or newer, Node.js 22.12+ (CI uses Node 24), and the AWS CDK CLI are required.

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
push to `main`, including a merged pull request, validates development before
deploying the `prod` CDK stage and publishing its frontend. The workflow can also be run manually from the Actions page.

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
- `src/handler/main.py`: authenticated HTTP API adapter.
- `src/handler/email_worker.py`: scheduled AWS SES reminder worker.
- `src/handler/service.py`: shared business logic.
- `tests/unit`: Lambda behavior tests.
- `tests/infrastructure`: synthesized-template assertions.

## Development learning flow

Sign in with the Cognito hosted UI at the stack's `WebUrl`. The app sends a
Cognito access token in `Authorization: Bearer ...`. API Gateway verifies its
issuer, expiry, app client, and `aws.cognito.signin.user.admin` scope; the handler
requires `token_use=access` and derives `USER#<sub>` from verified claims.
This standard Cognito scope is also available to the disposable development
smoke-test user. ID tokens and the old IAM authorizer are not accepted.

Public signup is enabled. Select **Sign in to your space**, then **Sign up**
in the Cognito hosted UI. Register with an email and password, then verify your
email with the code Cognito sends. No new API secrets are needed. Test users
are created without sending invitations.

On first sign-in, choose a language/deck, timezone, and daily practice goal
(3, 5, or 10 cards). **Urdu from English** is the default: 12 everyday phrases
with Urdu script and romanized pronunciation. Spanish from English is also
available. The goal sets the session size, not a hard daily limit.

`GET /profile` returns the learner profile and available deck metadata.
`POST /onboarding` accepts `deckId`, `learningLanguage`, `baseLanguage`,
`timezone` (IANA), and `dailyGoal`. Both routes require the same verified access
token as the review API. The profile and versioned starter cards are created in
one conditional DynamoDB transaction. Identical retries return the saved profile;
changed settings return 409. Existing cards and review progress are preserved.
During the first minute after enrollment, sessions use strongly consistent reads
so starter cards are available before the due index catches up. Profile editing is not implemented yet. Email reminder settings are available
from **Reminders** after onboarding; see [AWS email setup](docs/EMAIL_SETUP.md).

**Existing IAM-keyed data:** it is retained but is not automatically assigned to
a Cognito account. Any existing cards/reviews under `USER#<IAM ARN>` need an
explicit, verified migration to the intended `USER#<sub>` before being visible.
Never submit a user ID in a request body or query parameter.

Seed the deployed **development** table manually:

```bash
python scripts/seed_dev.py --table DEV_TABLE_NAME \
  --user-sub COGNITO_USER_SUB --profile personal
```

The script requires the table's `Stage=dev` tag and inserts five temporary
English-to-French fixtures only when absent. Re-running it preserves progress.
Tests seed these cards in mocked DynamoDB; no seed runs automatically on deploy.

`GET /session?reviewLimit=20&newLimit=3` accepts limits of 0–50 and 0–10.
Limits apply per request, not per calendar day. Answers and FSRS internals are
excluded. The index is eventually consistent, so freshly seeded cards may take
a moment to appear; authoritative reads exclude cards already rescheduled.
The response also includes `nextReviewAt` for the earliest learned card.

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
Use `GET /cards/{cardId}/answer` between the prompt and rating steps. It returns
`cardId`, `answer`, and `examples` (an empty list when absent), plus `explanation`
and `audioUrl` when present. Missing cards and cards owned by another learner
both return 404. Invalid card IDs return 400. Revealing is read-only, does not
advance the schedule or version, and responses use `Cache-Control: no-store`.
The mobile UI supports optional typed answers, explicit reveal, self-grading,
progress, and completion. Session drafts and exact retry payloads are kept in
session storage, scoped to the signed-in user and cleared on sign-out.

Each review stores the original request/result and before/after FSRS snapshots.
The review key is `REVIEW#<uuid>` (rather than a timestamp key), allowing direct
idempotency lookup across retries. `reviewedAt` retains the event timestamp.
FSRS state is stored as its complete JSON representation to preserve learning
steps, stability, difficulty, and last-review time without float conversion.
Tests use Moto and make no production data requests.


## Frontend development and hosting

```bash
npm ci --prefix web
npm --prefix web test
npm --prefix web run build
npm exec --prefix web -- playwright install chromium
npm --prefix web run test:e2e
```

For local development, copy the deployed development site's public `config.json`
to `web/public/config.json` (git-ignored), then run `npm --prefix web run dev` and
open **http://localhost:5173**. Only that localhost origin is allowed in development;
production CORS and Cognito redirects allow only the production CloudFront URL.
Runtime configuration contains only the API URL, Cognito issuer/client ID, and
hosted sign-in domain. It contains no credentials or secrets.

After changing frontend source:

```bash
npm --prefix web run build
python scripts/deploy_web.py --stage dev --profile personal
```

`deploy_web.py` reads stack outputs, uploads hashed assets with immutable caching,
then runtime config and the entry page with revalidation/no-store policies, and
invalidates entry files. Older hashed assets remain available for open tabs.
CloudFront rewrites extensionless paths to the app shell, including `/auth/callback`.
The S3 origin stays private; HTTPS is enforced. No custom domain is required.
Fonts are bundled locally. The manifest/icons and service worker provide an
installable shell; API responses, tokens, and runtime config are never cached by
the service worker, and reviews require a connection. Updates activate after old
tabs close so a new release doesn't interrupt an active session.

## Deployed smoke test

```bash
python scripts/build_lambda.py
cdk deploy -c stage=dev --profile personal
python scripts/deploy_web.py --stage dev --profile personal
python scripts/smoke_backend.py --browser --profile personal
```

The smoke test refuses every stack except `Lexikhan-dev` with `Stage=dev`, also
checks the table tag, creates a disposable Cognito user, and tests anonymous
rejection, access-token authentication, session/reveal/review, duplicate retries,
stale versions, and strongly consistent reads of the resulting DynamoDB records.
With `--browser`, it additionally signs in through the real hosted UI, completes
one review on the deployed mobile site, reloads, and signs out. Its generated
user and data are removed in `finally`; credentials never go in command-line
arguments, logs, screenshots, or saved browser state. Production data is not used.

Every push to `main` runs Python and frontend checks, deploys development, publishes
the development frontend, and runs this full smoke test **before** deploying and
publishing production. The existing `personal` GitHub environment credentials are
used. Those credentials must allow CloudFormation/CDK deployment plus Cognito
test-user lifecycle/authentication, development DynamoDB read/write, S3 asset
uploads, and CloudFront invalidation. Production authentication does not enable
the admin password flow used by the smoke test.


## Production custom domain

The production custom hostname is `lexikhan.colindchung.com`. Squarespace remains
the DNS provider. `infra/config.py` imports the DNS-validated ACM certificate in
`us-east-1`, as required by CloudFront; that externally managed certificate must
be `ISSUED` before deploying the custom-domain configuration. Keep the ACM
validation CNAME in DNS for automatic renewal.

Squarespace custom records (host names are relative to `colindchung.com`):

| Type | Host | Data |
| --- | --- | --- |
| CNAME | `_bb336f47969745dd02c100f93287d033.lexikhan` | `_f2cbbc95b0796305b52b9db27ccfb4e2.wzccmgtwzk.acm-validations.aws` |
| CNAME | `lexikhan` | `d12u67hs1yk1g8.cloudfront.net` |

After validation, deploy the CDK change to attach the hostname and certificate.
The same change adds the custom origin to Cognito callbacks/logout URLs and API
CORS. The original CloudFront URL remains allowed so existing links keep working.
`WebUrl` reports the custom address; `CloudFrontUrl` reports the distribution URL.
No API custom domain, nameserver change, or Cognito custom domain is required.

## Daily vocabulary emails

Daily random vocabulary with permanent per-account deduplication, AWS SES
integration, opt-in settings, and a five-minute production reminder
scheduler are implemented. The verified sender is reminders@colindchung.com.
Sending requires an approved email bound to the learner's verified Cognito
account. SES remains in its sandbox, so recipients must also be verified in SES.
No new API secrets or phone registration are required.
See [setup and delivery semantics](docs/EMAIL_SETUP.md).

## Language chat

Saved, account-private conversations help explain definitions, everyday phrases,
and pronunciation. Urdu replies include Roman Urdu and English meanings. Paid
access requires explicit account approval; the OpenAI key is held in AWS Secrets
Manager and read only by the background worker. See [chat setup](docs/CHAT_SETUP.md)
for activation, context limits, and failure behavior.
