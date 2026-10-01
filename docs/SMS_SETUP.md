# AWS SMS setup

Lexikhan sends transactional reminders through Amazon SNS in **us-east-2**.
Lambda uses IAM; there is no SMS API secret to add to GitHub or the frontend.

## Canada: one-time account setup

As inspected on 2026-10-01, the account is in the SMS sandbox, has a $1 monthly
SMS spend limit, and has no origination phone numbers. The app cannot deliver
texts until an SMS-capable sender and verified destination are configured.

1. In the [AWS End User Messaging SMS console](https://us-east-2.console.aws.amazon.com/sms-voice/home?region=us-east-2),
   provision a sender capable of sending to Canada. AWS permits purchasing a
   Canadian long code without a registration form; for best deliverability it
   recommends a registered toll-free number with international sending enabled.
   Review the recurring number charge and per-message charges before provisioning.
   See [Canadian long-code guidance](https://docs.aws.amazon.com/sms-voice/latest/userguide/phone-numbers-request-long-code.html)
   and [requesting a number](https://docs.aws.amazon.com/sms-voice/latest/userguide/phone-numbers-request.html).
2. Add the number's **resource policy** permitting `sns.amazonaws.com` to use it,
   scoped to this AWS account. Keep AWS-managed opt-outs enabled so STOP works.
   See [SNS origination identity permissions](https://repost.aws/articles/ARq-jq4ZWQTJqRg5_B4fqFZw/no-origination-identities-found-error-when-trying-to-send-messages-via-sns-or-pinpoint-even-though-the-required-origination-identity-is-acquired-in-the-account).
   SNS selects an eligible origination identity automatically; if several senders
   are added later, explicitly pin the desired sender before using them.
3. In [SNS SMS sandbox](https://us-east-2.console.aws.amazon.com/sns/v3/home?region=us-east-2#/sms),
   add the recipient's Canadian phone number and enter the verification code
   received on that phone. Sandbox verification itself sends an SMS.
   [AWS sandbox documentation](https://docs.aws.amazon.com/sns/latest/dg/sns-sms-sandbox.html).
4. Sign up in Lexikhan, verify the email, and complete onboarding. Bind that
   account to its verified phone with the admin helper:

   ```sh
   .venv/bin/python scripts/approve_sms.py --stage prod \
     --email YOUR_SIGN_IN_EMAIL --phone +1YOUR_NUMBER --profile personal
   ```

   This helper sends no message and does not enable reminders. It verifies the
   Cognito email, checks sandbox verification and provider opt-out, and binds a
   number to only one account. The binding is required even if AWS later moves
   the account out of sandbox. New account signup never grants permission to
   text an arbitrary number. Number reassignment requires an explicit admin
   migration of both `SMS_ACCESS` and `SMS_PHONE#... / OWNER`; the helper refuses
   to silently replace an existing binding.
5. In Lexikhan, open **Reminders**, select the time/timezone, check the SMS consent
   box, and save. This enables future daily sends. Turn it off in the app or reply
   STOP to the sender to stop messages. The app never overrides provider opt-out.

The existing $1 limit is unchanged. Review spending in AWS; request an increase
only if necessary. For this personal flow, staying in sandbox is sufficient for
verified recipients. Do not remove the application-level account binding when
leaving sandbox.

## Scheduling and delivery semantics

- Production checks the sparse `reminder-index` every five minutes. Development's
  schedule stays disabled. Each enabled user gets a local-time daily opportunity.
- First occurrence is used during fall-back; nonexistent spring-forward times
  move forward by the DST gap. Requests delayed more than one hour are skipped.
- No due cards, no SMS. New unlearned cards count as ready practice. The text
  gives the session-sized card count, approximate duration, and the HTTPS app link.
- A conditional DynamoDB transaction claims `SMS#YYYY-MM-DD` and advances the
  next reminder before calling SNS. It checks current settings and number approval.
- SNS has no idempotent Publish request. SDK retries are disabled. A crash after
  claiming can miss a reminder. `UNKNOWN` or unresolved `CLAIMED` attempts must
  not be blindly resent; the provider may already have accepted the message.
- `ACCEPTED` means SNS returned a message ID, **not** that the phone received it.
  Enable SNS delivery-status logging in the AWS console to diagnose carrier
  delivery. The integration stores acceptance metadata, not delivery receipts.
- The reminder Lambda has its own error alarm. Async failures and scheduler
  invocation failures use the existing DLQ and queue alarm. Alarms are visible
  in CloudWatch; no external alarm notification destination is configured.
- Public HTTP routes cannot invoke the sender. Only the dedicated worker has SMS
  permissions. Logs omit recipient numbers and provider exception details.

## API and records

`GET /reminders` returns settings, approved phone (only to its owner), and version.
`POST /reminders` accepts exactly `enabled`, `time` (`HH:MM`), `timezone` (IANA),
and `version`. Both routes require a verified Cognito access token. Saving an old
version returns 409; retrying the identical successful save is idempotent.

No record can be used to modify someone else's settings through these routes.
Onboarding is required. Phone approval is administrative; learners only control
consent and scheduling. Phone numbers are stored in the encrypted DynamoDB table.

## Verification

Tests mock SNS; they never send SMS. They cover approval, consent, user isolation,
no-due skips, opt-outs, DST, concurrent/duplicate attempts, disabling races, and
ambiguous provider/database failures. The deployed development smoke test verifies
the settings API and UI remain disabled for unapproved users. A real carrier
send remains a separate check after sender and destination setup.
