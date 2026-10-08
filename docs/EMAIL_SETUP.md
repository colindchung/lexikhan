# Email reminders

The reminder Lambda uses SES v2 in us-east-2, from
`reminders@colindchung.com`. The domain identity and Squarespace DKIM records
are managed outside CDK. CDK grants only this worker permission to send from
that identity and to read the account suppression list. No API keys are needed.

## Connect a recipient

1. Sign up in Lexikhan, verify the Cognito account email, and complete onboarding.
2. While SES is in its sandbox, create an email identity for the same address in
   SES (us-east-2) and click its verification link.
3. Bind the verified recipient without sending or enabling reminders:

   ```sh
   .venv/bin/python scripts/approve_email.py --stage prod --email YOUR_EMAIL
   ```

4. Sign in, open **Reminders**, enable daily email, choose a time and timezone,
   and save. Turn it off there to stop future sends.

The personal recipient is connected. Production SES access is still disabled;
public signup alone does not grant permission to receive reminders. Request
production access before supporting unverified SES recipients; the administrative
allowlist remains in force.

## Delivery and operations

Production checks every five minutes. Development scheduling stays disabled.
At most one attempt per learner per local day is claimed atomically before SES
is called. Each email contains a random unseen vocabulary item, independent of
review cards or curriculum progress. More than one hour late means skip that
day. Timezone/DST handling and optimistic settings versions are preserved.
Old SMS consent does not enable email: email needs a new opt-in.

`EMAIL_ACCESS` stores the approved recipient; `REMINDER` stores explicit email
consent, schedule, and recipient; `EMAIL#YYYY-MM-DD` stores the daily attempt.
`VOCAB#<language>#<stable-id>` permanently records each claimed word in the same
transaction as the daily attempt and schedule advance. Even ambiguous sends
consume the word, preventing repeats after retries or timezone changes.
The initial catalog contains 70 everyday objects, food words, and casual phrases
per language (Urdu and Spanish), separate from the starter decks. Urdu includes
script and approximate Roman Urdu pronunciation. Email has plain text and HTML
with right-to-left Urdu. No paid generation service or new secrets are needed.
Selection is random without replacement for each account; after exhaustion,
sends pause until new entries are appended. Never reassign stable catalog IDs.
A settings change or revoked approval before the claim prevents sending.
An already claimed message may still arrive after disabling.

SES account suppression for BOUNCE and COMPLAINT is enabled. The worker checks
that list before claiming; unexpected lookup errors prevent sending.
`ACCEPTED` means SES accepted the request, not inbox delivery. Ambiguous provider
errors become `UNKNOWN`; interrupted status writes can remain `CLAIMED`.
Neither is automatically resent. Inspect CloudWatch reminder errors and the
attempt record before taking action; do not delete claims to retry blindly.
Logs omit recipient addresses. Failure destinations and alarms remain enabled.

No real email is sent by automated tests or the deployment smoke test.
