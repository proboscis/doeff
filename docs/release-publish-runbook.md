# doeff Release Publish Runbook

## One-time Setup

1. Get an account-scoped PyPI API token from https://pypi.org/manage/account/token/
2. For CI: add it as `PYPI_API_TOKEN` in GitHub repo settings -> Secrets and variables -> Actions
3. For local: set `UV_PUBLISH_TOKEN` in your shell environment

## Release Ownership Contract

Packages are split by release owner. A package with a user-facing install command in
`packages/*/README.md` must appear in either the root tag release table or the independent public
package table. Packages outside both tables are not part of the public PyPI release surface.

### Root Tag Release Packages

These packages are published from the root `vX.Y.Z` tag by `.github/workflows/publish.yml`.
The workflow must build wheel and sdist artifacts and run `tools/verify_dist_metadata.py` before
publishing every Python package in this table.

| Package | Class | Publish gate | Order |
| --- | --- | --- | --- |
| `doeff-vm` | native helper | `build-vm.yml` publishes platform wheels | 1 |
| `doeff-indexer` | native helper | `build-indexer.yml` publishes platform wheels | 2 |
| `doeff-hy` | public macro/runtime helper | root tag Python dist matrix | 3 |
| `doeff-core-effects` | root runtime dependency | root tag Python dist matrix | 4 |
| `doeff` | root package | root tag Python dist matrix | 5 |
| `doeff-time` | public effect package used by `doeff-agents` | root tag Python dist matrix | 6 |
| `doeff-preset` | public preset package used by examples/extras | root tag Python dist matrix | 7 |
| `doeff-agents` | public companion package | root tag Python dist matrix | 8 |

### Independent Public Packages

These packages are public, but they are not published by the root `vX.Y.Z` release. Publish them
with a package-scoped release note and tag name: `<package>/vX.Y.Z`. Build and metadata
verification are still mandatory before publishing.

| Package | Class | Dependency order |
| --- | --- | --- |
| `doeff-llm` | provider-agnostic LLM effects | after `doeff` |
| `doeff-image` | provider-agnostic image effects | after `doeff` |
| `doeff-secret` | provider-agnostic secret effects | after `doeff` |
| `doeff-events` | event effects | after `doeff` |
| `doeff-traverse` | traversal effects | after `doeff-core-effects`, `doeff-time` |
| `doeff-notify` | notification effects | after `doeff` |
| `doeff-git` | Git effects used by orchestration packages | after `doeff` |
| `doeff-flow` | workflow trace package | after `doeff` |
| `doeff-adr` | executable ADR contract package | after `doeff-hy` |
| `doeff-agentic` | workflow/session package | after `doeff-flow`, `doeff-agents` |
| `doeff-conductor` | orchestration package | after `doeff-git`, `doeff-agentic`, `doeff-flow` |
| `doeff-openai` | LLM provider package | after `doeff-llm` |
| `doeff-openrouter` | LLM provider package | after `doeff-llm` |
| `doeff-gemini` | LLM/image provider package | after `doeff-llm`, `doeff-image` |
| `doeff-seedream` | image provider package | after `doeff-image` |
| `doeff-google-secret-manager` | secret provider package | after `doeff-secret` |

### Not Publicly Published

Do not publish these packages from the root tag release. If one becomes public, add a README install
surface, choose a release owner above, and add the matching build and metadata verification gate in
the same PR.

| Package | Class | Reason |
| --- | --- | --- |
| `doeff-agentd` | experimental service helper | proposed daemon surface is still being designed |
| `doeff-agentic-cli` | native helper | distributed with the agentic CLI workflow, not a public PyPI package |
| `doeff-docker` | experimental runtime adapter | no public install contract yet |
| `doeff-ml-nexus` | experimental runtime adapter | no public install contract yet |
| `doeff-effect-analyzer` | native analysis helper | development tooling, not part of the PyPI user surface |
| `doeff-linter` | native analysis helper | installed with Cargo tooling, not the PyPI release |
| `doeff-test-target` | test fixture package | fixture only |
| `doeff-vm-core` | internal native crate/package | implementation detail of `doeff-vm` |

## Release Order

Publish the root tag release in this strict order:

1. `doeff-vm`
2. `doeff-indexer`
3. `doeff-hy`
4. `doeff-core-effects`
5. `doeff`
6. `doeff-time`
7. `doeff-preset`
8. `doeff-agents`

Independent public packages are published separately. Their dependency order is listed in the
independent public package table above.

This runbook defines release ownership only. Dependency-cycle cleanup, root wheel typing markers,
and standard package test coverage remain separate work items and should not be folded into a
publish contract change.

