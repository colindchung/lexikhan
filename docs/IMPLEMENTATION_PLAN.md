# Lexikhan Implementation Plan

## Objective

Build a personal, mobile-friendly language-learning application that uses active
recall and spaced repetition. A learner should be able to open a short daily
session, answer cards, grade recall, and have Lexikhan schedule each card's next
review automatically.

The first useful release is complete when one authenticated user can finish a
review session from a phone and find the resulting schedule intact the next day.

## Current state

The core review flow and phases 2–4 are implemented:

- Cognito authorization-code/PKCE sign-in with a secretless browser client.
- JWT-protected session, answer-reveal, and transactional review APIs.
- Sparse due index, FSRS scheduling, durable retries, and conflict handling.
- React/TypeScript Vite mobile UI with drafts, retry recovery, and an installable shell.
- Private S3 + CloudFront hosting and runtime configuration generated from stack outputs.
- Disposable development-only backend and hosted-sign-in browser smoke tests.
- Deployment from `main` validates development before publishing production.

Learning keys now use verified Cognito subjects. Old IAM-keyed records are retained
and require an explicit identity migration if they contain data to keep. The
public signup and first-login onboarding now create an account profile and
starter deck, with Urdu from English as the default. AWS SNS reminders and settings are implemented; the production scheduler checks
every five minutes. Real delivery awaits sender and verified-recipient setup.

## Product decisions required

Resolve these before creating production learning content:

1. Target language and the learner's base language.
2. Initial content source: a curated deck, user-created cards, or both.
3. Whether the first cards teach words, phrases, sentences, or a mixture.
4. Preferred reminder time and timezone.
5. Whether answers are initially self-graded or evaluated automatically.

Recommended defaults are a curated starter deck of phrases, self-grading, three
new cards per day, and one daily email reminder in the learner's timezone.

## Target user experience

### Home

Show one primary action with an honest estimate:

```text
12 reviews · 3 new · about 7 minutes

[ Start session ]
```

Also show the next scheduled review time and a quiet indication of recent
progress. Avoid punitive streaks.

### Review card

Each card has two states:

1. **Prompt:** show a sentence, meaning, or audio cue without the answer.
2. **Feedback:** reveal the expected answer, pronunciation, explanation, and an
   example in context.

The learner then selects one of:

- `Again`: could not recall it.
- `Hard`: recalled with substantial effort or an important error.
- `Good`: recalled correctly.
- `Easy`: recalled immediately and confidently.

Start with self-grading. It handles valid alternate translations without
requiring an unreliable free-form answer grader. Typed answers may still be
stored for reflection, but they should not determine the grade in the MVP.

### Session completion

Show reviewed, learned, and forgotten counts plus the next review time. A
session should normally take five to ten minutes.

## Architecture

```text
Mobile-first web app
        |
        v
API Gateway -- JWT authorizer
        |
        v
Lambda ------------> DynamoDB
  ^                       |
  |                       | due-index
EventBridge Scheduler     |
  |                       v
  +------------------> due cards
  |
  +--> SES email provider
```

Selected frontend: a React and TypeScript progressive web app built with Vite
in `web/`, deployed as static assets to a private S3 bucket served through
CloudFront. Manage hosting in CDK and automate frontend builds and deployment
from `main`. It should be installable on a phone and remain a separate package
inside this repository.

Use a Cognito user pool and an API Gateway JWT authorizer. Keep `GET /health`
public, but require authentication for every endpoint that reads or changes
learning data.

## DynamoDB model

Continue using the existing physical keys, `userId` and `itemId`, while storing
typed identifiers as their values.

### Card item

```json
{
  "userId": "USER#<cognito-sub>",
  "itemId": "CARD#<card-id>",
  "recordType": "CARD",
  "prompt": "I forgot my umbrella.",
  "answer": "<target-language answer>",
  "explanation": "<optional explanation>",
  "state": "NEW",
  "dueAt": "2026-10-01T12:00:00Z",
  "difficulty": 0,
  "stability": 0,
  "reviewCount": 0,
  "lapseCount": 0,
  "version": 1,
  "createdAt": "2026-09-30T12:00:00Z"
}
```

