# doeff Release Publish Runbook

## One-time Setup

1. Get an account-scoped PyPI API token from https://pypi.org/manage/account/token/
2. For CI: add it as `PYPI_API_TOKEN` in GitHub repo settings → Secrets and variables → Actions
3. For local: set `UV_PUBLISH_TOKEN` in your shell environment

## Release Order

Publish in this strict order (root `doeff` depends on the others):

1. `doeff-vm`
2. `doeff-indexer`
3. `doeff`

## Preflight Checklist

1. Run tests locally:

```bash
uv run pytest
```

2. Bump versions in `pyproject.toml` and `doeff/__init__.py` if needed.

3. Build and verify distribution metadata (no local workspace-path leaks):

```bash
uv build --wheel --sdist
uv run python tools/verify_dist_metadata.py dist/*.whl dist/*.tar.gz
```

## Publishing

### Option A: Local publish (recommended for now)

```bash
# 1. Build and publish doeff-vm
cd packages/doeff-vm
rm -rf dist/
uv build --wheel --sdist
uv publish dist/*

# 2. Build and publish doeff-indexer
cd ../doeff-indexer
rm -rf dist/
uv build --wheel --sdist
uv publish dist/*

# 3. Build and publish doeff (from repo root)
cd ../..
rm -f dist/doeff-*
uv build --wheel --sdist
uv publish dist/doeff-*
```

Note: `uv publish` uses the `UV_PUBLISH_TOKEN` env var automatically.

### Option B: CI tag-driven publish

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

This triggers `.github/workflows/publish.yml`, which will:
- publish `doeff-vm` via `build-vm.yml` (all platforms)
- publish `doeff-indexer` via `build-indexer.yml` (all platforms)
- build + publish `doeff`

Requires `PYPI_API_TOKEN` secret in GitHub repo settings.

### Option C: Manual workflow dispatch

From GitHub Actions UI, run `Publish doeff` with `publish=true`.

## Post-publish Verification

```bash
python -m venv /tmp/doeff-release-check
/tmp/doeff-release-check/bin/pip install --upgrade pip
/tmp/doeff-release-check/bin/pip install doeff
/tmp/doeff-release-check/bin/python -c "
from doeff import Program, run
print(run(Program.pure('ok')).value)
"
```

## Publishing Other Subpackages

Each package in `packages/` can be published independently:

```bash
cd packages/doeff-openai
uv build --wheel --sdist
uv publish dist/*
```

No per-project PyPI config needed if using an account-scoped API token.

## Notes

- CI publish uses `skip-existing: true` so reruns are idempotent.
- Local builds only produce wheels for your current platform/Python version.
- CI builds produce wheels for linux/macos/windows × x86_64/aarch64.
- The sdist always works as a fallback (pip builds from source).
