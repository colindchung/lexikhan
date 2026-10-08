# Language chat

The Vite app has a **Language chat** page with saved conversations. Questions and replies are stored in each learner’s DynamoDB partition. Urdu is the default, with Roman Urdu pronunciation and English explanations; the learner’s language preference is respected.

## Activate the OpenAI integration

1. Create an OpenAI API key in your OpenAI project with API billing enabled.
2. In AWS Secrets Manager, select **us-east-2**, then open **lexikhan/prod/openai**.
3. Set its secret value to JSON with one property: `{"api_key":"YOUR_OPENAI_API_KEY"}`. Never put this key in frontend configuration, source control, or chat.
4. Allow up to five minutes for an already-running worker to pick up a rotated key. New workers read it immediately.
5. Sign in, open **Language chat**, create a chat, and send a question.

The deployment creates the empty secret. Only the background chat Lambda can read it. Development uses a separate `lexikhan/dev/openai` secret; it can remain empty.

Access is disabled by default, including for public signups. Enable a confirmed, verified account explicitly:

```sh
.venv/bin/python scripts/enable_chat.py --stage prod --email colin.d.chung@gmail.com --profile personal
```

## Behavior and limits

- 50 questions per account per UTC day; at most 100 conversations and 60 questions per conversation.
- Questions are limited to 2,000 characters. Each generation uses at most 1,000 output tokens.
- Replies use `gpt-4.1-mini` through the Responses API; change `OPENAI_MODEL` in the infrastructure to select another model.
- The latest eight completed question/reply pairs accompany each new question. Older history remains readable in the app.
- `store: false` disables Responses application-state storage. Questions and recent history are still sent to OpenAI; this is not a promise of zero provider retention. See [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).
- Questions are queued after an atomic ownership, quota, and concurrency check. Duplicate submissions with the same message ID never start a second generation.
- Provider requests time out after 45 seconds. Stuck turns expire after 150 seconds when the chat is next opened or polled. Failed questions remain visible with an explicit retry action; failed attempts count toward the daily limit.
- No automatic model retries. An ambiguous dispatch or provider timeout can consume a question without delivering a reply.
- CloudWatch logs contain generic failure messages, not questions, keys, or provider error bodies. The chat Lambda error alarm covers unhandled failures.

Conversation deletion is not currently exposed. DynamoDB follows the application table’s retention policy. The OpenAI secret is retained when its stack is removed.
