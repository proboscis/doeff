# doeff Release Publish Runbook

This runbook covers the release flow for milestone M1 (release infrastructure).

## One-time Setup (Manual)

Complete these steps in PyPI before first publish:

1. Create projects on PyPI:
   - `doeff-vm`
   - `doeff-indexer`
   - `doeff`
2. Configure Trusted Publishing (OIDC) for this repository and workflows:
   - `.github/workflows/build-vm.yml`
   - `.github/workflows/build-indexer.yml`
   - `.github/workflows/publish.yml`
3. Ensure each package has the correct project maintainers/owners.

## Release Order

Publish in this strict order:

1. `doeff-vm`
2. `doeff-indexer`
3. `doeff`

The root publish workflow (`publish.yml`) now orchestrates this order by calling the package workflows
first, then publishing `doeff`.

## Preflight Checklist

1. Run tests locally:

```bash
uv run pytest
```

2. Verify version consistency:
   - `pyproject.toml` project version is updated.
   - `doeff.__version__` resolves via package metadata.

3. Verify URLs point to canonical repository:
   - `https://github.com/proboscis/doeff`

4. Build and verify distribution metadata (no local workspace-path leaks):

```bash
uv build --wheel --sdist
uv run python tools/verify_dist_metadata.py dist/*.whl dist/*.tar.gz
```

## Publish Commands

### Option A: Tag-driven (recommended)

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

This triggers `.github/workflows/publish.yml`, which will:
- publish `doeff-vm` (skip-existing enabled)
- publish `doeff-indexer` (skip-existing enabled)
- build + publish `doeff`

### Option B: Manual workflow dispatch

From GitHub Actions UI, run `Publish doeff` with `publish=true`.

## Post-publish Verification

1. Create a clean virtual environment.
2. Install from PyPI:

```bash
python -m venv .venv-release-check
source .venv-release-check/bin/activate
python -m pip install --upgrade pip
python -m pip install doeff
python -c "import doeff; print(doeff.__version__)"
```

3. Smoke test runtime:

```bash
python - <<'PY'
from doeff import Program, run
print(run(Program.pure("ok")).value)
PY
```

## Notes

- All publish jobs use Trusted Publishing (`id-token: write`).
- Package publish steps use `skip-existing: true` so reruns are idempotent.
- If dependency packages are already published for the target version, root publish can proceed safely.
