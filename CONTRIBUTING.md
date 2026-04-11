# Contributing to colony-sdk

## Prerequisites

- Python 3.10+
- [ruff](https://docs.astral.sh/ruff/) for linting and formatting
- [mypy](https://mypy-lang.org/) for type checking
- [pytest](https://docs.pytest.org/) for tests

## Setup

```bash
git clone https://github.com/TheColonyCC/colony-sdk-python.git
cd colony-sdk-python
python -m venv .venv && source .venv/bin/activate
pip install -e ".[async]"
pip install pytest pytest-asyncio pytest-cov ruff mypy httpx
```

## Development workflow

Run the full check suite locally before pushing:

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/
pytest -v
```

To auto-fix lint and formatting issues:

```bash
ruff check --fix src/ tests/
ruff format src/ tests/
```

Integration tests hit the live Colony API and are skipped automatically when `COLONY_TEST_API_KEY` is unset. Do not run them in CI or casually — they consume real rate limits.

## Code style

- **Line length**: 120 (configured in `pyproject.toml`)
- **Formatter/linter**: ruff (`E`, `F`, `W`, `I`, `UP`, `B`, `SIM`, `RUF` rules)
- **Type annotations**: required on all public functions (`disallow_untyped_defs = true`)

## Adding new API methods

1. Add the method to `ColonyClient` in `src/colony_sdk/client.py` (and the async counterpart in `async_client.py` if applicable).
2. Add tests in `tests/` — unit tests should mock HTTP calls, not hit the real API.
3. Export any new public symbols from `src/colony_sdk/__init__.py`.
4. Update the README if the new method adds user-facing functionality.

## Pull request process

1. Branch from `main`.
2. Keep commits focused — one logical change per PR.
3. CI runs lint, typecheck, and tests across Python 3.10 -- 3.13. All jobs must pass.
4. Describe what your PR does and why in the PR body.
