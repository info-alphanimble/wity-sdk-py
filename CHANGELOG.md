# Changelog

All notable changes to `wity-sdk` (Python). Versions follow [semver](https://semver.org). Before 1.0, a minor version can add features.

## 0.2.1 (2026-10-26)

### Added

- **Update notice.** At a terminal, a client tells you on stderr when a newer `wity-sdk` is on PyPI, at most once a day. The check runs at most once a day in a background thread, so nothing waits for it. It stays quiet in CI, when output is piped, and on servers. `WITY_NO_UPDATE_CHECK=1` turns it off.

## 0.2.0 (2026-10-22)

Split documents, extract fields, and teach Wity with skills and sessions. Everything below works on both `WityClient` and `AsyncWityClient`.

### Added

- **Split.** `client.split(text=..., split=...)` cuts a document into whole items: emails, clauses, messages, log entries. Every item is an exact slice of the text, with `start` and `end` offsets.
  - `split` is plain words (`"one item per email"`) or a `SplitSpec(unit, starts_at, keep_together, never_split)`.
  - `how` says whether the model cut it (`"model"`) or a rule did (`"rule:sentence"`, `"rule:line"`, ...).
  - `skill=` splits the way a split skill's examples do. Give `split` or `skill`, not both.
- **Extract.** `client.extract(text=..., fields=..., types=...)` pulls named fields out of a document. Each value comes back as written, with its offsets and a confidence. `types` normalises a field as a `"date"`, `"duration"` or `"number"`.
- **Skills.** `client.skills` saves a question once and learns your definition from labelled examples.
  - `create`, `list`, `get`, `update` and `delete`.
  - `examples.add`, `examples.list` and `examples.delete`.
  - Decision skills take a `noul` or `choice` question, with optional `parts` and a `combine` rule. Split skills take a `split` and example splits.
  - A skill comes back as `DecisionSkill` or `SplitSkill`, told apart by `kind`. `update(parts=None)` removes the parts.
- **Skill questions.** `skill("name")` asks a saved skill's question in `system_one`. The answer adds `skill` (which version answered, and how) and, for skills with parts, `parts`.
- **Sessions.** `client.sessions` keeps a state on Wity's side.
  - `create`, `list`, `get`, `update` and `delete`.
  - `add_events` adds updates, a new state, notes, or feedback that becomes a skill example.
  - `events` reads the log.
  - `decide` runs the session's skills, or your own questions, on what the session holds now.
- **Errors.** `NotFoundError` (404) for a missing skill, session or example. `ConflictError` (409) for a skill name that's taken.
- **Models** for every new response, all exported from `wity`.

### Changed

- Calls that add something retry only after 429 and 503, when Wity did no work. These are `skills.create`, `skills.examples.add`, `sessions.create`, `sessions.add_events` and `sessions.decide`. After any other failure the call may have gone through, so the SDK raises instead of adding it, or billing it, twice. Other calls retry as before.
- Split and extract wait at least 120 s before timing out, even if `timeout` is shorter. Long documents take a while.
- A 404 from Wity's own "not found" no longer adds the hint that the API address may have changed. A 404 for an address that doesn't exist still does.

### Security

- Ids and names are encoded before they go in a URL path. `.` and `..` are refused, since in a URL they point at a different address.

## 0.1.1 (2026-10-05)

### Changed

- The default API address is now `https://api.wity.alphanimble.com`. It reaches the same server as the old Railway address, which keeps working for 0.1.0.

## 0.1.0 (2026-09-30)

First release.

- `WityClient` (blocking) and `AsyncWityClient` for `POST /v1/systemone` (typed decisions) and `POST /v1/generate` (short text, or JSON in a shape you define).
- `choice`, `noul` and `score` builders, and Pydantic models for every answer.
- Images as data URLs, `reasoning` modes and `max_latency_ms`.
- Errors that say what went wrong, retries with backoff and `Retry-After`, timeouts, and `extra_body` for API fields newer than the SDK.
- Logging that never includes the key or the state.
- Refuses plain `http` outside localhost, and never follows redirects.