### Review event

```json
{
  "userId": "USER#<cognito-sub>",
  "itemId": "REVIEW#<timestamp>#<review-id>",
  "recordType": "REVIEW",
  "cardId": "CARD#<card-id>",
  "rating": "GOOD",
  "typedAnswer": "<optional learner answer>",
  "previousDueAt": "2026-09-30T12:00:00Z",
  "nextDueAt": "2026-10-04T12:00:00Z",
  "reviewedAt": "2026-09-30T12:03:00Z"
}
```

Review events are append-only. They preserve enough information to audit or
recalculate schedules if the scheduling algorithm changes.

### Due-date index

Add a sparse global secondary index:

```text
Name:          due-index
Partition key: dueUserId
Sort key:      dueAt
```

Only card items receive `dueUserId`, so review events do not appear in the
index. Query `dueUserId = USER#<sub>` and `dueAt <= now` to retrieve due cards
without scanning the table.

### Concurrency and idempotency

Every review request includes a client-generated `reviewId` and the card
`version` it was based on. Record the review event and update the card in one
DynamoDB transaction:

- The review item must not already exist.
- The stored card version must equal the submitted version.
- The update increments the card version.

Retrying a request is therefore safe, and two browser tabs cannot silently
overwrite one another's review state.

## API contract

Replace the placeholder `POST /run` route with the following authenticated
routes.

### `GET /session`

Query parameters:

- `reviewLimit`, default `20`, maximum `50`.
- `newLimit`, default `3`, maximum `10`.

Return overdue cards first, followed by new cards up to the requested cap.
Return only fields needed to display the prompt; do not include the answer until
the learner requests feedback.

### `GET /cards/{cardId}/answer`

Return the expected answer, explanation, examples, and optional audio URL. This
separate request makes the reveal action explicit and measurable.

### `POST /reviews`

Request:

```json
{
  "reviewId": "<uuid>",
  "cardId": "<card-id>",
  "version": 3,
  "rating": "GOOD",
  "typedAnswer": "<optional>"
}
```

Return the updated card state and `nextDueAt`. Duplicate `reviewId` requests
return the original successful result. Stale card versions return HTTP `409`.

### Later endpoints

- `POST /cards` to create a custom card.
- `PATCH /cards/{cardId}` to edit or suspend a card.
- `GET /stats` for progress summaries.

## Scheduling

Use a maintained FSRS implementation rather than inventing an interval formula.
Persist the scheduler fields on each card and keep the append-only review log.
Use library defaults initially; personalized parameter optimization can wait
until there is enough review history.

The API should calculate scheduling decisions on the server. This prevents
different clients from producing incompatible card states.

Any third-party Python dependency must be bundled into the Lambda artifact. The
current `Code.from_asset("src/handler")` packaging only copies source files and
does not install dependencies, so introduce a repeatable Lambda build step or
CDK bundling before adding FSRS.

## Scheduler and reminders

The production scheduler checks every five minutes. Each learner has one daily
reminder opportunity in their timezone. The scheduled operation should:

1. Query whether at least one card is due.
2. Do nothing when no cards are due.
3. Send one email containing the due count, estimated duration, and a link
   to the web session.
4. Record the reminder timestamp to prevent duplicate messages on retries.

The scheduler should not select or mark a card as reviewed. Only an explicit
learner action changes learning state.

## Implementation sequence

### Phase 1: Make the backend safe and stateful

- Disable the placeholder 15-minute schedule until reminders exist.
- Remove or protect `POST /run`.
- Add `due-index` to DynamoDB.
- Introduce card and review domain models.
- Implement a DynamoDB repository with conditional and transactional writes.
- Seed a small test deck through a checked-in script or deployment asset.
- Add unit tests for due-card ordering, idempotency, and concurrent reviews.

