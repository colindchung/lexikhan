# Lexikhan Implementation Plan

## Objective

Build a personal, mobile-friendly language-learning application that uses active
recall and spaced repetition. A learner should be able to open a short daily
session, answer cards, grade recall, and have Lexikhan schedule each card's next
review automatically.

The first useful release is complete when one authenticated user can finish a
review session from a phone and find the resulting schedule intact the next day.

## Current state

The initial infrastructure has been deployed. The local next-PR implementation
now adds the core backend flow (not yet deployed):

- Scheduler disabled in all stages; placeholder `POST /run` removed.
- Sparse `due-index`, typed cards/reviews, transactional persistence.
- IAM-protected `GET /session` and `POST /reviews` using verified caller identity.
- Bundled FSRS scheduling, durable retry results, stale-version conflicts.
- An idempotent five-card development seed script and mocked persistence tests.

Cognito, answer reveal, the web UI, and reminders remain outstanding. IAM is a
temporary access-control bridge; learning keys currently use the verified caller
ARN rather than a Cognito subject. FSRS fields are stored in a complete JSON
snapshot. Review records use `REVIEW#<uuid>` for direct idempotency lookup and
retain a separate `reviewedAt` timestamp. See README for the local build and API
workflow. These local changes do not change the deployed production stack.

## Product decisions required

Resolve these before creating production learning content:

1. Target language and the learner's base language.
2. Initial content source: a curated deck, user-created cards, or both.
3. Whether the first cards teach words, phrases, sentences, or a mixture.
4. Preferred reminder time and timezone.
5. Whether answers are initially self-graded or evaluated automatically.

Recommended defaults are a curated starter deck of phrases, self-grading, three
new cards per day, and one daily reminder in the learner's timezone.

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
  +--> SES reminder email
```

Recommended frontend: a React and TypeScript progressive web app in `web/`,
deployed as static assets through S3 and CloudFront. It should be installable on
a phone and remain a separate package inside this repository.

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

Change the production schedule from every 15 minutes to once daily in the
learner's timezone. The scheduled operation should:

1. Query whether at least one card is due.
2. Do nothing when no cards are due.
3. Send one SES email containing the due count, estimated duration, and a link
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

- Create the `web/` React and TypeScript application.
- Implement sign-in, home, card prompt, answer reveal, rating, progress, and
  completion views.
- Design mobile-first with keyboard support and accessible focus states.
- Handle retries and HTTP `409` conflicts without losing the learner's answer.
- Add a web manifest and installable PWA shell.
- Deploy through S3 and CloudFront from CDK or a dedicated frontend workflow.

**Exit criteria:** the complete review flow works comfortably on a phone and a
desktop browser.

### Phase 5: Add reminders

- Verify the sender and personal recipient in SES.
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

## Current local pull request scope

Keep the next change deliberately narrow:

1. Disable the production scheduler.
2. Add `due-index`.
3. Add typed card and review models.
4. Add the DynamoDB repository.
5. Implement `GET /session` and `POST /reviews` without a frontend.
6. Seed five temporary development cards.
7. Cover the complete persistence flow with tests.

Do not add AI generation, email, audio, or analytics in this pull request. The
first priority is proving that the core learning state and review lifecycle are
correct.


This scope is implemented locally, pending review and deployment. Next, finish
Phase 2 with answer reveal and a deployed development smoke test, then replace
the temporary IAM access with Cognito in Phase 3.
