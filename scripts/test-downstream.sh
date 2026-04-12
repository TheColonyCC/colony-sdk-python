#!/usr/bin/env bash
#
# test-downstream.sh — install the local colony-sdk wheel into each
# downstream framework repo and run their test suite + mypy.
#
# Why: colony-sdk 1.7.0 broke every downstream consumer running strict
# mypy because we changed read-method return annotations from `dict` to
# `dict | Model`. The SDK's own unit tests passed because they don't
# `.get()` on the return value the way framework wrappers do. This
# script catches that class of regression by actually running each
# downstream's tests against the local SDK source.
#
# Usage:
#   ./scripts/test-downstream.sh                       # all known repos
#   ./scripts/test-downstream.sh langchain-colony      # one repo
#   COLONY_DOWNSTREAM_DIR=~/code ./scripts/test-downstream.sh
#
# Repos are auto-discovered in this order:
#   1. $COLONY_DOWNSTREAM_DIR (if set)
#   2. ../<repo>/  (sibling directories of colony-sdk-python)
#   3. /tmp/<repo>/
#
# Skips repos that aren't found locally and tells you which path it
# tried — clone them if you want a fuller signal.
#
# Run this BEFORE every release. It's the equivalent of an integration
# test for the SDK's public API contract.

set -euo pipefail

# ── Repos to check ───────────────────────────────────────────────────

DOWNSTREAM_REPOS=(
    "langchain-colony"
    "crewai-colony"
    "openai-agents-colony"
    "smolagents-colony"
    "pydantic-ai-colony"
)

# ── Paths and helpers ────────────────────────────────────────────────

SDK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHEEL_DIR="$SDK_ROOT/dist"