**Exit criteria:** tests can create cards, retrieve a due session, record a
review exactly once, and retrieve the newly calculated due date.

### Phase 2: Implement the review API

- Add `GET /session`.
- Add `GET /cards/{cardId}/answer`.
- Add `POST /reviews`.
- Validate all request bodies and return consistent error responses.
- Add structured logs without recording sensitive answer text by default.
- Add an end-to-end smoke test against a deployed non-production stack.

**Exit criteria:** a client can complete an entire session through HTTP and all
state survives a Lambda restart and redeployment.

### Phase 3: Add authentication

- Create a Cognito user pool and application client in CDK.
- Add an API Gateway JWT authorizer to all learning routes.
- Derive `userId` from the verified token subject; never trust a submitted user
  ID.
- Configure API CORS for the exact frontend origins.

**Exit criteria:** anonymous writes receive `401`, while the configured personal
user can complete a session.

### Phase 4: Build the web MVP

- Create the `web/` React and TypeScript application with Vite.
- Implement sign-in, home, card prompt, answer reveal, rating, progress, and
  completion views.
- Design mobile-first with keyboard support and accessible focus states.
- Handle retries and HTTP `409` conflicts without losing the learner's answer.
- Add a web manifest and installable PWA shell.
- Define a private S3 origin and CloudFront distribution in CDK.
- Build with Vite and deploy static assets automatically from `main`, including
  cache handling and client-side route support.

**Exit criteria:** the complete review flow works comfortably on a phone and a
desktop browser.

### Phase 5: Add reminders

- Configure the SES email provider, sender, and personal recipient.
- Configure a daily timezone-aware schedule.
- Send a reminder only when reviews are due.
- Include a direct HTTPS link to start the session.
- Alarm on reminder failures and dead-letter messages.

**Exit criteria:** one reminder arrives on a due day, no reminder arrives when
nothing is due, and retries cannot create duplicate messages.

### Phase 6: Improve learning quality

- Add audio and pronunciation practice.
- Mix recognition and production prompts.
- Add card editing and suspension.
- Add contextual examples and phrase-level cards.
- Optimize FSRS parameters after sufficient review history exists.
- Add lightweight progress analytics based on review events.

## Testing strategy

- **Domain tests:** scheduling inputs, ratings, due ordering, and edge cases.
- **Repository tests:** DynamoDB expressions, transactions, and idempotency.
- **Handler tests:** authentication context, validation, status codes, and
  response bodies.
- **Infrastructure tests:** table index, IAM grants, authorizer, routes,
  retention, alarms, and schedule state.
- **Frontend tests:** keyboard flow, reveal behavior, rating controls, loading,
  error recovery, and accessibility.
- **Deployment smoke test:** health check, authenticated session fetch, one test
  review, and verification of the resulting DynamoDB state.

Production data must never be used by automated tests.

## Latest implementation scope

- Public signup and first-login learner onboarding.
- Versioned Urdu (default) and Spanish starter decks for English speakers.
- Atomic, idempotent profile and deck enrollment without resetting existing cards.
- A timezone and a daily goal that controls session size.
- Authenticated profile/enrollment APIs, browser tests, and deployed development smoke coverage.


The backend, Cognito, Vite UI, CloudFront hosting, and deployed development
verification are implemented. Onboarding and atomic starter-deck enrollment are implemented, with Urdu as
the default and a configurable timezone and session goal. AWS SES email integration and reminder settings are implemented. The sender domain and personal
recipient are verified. Next: enable reminders in the app and confirm the first scheduled email.
See `EMAIL_SETUP.md` for sandbox limits and operations.

Daily emails now teach a random unseen everyday word or casual phrase directly,
independent of curriculum/review progress. A 70-entry pool per language pauses
when exhausted; extend it with new stable IDs to continue without repeats.
