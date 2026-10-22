# Wity Python SDK

Python client for [Wity](https://wity.alphanimble.com), the typed-decision API. Send some text and a few questions with fixed options. Get back a probability for every option. It also splits documents into items, extracts fields, and teaches skills from your examples.

## Install

```sh
pip install wity-sdk
```

Or with uv: `uv add wity-sdk`. Needs Python 3.10 or newer. The SDK depends on `httpx` and `pydantic`.

## Quick start

Set your key in the environment:

```sh
export WITY_API_KEY="wity_..."
```

Then ask questions:

```python
from wity import WityClient, choice, noul, score

with WityClient() as client:
    res = client.system_one(
        state="I was charged twice for my subscription this month. Please refund one of them today.",
        questions={
            "team": choice("Which team should handle this ticket?", {
                "billing": "Payments, charges and refunds",
                "technical": "Bugs, errors and outages",
                "other": "Anything else",
            }),
            "urgent": noul(
                "Does the customer ask for something to happen soon?",
                yes="They ask for fast action",
                no="No time pressure",
            ),
            "severity": score("How much is this hurting the customer?", ["Not at all", "A little", "A lot"]),
        },
    )

res.choices["team"].choice         # "billing"
res.choices["team"].probabilities  # {"billing": 0.99993..., "technical": 0.00003..., "other": 0.00003...}
res.nouls["urgent"].noul           # 0.99989... = probability of yes
res.scores["severity"].score       # 1.97322... = expected level, from 0 (Not at all) to 2 (A lot)
```

Answers come back under the names you gave the questions.

- `res.choices`, `res.nouls` and `res.scores` each hold one type of answer, so your editor knows their fields.
- `res.answers` holds every answer, whatever its type.
- Answers are Pydantic models. `res.model_dump()` turns the whole response into a dict.

## Async

`AsyncWityClient` takes the same options and has the same methods. Use `await`:

```python
from wity import AsyncWityClient

async with AsyncWityClient() as client:
    res = await client.system_one(state=state, questions=questions)
```

## Questions

| Builder | Asks | Answer fields |
|---|---|---|
| `choice(instructions, {"id": "description", ...})` | Pick one of 2–256 options | `choice`, `probabilities`, `confidence` |
| `noul(instructions, yes="...", no="...")` | Yes or no. `yes` and `no` are optional, but give both or neither | `noul` (probability of yes) |
| `score(instructions, ["lowest", ..., "highest"])` | Pick one of 2–10 levels | `score`, `legend`, `probabilities`, `confidence` |

- `state` can be text, or a dict or list, which Wity reads as JSON.
- Option ids written as numbers (`{1: "Ground"}`) come back as strings (`"1"`).
- `score` levels can come from a variable, like a list loaded from a database.
- The builders return `Choice`, `Noul` and `Score` models. You can create those directly too. A plain dict in the API's own format also works as a question.
- Wity checks the limits (option counts, lengths). If one is broken, it says which in a `BadRequestError`. Failed calls aren't billed.
- Probabilities sum to 1, but they are not calibrated. Treat them as a ranking of how sure the model is, not as exact odds.

## Reasoning

Wity answers most questions in one fast pass, and thinks first only when a question needs it.

```python
client.system_one(state=state, questions=questions, reasoning="off", max_latency_ms=3000)
```

- `reasoning`:
  - `"auto"` (the default) thinks only when needed.
  - `"off"` never thinks. It's the fastest.
  - `"always"` thinks before every answer.
- `max_latency_ms` caps the time per request, from 200 to 120000 ms. If thinking was cut short, the answer's `reasoning.budget_limited` is `True`. The SDK waits at least `max_latency_ms` plus 10 s before timing out, even if `timeout` is shorter.
- When Wity thought, the answer has `reasoning.thought == True`. It also carries the answer from before thinking: `direct_probabilities` for choice and score, `direct_noul` for noul.

## Images

Both `system_one` and `generate` take one image as a data URL:

```python
client.system_one(state="Photo of the delivered parcel", questions=questions, image="data:image/jpeg;base64,...")
```

## Generate

Choice, noul and score pick from options you wrote in advance. `generate` writes text you couldn't list ahead of time: a value read off a document, a form field, a one-line reply. It returns one piece of text per call, with no probabilities. If the answer is one of a known set, use a question instead.

Free text:

```python
reply = client.generate(
    state="I was charged twice for my subscription this month. Please refund one of them today.",
    instructions="Write the one-sentence reply the agent sends to the customer.",
    max_tokens=80,
)

reply.text  # "Thank you for bringing this to our attention; I've reviewed your account..."
```

JSON in a shape you define. `shape` is a JSON Schema whose top level is an object:

```python
origin = client.generate(
    state="Ticket scan: LISBOA (LIS) -> BERLIN BRANDENBURG (BER), 14 Oct, seat 23C.",
    instructions="Origin city name, in English",
    shape={
        "type": "object",
        "properties": {"city": {"type": "string", "pattern": "^[A-Za-z ]{1,40}$"}},
        "required": ["city"],
    },
    max_tokens=40,
)

origin.value  # {"city": "Lisbon"}
```

- When Wity finishes (`finish_reason == "stop"`), the output matches the shape and comes back parsed in `value`.
- If `finish_reason` is `"length"`, Wity hit `max_tokens` (1 to 512, default 128) before finishing. `text` is cut off and `value` is `None`. Raise the limit, or send the item to a person.
- Only input tokens are billed.
- Check generated text before acting on it. It can state things that aren't in `state`.

## Split

`split` cuts a document into whole items: emails from a thread, clauses from a contract, messages, log entries. Every item is an exact slice of the text you sent. Nothing is rewritten or dropped.

```python
res = client.split(text=thread, split="one item per email")

for item in res.items:
    assert thread[item.start : item.end] == item.text
```

- `split` says what one item is, in plain words (3 to 300 characters). The default is `"one item per paragraph"`.
- For more control, pass a spec: `SplitSpec(unit="clause", starts_at=["a numbered heading like 4. Liability."])`. It also takes `keep_together` and `never_split`.
- `sentence`, `line`, `bullet`, `speaker turn` and `paragraph` are cut by rule. Then `how` is `"rule:<kind>"` instead of `"model"`.
- `text` can be up to 60,000 characters. The SDK waits at least two minutes for split, even if `timeout` is shorter.
- A common pattern: split, then ask one question per item, with the item's text as `state`.

To split an unusual format the way you would, teach a [split skill](#skills) and pass `skill=` instead of `split=`. Give one or the other, not both.

## Extract

`extract` pulls named fields out of a document. Each value comes back as written, with its position and a confidence.

```python
res = client.extract(
    text=contract,
    fields={"party": "a company that signs the agreement", "effective date": "when it takes effect"},
    types={"effective date": "date"},
)

res.fields["party"]  # ["Kestrel Foods Ltd.", "Brightmart Inc."]
res.entities[0]  # ExtractEntity(field="party", text="Kestrel Foods Ltd.", start=112, end=130, confidence=0.98)
```

- `fields` maps each field name to what it asks for, in plain words. Give 1 to 16 fields. Each field returns up to 30 values, and `[]` when the document has none.
- `types` makes a field come back normalised: `"date"` (YYYY-MM-DD), `"duration"` or `"number"`. The offsets still mark where the value was read.
- `confidence` is from 0 to 1. It is not calibrated.
- `text` can be up to 60,000 characters. The SDK waits at least two minutes for extract.

## Skills

A skill saves a question once and learns your definition from labelled examples. Make it once, then use it by name:

```python
from wity import noul, skill

client.skills.create(
    name="urgent-ticket",
    question=noul("Does this support ticket need a reply within the hour?"),
    examples=[
        {"state": "Checkout is down for every customer.", "answer": "yes"},
        {"state": "Can you change my invoice address?", "answer": "no"},
        # ...
    ],
)

res = client.system_one(state=ticket, questions={"urgent": skill("urgent-ticket")})
res.answers["urgent"].skill  # SkillInfo(name="urgent-ticket", version=3, answered_by="examples", active=True, ...)
```

- A skill's question is a `noul` or a `choice`. Learning starts at 8 examples, with at least 2 for each of two answers.
- The skill uses what it learned only when that beats the plain question on examples it held back. Then `active` is `True`, and `quality` on the skill shows the accuracy with and without it.
- `parts` splits a hard yes/no question into simple ones, and `combine` joins them with a rule like `"not same_state and amount"`. `parts="auto"` lets Wity write them.
- Skill questions are answered in fast mode.

Every method that takes a skill accepts its id (`sk_...`) or its name:

| Call | Does |
|---|---|
| `skills.create(name=..., question=..., ...)` | Make a decision skill. Returns a `DecisionSkill` |
| `skills.create(name=..., split=..., examples=...)` | Make a split skill. Examples are `{"text", "items"}` or `{"text", "starts"}`. Returns a `SplitSkill` |
| `skills.list(limit=..., after=...)` | Newest first, in pages of up to 100 |
| `skills.get(skill)` | One skill |
| `skills.update(skill, ...)` | A new question, parts or rule makes it learn again. `parts=None` removes the parts |
| `skills.delete(skill)` | Delete it and its examples |
| `skills.examples.add(skill, examples)` | Add 1 to 1,000 examples |
| `skills.examples.list(skill, limit=..., after=...)` | Newest first |
| `skills.examples.delete(skill, example_id)` | Remove one example |

Use a split skill with `client.split(text=..., skill="my-format")`. One example already helps, and about three is enough.

Reading and managing skills is free. Adding examples is billed once, on their tokens.

## Sessions

A session keeps the state on Wity's side. Add to it as things happen, decide when you need to, and correct answers so the skills learn.

```python
session = client.sessions.create(state="Ticket #5120 from Northwind Foods.", skills=["urgent-ticket"])

client.sessions.add_events(session.id, [{"type": "update", "content": "Customer: checkout still fails."}])

decision = client.sessions.decide(session.id)  # runs the session's skills
decision.answers["urgent-ticket"]

# It was wrong: this becomes an example of the skill.
client.sessions.add_events(session.id, [{"type": "feedback", "decision_id": decision.decision_id, "answer": "no"}])
```

| Event | Does |
|---|---|
| `{"type": "update", "content": ...}` | Adds to what Wity reads |
| `{"type": "state", "content": ...}` | Replaces what Wity reads from here on |
| `{"type": "note", "content": ...}` | Kept in the log, never read |
| `{"type": "feedback", "decision_id": ..., "answer": ...}` | The right answer to an earlier decision. Add `"question"` when it had more than one skill question |

- `decide(session_id, questions=...)` asks your own questions instead of the session's skills. Its `reasoning` defaults to `"off"`. `remember=True` adds the answers to what later decisions read.
- `context` on a decision says how many updates it read, and how many of the oldest were left out to fit.
- `cached` is `True` when the same contents and questions were answered before. You get the same answer, for 0 tokens.
- `sessions.list`, `get`, `update` (skills, metadata, expires_in), `delete` and `events(session_id)` (the log, oldest first) work like the skills calls.
- Sessions expire after 7 days, unless `expires_in` says otherwise (60 s to 90 days).

Decisions are billed like `system_one`. Events, reads and repeats are free.

`AsyncWityClient` has the same `split`, `extract`, `skills` and `sessions`, with `await`.

## Errors

Every error the SDK raises extends `WityError`:

```python
from wity import RateLimitError, WityError

try:
    client.system_one(state=state, questions=questions)
except RateLimitError:
    ...  # still rate limited after retries
except WityError as err:
    print(err)
```

| Error | When |
|---|---|
| `BadRequestError` | 400: the request is wrong. The message says which part. |
| `AuthenticationError` | 401: the key is missing or wrong. |
| `InsufficientBalanceError` | 402: the account has no balance left. |
| `NotFoundError` | 404: no skill, session or example by that id or name. |
| `ConflictError` | 409: a skill with that name already exists. |
| `RateLimitError` | 429: too many requests, after retries. |
| `InternalServerError` | 5xx: a problem on Wity's side, after retries. |
| `APIError` | Any other status. All the classes above extend it. It has `status`, `body`, `headers` and `request_id`. |
| `APIConnectionError` | The API couldn't be reached, or the connection broke while reading its answer. |
| `APITimeoutError` | No answer within the timeout. |
| `WityError` | Anything else: a setup problem like a missing key, a request that can't be sent as JSON, or an answer that isn't from Wity. |

`request_id` is the server's id for the request. Quote it when you report a problem.

Building a question with the wrong types, like `Choice(criteria=["a", "b"])`, raises Pydantic's `ValidationError` on that line, before anything is sent.

**Typos and new fields.** A misspelled argument, like `max_latency=3000` instead of `max_latency_ms`, raises `TypeError` straight away. To send a field the API has but this SDK doesn't know yet, use `extra_body`:

```python
client.system_one(state=state, questions=questions, extra_body={"new_field": True})
```

The SDK logs a warning that names each field it doesn't know. If Wity rejects the request, the `BadRequestError` names them too.

## Retries, timeouts and cancelling

- 408, 429, 5xx and network failures are retried up to 2 times. The SDK waits longer each time, or as long as the `Retry-After` header asks.
- If `Retry-After` asks for more than 60 s, the SDK raises straight away and leaves the decision to you.
- Timeouts aren't retried. The request may already have reached Wity, and you've already waited the full timeout.
- Calls that add something are retried only after 429 and 503, when Wity did no work. These are `skills.create`, `skills.examples.add`, `sessions.create`, `sessions.add_events` and `sessions.decide`. After any other failure the call may have gone through, so the SDK raises instead of adding it twice.
- If the connection breaks while the answer is on its way back, Wity may already have billed the call. The retry is billed again.

```python
WityClient(timeout=30, max_retries=0)  # defaults: 60 seconds and 2
```

Cancel an async call the usual asyncio way. This also stops any retries and the waits between them. `asyncio.timeout()` (Python 3.11+) or `asyncio.wait_for()` caps the total time across all attempts:

```python
async with asyncio.timeout(20):
    await client.system_one(state=state, questions=questions)
```

`WityClient` calls can't be cancelled once started, like any blocking HTTP call. Use `AsyncWityClient` if you need that.

## Logging

The SDK logs through Python's standard `logging` module, under the logger name `wity`. Only warnings show unless you turn it up:

```sh
export WITY_LOG_LEVEL=debug   # or info, warn, error, off
```

Or from code, like any logger:

```python
import logging
logging.getLogger("wity").setLevel(logging.DEBUG)
```

| Level | What you see |
|---|---|
| `warn` (the default) | Unknown fields in a request, and a `Retry-After` too long to wait for. Nothing when calls succeed. |
| `info` | Also each retry (why, and how long it waits), timeouts and connection errors. |
| `debug` | Also each request: route, attempt, size in bytes, question names, status, time and request id. |
| `off` | Nothing. |

If your app hasn't set up logging, `WITY_LOG_LEVEL=debug` or `info` prints to stderr with a `[wity]` prefix. If it has, the messages go to your handlers.

Logs never contain your API key (not even part of it) or the `state` text, which is usually customer data. Errors are always raised to you. Logs only add context, whatever the level.

## Configuration

| Option | Environment variable | Default |
|---|---|---|
| `api_key` | `WITY_API_KEY` | required |
| `base_url` | `WITY_BASE_URL` | `DEFAULT_BASE_URL` (the production API) |
| `timeout` | | `60` seconds |
| `max_retries` | | `2` |
| | `WITY_LOG_LEVEL` | `warn` |
| `http_client` | | a new `httpx.Client` (or `httpx.AsyncClient`) |

An option passed in code wins over the environment variable. An empty `api_key` is an error. It never falls back to `WITY_API_KEY`, which could belong to a different account. `client.base_url` shows which address the client uses.

Pass your own `http_client` for proxies, custom certificates or HTTP/2 (`httpx.Client(http2=True)`, which needs `pip install httpx[http2]`). The SDK doesn't close a client you pass in.

## Security

- Keep the key on your server. Anyone who can see your browser code can read a key inside it.
- The client refuses to run in a browser (Pyodide or PyScript), to catch that mistake early.
- Load the key from the environment or a secret manager. Don't write it in code.
- The client never shows the key when it's printed or logged, and errors never include it.
- `base_url` must use `https://`. Plain `http://` is allowed only for `localhost`, `127.0.0.1` and `[::1]`.
- The client never follows redirects, so the key only goes to the address you set. This holds even if your own `http_client` is set to follow them.
- An answer larger than 10 MB can't be from Wity. The client stops reading it and raises a `WityError`.

## Support

Questions, bugs and security problems: info@alphanimble.com. Please report security problems by email, not in public.

## License

Apache-2.0. See [LICENSE](LICENSE).
