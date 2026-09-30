# Developing the Wity Python SDK

## Commands

```sh
uv sync            # install the SDK and the dev tools
./check.sh         # format check, lint, types (mypy strict), tests, build
uv run ruff format .   # fix formatting
WITY_LIVE=1 uv run --env-file ../.env pytest tests/test_live.py   # real calls (2 billed, a tiny amount)
```

- The tests replay real API responses saved in `tests/fixtures/`, so they run offline and cost nothing. Each client test runs twice: once with `WityClient` and once with `AsyncWityClient`.
- The live test reads `WITY_API_KEY` and `WITY_BASE_URL` from `../.env`. That file lives in the folder above this repo. If you cloned this repo on its own, create it there.
- To test another Python version: `uv run --isolated --python 3.13 pytest`.
- To test the oldest allowed httpx and pydantic: `uv run --isolated --resolution lowest-direct pytest`.

## Fixtures

The fixtures are copies of `test/fixtures/` in the TypeScript SDK (`wity-sdk-ts`), which has the script that captures them. To refresh them:

1. In `wity-sdk-ts`, run `npm run fixtures` (a tiny amount).
2. Copy `wity-sdk-ts/test/fixtures/*.json` to `tests/fixtures/` here.
3. Run `./check.sh`.

The README's example answers come from `multi-auto.json`, `generate-text.json` and `generate-shape.json`. If those change, update the README to match.

## If the API address changes

1. Update `DEFAULT_BASE_URL` in `src/wity/client.py`. The tests use that constant, so they follow along.
2. Bump the version in `pyproject.toml` and `src/wity/__init__.py`.
3. Publish.

Until users update, they can set `WITY_BASE_URL` to the new address.

## Publishing

1. Bump the version in `pyproject.toml` and `src/wity/__init__.py`. The PyPI package name is `wity-sdk`; the import is still `wity`.
2. Run `./check.sh`.
3. Run `uv publish` with a PyPI token.
