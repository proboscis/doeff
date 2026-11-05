"""The key for looking up the answer of an installed defsemgrep (the ids of the rules that hit one example set), and the place that keeps answers outside the process.

The examples of rules with the same configuration are checked together in one semgrep run (registry._installed_verdict), but the remaining cost is one semgrep
startup itself (about 8 seconds), which the agora daily run paid every time, per pytest session and per configuration, even on days when neither configuration nor examples changed. If the caller
names the place's dir with the environment variable SEMGREP_VERDICTS_ENV, answers are kept in ``<dir>/<key>.json`` and the next process reads from there. Without a name
nothing is kept (remembered only inside the process) — doeff-adr knows neither the daily run's name nor the root of the place.

The key is the semgrep version, the contents of the configuration, the contents of the examples (and the format version of the place); the paths of semgrep and the configuration are not included. The daily run replaces HOME with an empty dir on each run,
and semgrep (``uv tool install``) and the configuration (clone) are at different paths every time, so including a path would miss every time. Lookup is by contents, so a run in which one rule or example
changed gets a different key and semgrep runs again — old answers are not read.
"""

import hashlib
import json
import os
import tempfile
from functools import cache
from importlib.metadata import Distribution
from pathlib import Path

from doeff_adr._env_places import SEMGREP_VERDICTS_ENV, environment_value

# Bump this when the key material or the file format of the place changes (answers of the old version get a different key and are not read).
VERDICT_FORMAT = 1


class MalformedVerdictError(ValueError):
    """The shape of an answer file in the place is wrong (not a list of rule id strings)."""


def verdict_store() -> Path | None:
    """The dir that keeps answers (the value of the environment variable SEMGREP_VERDICTS_ENV) — None if not named or empty (nothing is kept outside the process)."""
    named = (environment_value(SEMGREP_VERDICTS_ENV) or "").strip()
    return Path(named).expanduser() if named else None


@cache
def semgrep_identity(semgrep: str) -> str:
    """The identity of semgrep — the same version gives the same answer even at a different path, so the key holds the version, not the path.

    The version is read from the dist-info of the venv that holds the executable (after resolving symlinks) (both ``uv tool install`` and a project venv have the shape
    ``<venv>/bin/semgrep`` and ``<venv>/lib/python*/site-packages/semgrep-*.dist-info``). ``semgrep --version`` goes to the network to ask
    for a newer version and took 37 seconds (2026-10-11, local), so it is not used. For an install that is not shaped like a venv (the dist-info is not unique),
    the resolved path is the identity — an install whose path changes per version (Homebrew's Cellar and others) tells versions apart, and even an install that replaces the same path
    does not mix the answer of that path with the answer of another path.
    """
    executable = Path(semgrep).resolve()
    dist_infos = sorted(
        (executable.parent.parent / "lib").glob("python*/site-packages/semgrep-*.dist-info")
    )
    if len(dist_infos) == 1:
        return f"semgrep=={Distribution.at(dist_infos[0]).version}"
    return f"semgrep@{executable}"


def fixture_set_key(identity: str, config_digest: str, fixtures: tuple[dict[str, str], ...]) -> str:
    """The key for looking up an answer — a change in any of the semgrep version, the configuration contents or the example contents gives a different key, so old answers are not reused."""
    material = json.dumps([VERDICT_FORMAT, identity, config_digest, list(fixtures)], sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def read_verdict(store: Path, key: str) -> frozenset[str] | None:
    """The answer for a key that a previous process left in the place — so semgrep need not be started if it exists (None if not)."""
    path = store / f"{key}.json"
    return _decode(path) if path.is_file() else None


def write_verdicts(store: Path, verdicts: dict[str, frozenset[str]]) -> None:
    """Write an answer to a per-key file — written to a temporary file in the same dir and then swapped in, so that a reader never sees a half-written file
    even if a neighbouring session writes the same key at the same time."""
    store.mkdir(parents=True, exist_ok=True)
    for key, rule_ids in verdicts.items():
        with tempfile.NamedTemporaryFile(
            "w", dir=store, prefix=f".{key}.", suffix=".tmp", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(sorted(rule_ids), handle)
        os.replace(handle.name, store / f"{key}.json")


def _decode(path: Path) -> frozenset[str]:
    """Turn an answer file into a set of rule ids — a file of the wrong shape is not silently read as "nothing hit" but is stopped with its path named."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(rule_id, str) for rule_id in value):
        raise MalformedVerdictError(
            f"the shape of the semgrep answer file is wrong (not a list of rule id strings): {path}"
        )
    return frozenset(value)