## Preflight Checklist

1. Run tests locally:

```bash
uv run pytest
```

2. Bump versions for every package being published. For root releases, check all packages in the
root tag release table plus `doeff/__init__.py` when the root package version changes.

3. Build and verify distribution metadata for every root tag Python package:

```bash
for package in doeff-hy doeff-core-effects doeff doeff-time doeff-preset doeff-agents; do
  rm -rf "/tmp/${package}-dist"
  uv build --package "${package}" --wheel --sdist --out-dir "/tmp/${package}-dist"
  uv run python tools/verify_dist_metadata.py \
    "/tmp/${package}-dist"/*.whl \
    "/tmp/${package}-dist"/*.tar.gz
done
```

4. Confirm native package release workflows for `doeff-vm` and `doeff-indexer` are green before
publishing the root package stack.

## Publishing

### Option A: Local root release publish

```bash
# 1. Build and publish doeff-vm
cd packages/doeff-vm
rm -rf dist/
uv build --wheel --sdist
uv run python ../../tools/verify_dist_metadata.py dist/*.whl dist/*.tar.gz
uv publish dist/*

# 2. Build and publish doeff-indexer
cd ../doeff-indexer
rm -rf dist/
uv build --wheel --sdist
uv run python ../../tools/verify_dist_metadata.py dist/*.whl dist/*.tar.gz
uv publish dist/*

# 3. Build and publish root tag Python packages from repo root
cd ../..
for package in doeff-hy doeff-core-effects doeff doeff-time doeff-preset doeff-agents; do
  rm -rf "/tmp/${package}-dist"
  uv build --package "${package}" --wheel --sdist --out-dir "/tmp/${package}-dist"
  uv run python tools/verify_dist_metadata.py \
    "/tmp/${package}-dist"/*.whl \
    "/tmp/${package}-dist"/*.tar.gz
  uv publish "/tmp/${package}-dist"/*
done
```

Note: `uv publish` uses the `UV_PUBLISH_TOKEN` env var automatically.

### Option B: CI tag-driven root release publish

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

This triggers `.github/workflows/publish.yml`, which will:

- publish `doeff-vm` via `build-vm.yml` (all platforms)
- publish `doeff-indexer` via `build-indexer.yml` (all platforms)
- build wheel and sdist artifacts for `doeff-hy`, `doeff-core-effects`, `doeff`,
  `doeff-time`, `doeff-preset`, and `doeff-agents`
- verify every Python distribution with `tools/verify_dist_metadata.py`
- publish the Python packages in the root tag release order

Requires `PYPI_API_TOKEN` secret in GitHub repo settings.

### Option C: Manual workflow dispatch

From GitHub Actions UI, run `Publish doeff` with `publish=true`.

## Independent Public Package Publish

Use this flow only for packages listed in the independent public package table. The tag name is for
release tracking; the root `Publish doeff` workflow does not publish these package-scoped tags.

```bash
package=<package>
git tag "${package}/vX.Y.Z"
git push origin "${package}/vX.Y.Z"

rm -rf "/tmp/${package}-dist"
uv build --package <package> --wheel --sdist --out-dir "/tmp/${package}-dist"
uv run python tools/verify_dist_metadata.py \
  "/tmp/${package}-dist"/*.whl \
  "/tmp/${package}-dist"/*.tar.gz
uv publish "/tmp/${package}-dist"/*
```

Before publishing an independent provider package, publish its provider-agnostic effect package
first. For example, publish `doeff-llm` before `doeff-openai`, `doeff-openrouter`, or
`doeff-gemini`; publish `doeff-image` before `doeff-seedream` or the image side of
`doeff-gemini`; publish `doeff-secret` before `doeff-google-secret-manager`.

## Post-publish Verification

```bash
python -m venv /tmp/doeff-release-check
/tmp/doeff-release-check/bin/pip install --upgrade pip
/tmp/doeff-release-check/bin/pip install doeff doeff-agents
/tmp/doeff-release-check/bin/python -c "
from doeff import Program, run
print(run(Program.pure('ok')).value)
"
/tmp/doeff-release-check/bin/python -c "import doeff_agents; print('agents ok')"
```

For independent public packages, create a fresh virtual environment and install the exact package
name from PyPI after publish.

## Notes

- CI publish uses `skip-existing: true` so reruns are idempotent.
- Local native builds only produce wheels for your current platform/Python version.
- CI native builds produce wheels for linux/macos/windows x x86_64/aarch64.
- The sdist always works as a fallback (pip builds from source).
