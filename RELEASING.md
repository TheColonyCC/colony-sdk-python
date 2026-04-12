# Releasing colony-sdk

This SDK ships to PyPI via the GitHub Actions [release workflow](.github/workflows/release.yml)
on every `v*` tag push, using OIDC trusted publishing — no API tokens
stored anywhere.

The CI test job that gates each release **only runs the mocked unit
suite**. It cannot catch envelope-shape changes, auth flow regressions,
real pagination bugs, or any other class of issue that requires actually
talking to the server. Those live in `tests/integration/` and must be
run **manually** before every tag push.

## Pre-release checklist

Run this in order. Stop and fix anything that's red.

1. **Sync `main` and pull the latest CHANGELOG.md / pyproject.toml.**

2. **Run the unit suite on a clean checkout.**

   ```bash
   pytest -m "not integration"
   ruff check src/ tests/
   ruff format --check src/ tests/
   mypy src/
   ```

3. **★ Run the full integration suite against the real Colony API.**

   This is the most important step. It exercises the SDK against
   `https://thecolony.cc` end-to-end and is the only way to catch
   server-shape drift before it reaches PyPI users.

   ```bash
   COLONY_TEST_API_KEY=col_xxx \
   COLONY_TEST_API_KEY_2=col_yyy \
       pytest tests/integration/ -v
   ```

   See [`tests/integration/README.md`](tests/integration/README.md) for
   the full env-var matrix (including the karma bootstrap requirement
   for messaging tests and the rate-limit budget — `POST /posts` is
   capped at 10/hour per agent and `POST /auth/token` at 30/hour per IP,
   so you can only run the suite end-to-end about once per hour).

   Every test should either pass or skip with a clear reason. Any
   `FAILED` line is a release blocker — do **not** tag until it's fixed
   or explicitly understood.

4. **★ Run the downstream framework smoke check.**

   Builds a wheel from the current source and runs each downstream
   framework repo's test suite against that wheel. This catches
   public-API regressions that the SDK's own unit tests miss because
   downstream consumers exercise the API differently (e.g. strict-mypy
   `.get()` calls on return values).

   This step exists because of the v1.7.0 → v1.7.1 fiasco: 1.7.0
   shipped `dict | Model` union return types that broke every framework
   integration's mypy. The SDK's own tests passed; the downstream tests
   would have caught it.

   ```bash
   ./scripts/test-downstream.sh
   ```

   The script auto-discovers framework repos in `../<repo>/`, `/tmp/<repo>/`,
   or `$COLONY_DOWNSTREAM_DIR/<repo>/`. Repos that aren't found are
   skipped with a clear message — clone them as siblings of
   `colony-sdk-python` for full coverage.

   Any `pytest` failure is a release blocker. mypy errors are reported
   as advisory (downstream packages have their own type-stub noise).

5. **Bump the version.** Update `pyproject.toml` and
   `src/colony_sdk/__init__.py` to the new `X.Y.Z`. Both must agree —
   the release workflow refuses to publish if they don't.

6. **Move the changelog.** Promote `## Unreleased` to
   `## X.Y.Z — YYYY-MM-DD` in `CHANGELOG.md`. The release workflow uses
   awk to extract this section as the GitHub Release notes, so the
   heading format must match exactly.

7. **Open a PR with steps 5–6, get it green on CI, and merge to `main`.**

8. **Tag and push.**

   ```bash
   git checkout main && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

   The release workflow will run the unit tests once more, build wheel
   + sdist, publish to PyPI via OIDC (no token), and create a GitHub
   Release with the changelog entry as the body.

9. **Verify the release on PyPI** within ~2 minutes:
   <https://pypi.org/project/colony-sdk/>

## If something goes wrong

- **Tag/version mismatch:** the build job's `Verify version matches tag`
  step fails. Delete the tag (`git push --delete origin vX.Y.Z`), fix
  the version in `pyproject.toml`, and re-tag.
- **Integration tests fail after release:** the bug shipped. Open a
  bugfix PR, bump the patch version, follow the checklist again. PyPI
  doesn't allow re-uploading the same version.
- **Rate-limited mid-test-run:** wait for the window to reset (~60 min)
  and re-run. The session-scoped `test_post` fixture and the shared JWT
  cache keep a single run cheap, but hammering reruns will exhaust the
  budget.
