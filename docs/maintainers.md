# Maintainer infrastructure (landing and verification)

This document collects doeff's **maintainer-only** infrastructure. People who use doeff as a dependency or develop
it locally do not need it. For a normal install, the install line of the root `README.md` is enough; for
development, `## Development` in `CONTRIBUTING.md` (`make sync` and `uv run pytest`) is enough.

## Landing on main (since 2026-08-09)

Publishing (pushing to origin main) is done by the land queue window — push your change as a branch and enqueue it with `ai land request` (a direct push is blocked by a guard).

Since 2026-08-09, the upstream rule (main only advances with verified commits) is machine-enforced by the window's battery.

The machine that runs the window is decided by `policy.runner` in `.agents/land-queue.toml` — since 2026-09-14 it is `"pod"` (land-api in the brain pod on k3s, runner `land-runner@agora-1`, push key = the deploy key in Secret `land-key-doeff`). A registered Mac announces "this repo's window is run by the pod" and skips it. To return to a Mac window, delete the runner line.

## `.agents/land-queue.toml` is maintainer infrastructure

This file declares the landing window and holds the maintainer's verification infrastructure. `gate.full` (the
verbatim full check, used only by the daily verification run `ai land verify` and the full_paths run),
`gate.timeout_s`, the focus mode policy, the `UV_CACHE_DIR` lane separation, the choice of execution surface
(zeus / k3s), and `[test-admission]` (admission for running broad tests) all live here. **You do not need to read
this file to use doeff or to develop doeff locally.**

## Hygiene of tracked files (templates and fixtures)

`.gitignore` excludes runtime state (DB / SQLite / agent home / log) and generated artifacts.
`scripts/check-repo-hygiene.sh` checks that no file matching those exclusions is tracked by mistake.

When you **intentionally want to track** a file matching `.env.*` or `*.jsonl` (a config template, a test
fixture), do not loosen `.gitignore`; instead add the path to that script's allowlist (the `case` clause) and
**write the README or test that uses it in a comment on the same line**. The exclusion rules stay the repo-wide
default, and each exception is declared in one place with its name and purpose.

As of 2026-09-15, there is no file that belongs on this allowlist.

The `python/` rule refers **only to the temporary work dir at the repo root** (`/python/`). A `python/` inside a
package (e.g. `packages/doeff-indexer/python/`) is regular source and is not covered.
