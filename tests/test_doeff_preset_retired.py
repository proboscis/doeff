"""ADR-DOE-PRESET-001: doeff-preset is retired.

The preset package bundled a display default (now owned by slog_handler per
ADR-DOE-CORE-EFFECTS-001 R2: stderr terminal sink) with config Ask defaults
(owned by reader/lazy_ask env at the entry point). It depended on removed APIs
(default_handlers, run_program(scoped_handlers=...), effect.message) and had no
consumers outside bit-rotted examples. Retired means: not in the workspace, not
importable, not referenced by code, and not in the publish matrix.
"""

import importlib.util
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Prose may keep mentioning doeff-preset as history (ADRs, plans, evidence).
# Operational surfaces may not — the publish runbook is explicitly denied so a
# retired package can never sit in the release matrix.
ALLOWED_REFERENCE_PREFIXES = (
    "docs/",
    "specs/",
    "Hypha/",
    "uv.lock",
    ".semgrep.yaml",
    "tests/test_doeff_preset_retired.py",
)
DENIED_REFERENCE_PATHS = ("docs/release-publish-runbook.md",)


def test_doeff_preset_not_importable():
    assert importlib.util.find_spec("doeff_preset") is None, (
        "doeff_preset is retired (ADR-DOE-PRESET-001) but still importable; "
        "remove it from the workspace and run `make sync`"
    )


def test_doeff_preset_package_dir_removed():
    assert not (REPO_ROOT / "packages" / "doeff-preset").exists(), (
        "packages/doeff-preset must be deleted (ADR-DOE-PRESET-001)"
    )


def test_no_tracked_references_to_doeff_preset():
    proc = subprocess.run(
        ["git", "grep", "-l", "-E", "doeff[_-]preset"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    offenders = [
        path
        for path in proc.stdout.splitlines()
        if path
        and (
            path in DENIED_REFERENCE_PATHS
            or not path.startswith(ALLOWED_REFERENCE_PREFIXES)
        )
    ]
    assert offenders == [], (
        "doeff-preset is retired (ADR-DOE-PRESET-001); these tracked files still "
        f"reference it: {offenders}"
    )