# Filter to a specific repo if the user passed an arg
if [[ $# -gt 0 ]]; then
    DOWNSTREAM_REPOS=("$@")
fi

# Color helpers — disabled when stdout isn't a TTY (CI, pipes).
if [[ -t 1 ]]; then
    RED=$'\033[31m'
    GREEN=$'\033[32m'
    YELLOW=$'\033[33m'
    BOLD=$'\033[1m'
    RESET=$'\033[0m'
else
    RED='' GREEN='' YELLOW='' BOLD='' RESET=''
fi

log_step() { echo "${BOLD}==>${RESET} $*"; }
log_pass() { echo "${GREEN}✓${RESET} $*"; }
log_fail() { echo "${RED}✗${RESET} $*"; }
log_skip() { echo "${YELLOW}⊘${RESET} $*"; }

find_repo() {
    local name="$1"
    local candidates=(
        "${COLONY_DOWNSTREAM_DIR:-}/$name"
        "$SDK_ROOT/../$name"
        "/tmp/$name"
    )
    for path in "${candidates[@]}"; do
        if [[ -n "$path" && -f "$path/pyproject.toml" ]]; then
            echo "$path"
            return 0
        fi
    done
    return 1
}

# ── Build the SDK wheel from the current source ──────────────────────

log_step "Building colony-sdk wheel from $SDK_ROOT"
rm -rf "$WHEEL_DIR"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || { echo "python3 not found; set PYTHON=path/to/python"; exit 1; }
(cd "$SDK_ROOT" && "$PYTHON" -m build --wheel --quiet 2>&1 | tail -5)

WHEEL=$(ls -t "$WHEEL_DIR"/colony_sdk-*.whl 2>/dev/null | head -1)
if [[ -z "$WHEEL" ]]; then
    log_fail "wheel build produced no output in $WHEEL_DIR"
    exit 1
fi
log_pass "built $(basename "$WHEEL")"
echo

# ── Run downstream tests in isolated venvs ───────────────────────────

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
FAILED_REPOS=()

for repo in "${DOWNSTREAM_REPOS[@]}"; do
    log_step "Checking $repo"
    repo_path=$(find_repo "$repo") || {
        log_skip "$repo not found locally (set COLONY_DOWNSTREAM_DIR or clone to ../$repo)"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        echo
        continue
    }
    echo "  path: $repo_path"

    # Use a temporary venv per repo so we don't pollute the user's env
    # and each downstream gets a clean dependency tree.
    venv_dir=$(mktemp -d -t "colony-downstream-${repo}-XXXXXX")
    trap 'rm -rf "$venv_dir"' EXIT

    # Prefer uv for venv creation if available — it's ~10x faster than
    # python -m venv and doesn't depend on the python3-venv package
    # being installed system-wide.
    if command -v uv >/dev/null 2>&1; then
        uv venv "$venv_dir" --python "$PYTHON" --quiet
    else
        "$PYTHON" -m venv "$venv_dir"
    fi

    PIP="$venv_dir/bin/pip"
    PY="$venv_dir/bin/python"
    # uv venv doesn't ship pip; install it on-demand for repos that need it
    if [[ ! -f "$PIP" ]]; then
        if command -v uv >/dev/null 2>&1; then
            VIRTUAL_ENV="$venv_dir" uv pip install --quiet pip
        else
            "$PY" -m ensurepip --quiet
        fi
    fi

    # Install the local SDK wheel + the downstream's dev dependencies.
    # Use --quiet to keep noise down; failures will surface via exit codes.
    if ! "$PIP" install --quiet --upgrade pip 2>&1 | tail -3; then
        log_fail "  failed to upgrade pip in venv"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_REPOS+=("$repo (venv setup)")
        rm -rf "$venv_dir"
        echo
        continue
    fi

    # Install downstream first (pulls colony-sdk from PyPI), then force-reinstall
    # the local wheel on top so we test against this branch's SDK changes.
    if ! "$PIP" install --quiet -e "$repo_path[dev]" 2>&1 | tail -10; then
        log_fail "  failed to install $repo[dev]"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_REPOS+=("$repo (deps)")
        rm -rf "$venv_dir"
        echo
        continue
    fi
    "$PIP" install --quiet --force-reinstall --no-deps "$WHEEL"

    installed_version=$("$PY" -c "import colony_sdk; print(colony_sdk.__version__)")
    echo "  testing against colony-sdk $installed_version (local wheel)"

    # Run the downstream's test suite. Each downstream sets its own
    # pytest config; we just shell out and trust their conftest. Skip
    # integration tests across the board (they need API keys).
    cd "$repo_path"
    test_failed=0
    if ! "$venv_dir/bin/pytest" --ignore=tests/integration --ignore=tests/test_integration.py -q 2>&1 | tail -25; then
        test_failed=1
    fi

    # Run mypy too if the downstream has it as a dev dep. This catches
    # the exact class of regression that v1.7.0 shipped — type-only
    # breakage that pytest doesn't see because the unit tests use raw
    # dicts without strict type-checking the return values.
    #
    # mypy is *advisory*, not gating. Downstream repos pull in their
    # own dependencies (langchain, smolagents, langgraph, etc.) which
    # often lack py.typed markers and produce baseline noise. We log
    # the mypy delta but don't fail the release on it. Compare against
    # main if you want a clean signal.
    mypy_count=""
    if [[ -d "$repo_path/src" ]] && "$venv_dir/bin/python" -c "import mypy" 2>/dev/null; then
        mypy_out=$("$venv_dir/bin/mypy" "$repo_path/src" 2>&1 || true)
        mypy_count=$(echo "$mypy_out" | grep -c "^.*error:" || true)
        if [[ "$mypy_count" -gt 0 ]]; then
            echo "  mypy: ${YELLOW}$mypy_count error(s) (advisory; not a release gate)${RESET}"
        else
            echo "  mypy: ${GREEN}clean${RESET}"
        fi
    fi

    cd "$SDK_ROOT"

    if [[ $test_failed -eq 0 ]]; then
        log_pass "$repo"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        log_fail "$repo (pytest)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        FAILED_REPOS+=("$repo (pytest)")
    fi

    rm -rf "$venv_dir"
    echo
done

# ── Summary ──────────────────────────────────────────────────────────

echo "──────────────────────────────────────────────"
log_step "Downstream check complete"
echo "  passed:  $PASS_COUNT"
echo "  failed:  $FAIL_COUNT"
echo "  skipped: $SKIP_COUNT"

if [[ $FAIL_COUNT -gt 0 ]]; then
    echo
    echo "${RED}${BOLD}Failed repos:${RESET}"
    for r in "${FAILED_REPOS[@]}"; do
        echo "  - $r"
    done
    echo
    echo "${RED}Do not release. Fix the SDK or open issues against the failing downstream repos.${RESET}"
    exit 1
fi

if [[ $PASS_COUNT -eq 0 ]]; then
    echo
    echo "${YELLOW}No downstream repos were tested. Set COLONY_DOWNSTREAM_DIR or clone the framework repos as siblings.${RESET}"
    exit 1
fi

echo
echo "${GREEN}${BOLD}All downstream repos pass against the local SDK.${RESET}"
