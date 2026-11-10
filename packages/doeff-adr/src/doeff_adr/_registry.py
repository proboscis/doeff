"""Runtime registry for executable ADR contracts.

Hy macros expand into calls in this module, except where they perform effects:
``defsemgrep`` performs RegisterSemgrep and AssertSemgrep
(``doeff_adr._semgrep_effects``), whose handler answers them with
``_register_semgrep_enforcement`` and ``_assert_semgrep_enforcement`` here, and
``defadr`` performs RegisterDeftest, RegisterAdr and AssertAdrContract
(``doeff_adr._effects``), whose handler answers them with
``_register_deftest_enforcement``, ``_register_adr`` and
``_assert_adr_contract`` here.  The registry is intentionally small: it
records ADR specs and executable enforcement specs, then exposes assertions
that pytest-generated functions can call.
"""

import hashlib
import json
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from doeff_adr._semgrep_verdicts import (
    fixture_set_key,
    read_verdict,
    semgrep_identity,
    verdict_store,
    write_verdicts,
)

AdrStatus = Literal["proposed", "accepted", "superseded", "rejected"]
EnforcementMode = Literal["green", "expected-red"]
FixtureSpec = str | dict[str, str]

ADR_STATUSES: set[str] = {"proposed", "accepted", "superseded", "rejected"}
ENFORCEMENT_MODES: set[str] = {"green", "expected-red"}


@dataclass(frozen=True)
class EnforcementRef:
    id: str
    kind: str
    mode: EnforcementMode = "green"


@dataclass(frozen=True)
class SemgrepSpec:
    id: str
    pattern: str | None = None
    installed_rule_id: str | None = None
    config: str = ".semgrep.yaml"
    # Directory a relative ``config`` is resolved against.  ``None`` keeps the
    # legacy lookup: the nearest ancestor of pytest's cwd that carries ``config``.
    config_base: str | None = None
    # Installed form only: expand ``.hy`` fixtures to Python before scanning.
    expand_hy: bool = False
    languages: tuple[str, ...] = ("generic",)
    message: str = "ADR Semgrep enforcement failed"
    severity: str = "ERROR"
    bad: tuple[FixtureSpec, ...] = ()
    good: tuple[FixtureSpec, ...] = ()
    hit_fixtures: tuple[dict[str, str], ...] = ()
    clean_fixtures: tuple[dict[str, str], ...] = ()
    mode: EnforcementMode = "green"


@dataclass(frozen=True)
class SemgrepResult:
    """The fields the checker reads from one entry of the semgrep --json result — typed values at the JSON boundary so callers do not index into a dict."""

    check_id: str
    path: str
    line: int
    message: str


@dataclass(frozen=True)
class AdrSpec:
    id: str
    title: str
    status: AdrStatus
    scope: tuple[str, ...] = ()
    problem: tuple[Any, ...] = ()
    context: tuple[Any, ...] = ()
    decision: tuple[Any, ...] = ()
    laws: tuple[Any, ...] = ()
    enforcement: tuple[EnforcementRef, ...] = ()
    plans: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


_ADRS: dict[str, AdrSpec] = {}
_ENFORCEMENTS: dict[str, EnforcementRef | SemgrepSpec] = {}
# Ids of the rules that matched one installed defsemgrep example group (key = semgrep version, config contents, example contents digest — semgrep_verdicts).
# Examples for rules that read the same config are checked in one semgrep run; the remaining checks in the same process read from here.
_INSTALLED_VERDICTS: dict[str, frozenset[str]] = {}


def _clear_registry(
    *,
    adrs: dict[str, AdrSpec] | None = None,
    table: dict[str, EnforcementRef | SemgrepSpec] | None = None,
) -> None:
    """Empty the ADR table and the enforcement table (None = the process registry). From outside, empty them with the effect ClearRegistry (answered by the handler adr_registry
    for it)."""
    (_ADRS if adrs is None else adrs).clear()
    (_ENFORCEMENTS if table is None else table).clear()


@contextmanager
def isolated_registry() -> Generator[None]:
    """Run the body with an empty registry and restore the previous registry on exit.

    ADRs imported during collection are registered in the registry at collection time — if a test returns with the registry left empty, the tests for that ADR's articles that run later
    fail with KeyError. Use this when a test wants to empty the registry.

    A test that only reads and writes through effects can instead use a separate registry installed with the handler adr_registry(specs=[], enforcements={}).
    This is needed only by tests that exercise the place where the expansion of the macros defadr and defsemgrep registers into the process registry at import time (the expansion
    installs its own handler, so it cannot be swapped from outside).
    """
    saved_adrs = dict(_ADRS)
    saved_enforcements = dict(_ENFORCEMENTS)
    _clear_registry()
    try:
        yield
    finally:
        _clear_registry()
        _ADRS.update(saved_adrs)
        _ENFORCEMENTS.update(saved_enforcements)


def _keyword_to_text(value: Any) -> str:
    text = str(value)
    if text.startswith(":"):
        return text[1:]
    return text


def _normalize_adr_status(value: Any) -> AdrStatus:
    status = _keyword_to_text(value).lower()
    if status not in ADR_STATUSES:
        raise ValueError(f"unsupported ADR status: {value!r}")
    return status  # type: ignore[return-value]


def _normalize_enforcement_mode(value: Any) -> EnforcementMode:
    mode = _keyword_to_text(value).lower()
    if mode not in ENFORCEMENT_MODES:
        raise ValueError(f"unsupported enforcement mode: {value!r}")
    return mode  # type: ignore[return-value]


def _ensure_new_enforcement_id(
    enforcement_id: str, table: dict[str, EnforcementRef | SemgrepSpec] | None = None
) -> None:
    if enforcement_id in (_ENFORCEMENTS if table is None else table):
        raise ValueError(f"duplicate ADR enforcement id: {enforcement_id}")


def _tuple_of_text(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    return tuple(_keyword_to_text(value) for value in values)


def _tuple_of_any(values: Any) -> tuple[Any, ...]:
    if values is None:
        return ()
    return tuple(values)


def make_fact(text: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "fact", "text": text, **extra}


def make_interpretation(text: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "interpretation", "text": text, **extra}


def make_rule(rule_id: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "rule", "id": rule_id, "text": text, **extra}


def make_counterexample(text: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "counterexample", "text": text, **extra}


def make_law(law_id: str, statement: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "law", "id": law_id, "statement": statement, **extra}


def enforcement_ref(
    enforcement_id: str,
    *,
    kind: str = "unknown",
    mode: EnforcementMode = "green",
) -> EnforcementRef:
    return EnforcementRef(id=enforcement_id, kind=kind, mode=mode)


def _register_deftest_enforcement(
    enforcement_id: str,
    *,
    table: dict[str, EnforcementRef | SemgrepSpec] | None = None,
    mode: EnforcementMode = "green",
) -> EnforcementRef:
    """Register a deftest enforcement in the table (None = the process registry). From outside, register it with the effect RegisterDeftest (answered by the handler
    adr_registry)."""
    _ensure_new_enforcement_id(enforcement_id, table)
    normalized_mode = _normalize_enforcement_mode(mode)
    ref = EnforcementRef(id=enforcement_id, kind="deftest", mode=normalized_mode)
    (_ENFORCEMENTS if table is None else table)[enforcement_id] = ref
    return ref


def _register_semgrep_enforcement(
    enforcement_id: str,
    *,
    table: dict[str, EnforcementRef | SemgrepSpec] | None = None,
    pattern: str | None = None,
    rule_id: str | None = None,
    config: str = ".semgrep.yaml",
    declared_in: str | None = None,
    expand_hy: bool = False,
    languages: list[str] | tuple[str, ...] | None = None,
    message: str = "ADR Semgrep enforcement failed",
    severity: str = "ERROR",
    bad: list[FixtureSpec] | tuple[FixtureSpec, ...] | None = None,
    good: list[FixtureSpec] | tuple[FixtureSpec, ...] | None = None,
    hit_fixtures: list[dict[str, str]] | tuple[dict[str, str], ...] | None = None,
    clean_fixtures: list[dict[str, str]] | tuple[dict[str, str], ...] | None = None,
    mode: EnforcementMode = "green",
) -> SemgrepSpec:
    """Register a Semgrep enforcement in the table (None = the process registry). From outside, register it with the effect RegisterSemgrep (answered by the handler
    semgrep_enforcements)."""
    _ensure_new_enforcement_id(enforcement_id, table)
    if pattern is None and rule_id is None:
        raise ValueError("defsemgrep requires either pattern= or rule_id=")
    normalized_mode = _normalize_enforcement_mode(mode)
    spec = SemgrepSpec(
        id=enforcement_id,
        pattern=pattern,
        installed_rule_id=rule_id,
        config=config,
        config_base=None if declared_in is None else str(Path(declared_in).resolve().parent),
        expand_hy=bool(expand_hy),
        languages=tuple(languages or ("generic",)),
        message=message,
        severity=severity,
        bad=tuple(bad or ()),
        good=tuple(good or ()),
        hit_fixtures=tuple(hit_fixtures or ()),
        clean_fixtures=tuple(clean_fixtures or ()),
        mode=normalized_mode,
    )
    (_ENFORCEMENTS if table is None else table)[enforcement_id] = spec
    return spec


def _register_adr(
    adr_id: str,
    *,
    title: str,
    status: str,
    scope: list[Any] | tuple[Any, ...] | None = None,
    problem: list[Any] | tuple[Any, ...] | None = None,
    context: list[Any] | tuple[Any, ...] | None = None,
    decision: list[Any] | tuple[Any, ...] | None = None,
    laws: list[Any] | tuple[Any, ...] | None = None,
    enforcement: list[Any] | tuple[Any, ...] | None = None,
    plans: list[Any] | tuple[Any, ...] | None = None,
    metadata: dict[str, Any] | None = None,
    adrs: dict[str, AdrSpec] | None = None,
    table: dict[str, EnforcementRef | SemgrepSpec] | None = None,
) -> AdrSpec:
    """Register an ADR in the table (adrs; table None = the process registry). The kind of an enforcement id is read from the enforcement table `table`.
    From outside, register it with the effect RegisterAdr (answered by the handler adr_registry; the expansion of the macro defadr does the same)."""
    by_id = _ADRS if adrs is None else adrs
    if adr_id in by_id:
        raise ValueError(f"duplicate ADR id: {adr_id}")
    normalized_status = _normalize_adr_status(status)
    enforcements = _ENFORCEMENTS if table is None else table
    refs = tuple(_coerce_enforcement_ref(item, enforcements) for item in _tuple_of_any(enforcement))
    spec = AdrSpec(
        id=adr_id,
        title=title,
        status=normalized_status,  # type: ignore[arg-type]
        scope=_tuple_of_text(scope),
        problem=_tuple_of_any(problem),
        context=_tuple_of_any(context),
        decision=_tuple_of_any(decision),
        laws=_tuple_of_any(laws),
        enforcement=refs,
        plans=_tuple_of_text(plans),
        metadata={} if metadata is None else dict(metadata),
    )
    by_id[adr_id] = spec
    return spec


def _coerce_enforcement_ref(
    item: Any, enforcements: dict[str, EnforcementRef | SemgrepSpec]
) -> EnforcementRef:
    """Turn one :enforcement item of defadr into the enforcement reference written in the ADR (the kind of the id is read from the enforcements table)."""
    if isinstance(item, EnforcementRef):
        return item
    if isinstance(item, SemgrepSpec):
        return EnforcementRef(id=item.id, kind="defsemgrep", mode=item.mode)
    if isinstance(item, str):
        known = enforcements.get(item)
        if isinstance(known, SemgrepSpec):
            return EnforcementRef(id=item, kind="defsemgrep", mode=known.mode)
        if isinstance(known, EnforcementRef):
            return known
        return EnforcementRef(id=item, kind="unknown")
    raise TypeError(f"unsupported ADR enforcement reference: {item!r}")


def _adr_ids() -> list[str]:
    """List the ids of the ADRs in the registry. From outside, read them with the effect AdrIds (answered by the handler adr_registry)."""
    return sorted(_ADRS)


def _enforcement_ids(table: dict[str, EnforcementRef | SemgrepSpec] | None = None) -> list[str]:
    """List the ids in the enforcement table (None = the process registry). From outside, read them with the effect EnforcementIds (answered by the handler adr_registry)."""
    return sorted(_ENFORCEMENTS if table is None else table)


def _get_adr(adr_id: str) -> AdrSpec:
    """Look up an ADR in the registry by id. From outside, look it up with the effect GetAdr (answered by the handler adr_registry)."""
    return _ADRS[adr_id]


def _get_enforcement(
    enforcement_id: str, *, table: dict[str, EnforcementRef | SemgrepSpec] | None = None
) -> EnforcementRef | SemgrepSpec:
    """Look up an enforcement in the enforcement table (None = the process registry) by id. From outside, look it up with the effect GetEnforcement (answered by the handler adr_registry)."""
    return (_ENFORCEMENTS if table is None else table)[enforcement_id]


def _assert_adr_contract(
    spec: AdrSpec, *, table: dict[str, EnforcementRef | SemgrepSpec] | None = None
) -> None:
    """Check the shape of an ADR: an adopted one has at least one enforcement, the listed enforcements are in the table (None = the process registry), and expected-red enforcements
    are tied to a plan. From outside, check with the effects AssertAdrContract and AssertAllAdrContracts."""
    enforcements = _ENFORCEMENTS if table is None else table
    if spec.status == "accepted" and not spec.enforcement:
        raise AssertionError(f"{spec.id}: accepted ADR must have executable enforcement")
    for ref in spec.enforcement:
        if ref.id not in enforcements:
            raise AssertionError(f"{spec.id}: enforcement {ref.id!r} is not registered")
        if ref.mode == "expected-red" and not spec.plans:
            raise AssertionError(
                f"{spec.id}: expected-red enforcement {ref.id!r} must be tied to a plan"
            )


def _assert_semgrep_enforcement(
    enforcement_id: str,
    *,
    table: dict[str, EnforcementRef | SemgrepSpec] | None = None,
    semgrep: str | None = None,
) -> None:
    """Run the Semgrep enforcements in the table (None = the process registry) and check that the rule matches the samples it should match and does not match the ones it should not
    (semgrep = the executable to run; None means semgrep on PATH). From outside, check with the effect AssertSemgrep."""
    enforcements = _ENFORCEMENTS if table is None else table
    spec = enforcements[enforcement_id]
    if not isinstance(spec, SemgrepSpec):
        raise AssertionError(f"{enforcement_id}: registered enforcement is not a Semgrep rule")
    executable = shutil.which("semgrep") if semgrep is None else semgrep
    if executable is None:
        raise AssertionError("semgrep executable is required for defsemgrep enforcement")
    # When the named executable is missing, fail with the same message instead of the subprocess FileNotFoundError (finding 4 of the configuration review).
    if not Path(executable).is_file():
        raise AssertionError(
            f"semgrep executable is required for defsemgrep enforcement: {executable} is not a file"
        )
    if spec.installed_rule_id is not None:
        _assert_installed_semgrep_enforcement(executable, spec, enforcements)
        return
    _assert_inline_semgrep_enforcement(executable, spec)


def _assert_inline_semgrep_enforcement(semgrep: str, spec: SemgrepSpec) -> None:
    if spec.pattern is None:
        raise AssertionError(f"{spec.id}: inline defsemgrep requires pattern=")
    if not spec.bad:
        raise AssertionError(f"{spec.id}: inline defsemgrep requires at least one bad fixture")
    if not spec.good:
        raise AssertionError(f"{spec.id}: inline defsemgrep requires at least one good fixture")
    with tempfile.TemporaryDirectory(prefix="doeff-adr-semgrep-") as tmp:
        root = Path(tmp)
        config = root / "rule.json"
        config.write_text(json.dumps(_semgrep_config(spec)), encoding="utf-8")
        bad_dir = root / "bad"
        good_dir = root / "good"
        bad_dir.mkdir()
        good_dir.mkdir()
        bad_files = _write_inline_fixture_files(bad_dir, spec.bad, spec.languages)
        good_files = _write_inline_fixture_files(good_dir, spec.good, spec.languages)
        bad_result = _run_semgrep(semgrep, config, bad_files, project_root=root)
        good_result = _run_semgrep(semgrep, config, good_files, project_root=root)
    if not bad_result:
        raise AssertionError(f"{spec.id}: defsemgrep did not match any bad fixture")
    if good_result:
        raise AssertionError(f"{spec.id}: defsemgrep matched good fixtures: {good_result!r}")


def _assert_installed_semgrep_enforcement(
    semgrep: str, spec: SemgrepSpec, table: dict[str, EnforcementRef | SemgrepSpec]
) -> None:
    if not spec.hit_fixtures:
        raise AssertionError(f"{spec.id}: installed defsemgrep requires hit fixtures")
    if not spec.clean_fixtures:
        raise AssertionError(f"{spec.id}: installed defsemgrep requires clean fixtures")
    if spec.installed_rule_id is None:
        raise AssertionError(f"{spec.id}: installed defsemgrep requires rule_id=")
    config_path = resolved_config_path(spec)
    if not config_path.is_file():
        raise AssertionError(f"{spec.id}: semgrep config does not exist: {config_path}")
    _ensure_installed_rule_exists(config_path, spec.installed_rule_id)
    hit_rule_ids = _installed_fixture_rule_ids(semgrep, config_path, spec, table, polarity="hit")
    clean_rule_ids = _installed_fixture_rule_ids(
        semgrep, config_path, spec, table, polarity="clean"
    )
    if not _has_rule(hit_rule_ids, spec.installed_rule_id):
        raise AssertionError(
            f"{spec.id}: installed semgrep rule did not fire on hit fixtures: "
            f"{spec.installed_rule_id}"
        )
    if _has_rule(clean_rule_ids, spec.installed_rule_id):
        raise AssertionError(
            f"{spec.id}: installed semgrep rule fired on clean fixtures: {spec.installed_rule_id}"
        )


def _installed_fixture_rule_ids(
    semgrep: str,
    config_path: Path,
    spec: SemgrepSpec,
    table: dict[str, EnforcementRef | SemgrepSpec],
    *,
    polarity: str,
) -> set[str]:
    fixtures = spec.hit_fixtures if polarity == "hit" else spec.clean_fixtures
    if not spec.expand_hy:
        return set(_installed_verdict(semgrep, config_path, spec, fixtures, table))
    from doeff_adr._semgrep_hy import scan_with_hy_expansion

    with tempfile.TemporaryDirectory(prefix=f"doeff-adr-hy-fixtures-{polarity}-") as tmp:
        root = Path(tmp)
        targets = _write_semgrep_structured_fixtures(root, fixtures)
        # Project macros ((require my.macros [...])) resolve from pytest's cwd.
        findings = scan_with_hy_expansion(
            config_path, root, targets, python_path=(str(Path.cwd()),), semgrep=semgrep
        )
    return {finding.rule_id for finding in findings}


def _installed_verdict(
    semgrep: str,
    config_path: Path,
    spec: SemgrepSpec,
    fixtures: tuple[dict[str, str], ...],
    table: dict[str, EnforcementRef | SemgrepSpec],
) -> frozenset[str]:
    """Ids of the rules that matched one example group — read from the answer of a single semgrep run that checks the examples of all rules reading the same config together.

    Starting semgrep once per rule enforcement multiplies the cost of one launch (about 2.3 seconds) by "number of rules x 2"
    (agora-controllers' defadr_turn_boundary has 7 rules, so 14 launches, about 33 seconds). At the first enforcement, we
    collect all the matching and non-matching examples of the rules in the table (`table` — the table holding the enforcement being checked) that read the same config, run them once, and remember the answer. If the caller names a store
    (semgrep_verdicts.verdict_store), read from the store first and leave the answer of the run in the store — the next process whose config and examples are unchanged
    does not launch semgrep (the daily run used to pay about 8 seconds for one launch per session and config every time).
    """
    identity = semgrep_identity(semgrep)
    config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    key = fixture_set_key(identity, config_digest, fixtures)
    if key in _INSTALLED_VERDICTS:
        return _INSTALLED_VERDICTS[key]
    store = verdict_store()
    stored = None if store is None else read_verdict(store, key)
    if stored is not None:
        _INSTALLED_VERDICTS[key] = stored
        return stored
    siblings = {
        fixture_set_key(identity, config_digest, sibling_fixtures): sibling_fixtures
        for sibling in table.values()
        if isinstance(sibling, SemgrepSpec)
        and sibling.installed_rule_id is not None
        and not sibling.expand_hy
        and (sibling.config, sibling.config_base) == (spec.config, spec.config_base)
        for sibling_fixtures in (sibling.hit_fixtures, sibling.clean_fixtures)
        if sibling_fixtures
    }
    pending = {
        pending_key: pending_fixtures
        for pending_key, pending_fixtures in (siblings | {key: fixtures}).items()
        if pending_key not in _INSTALLED_VERDICTS
    }
    verdicts = _run_installed_semgrep_batch(semgrep, config_path, pending)
    _INSTALLED_VERDICTS.update(verdicts)
    if store is not None:
        write_verdicts(store, verdicts)
    return verdicts[key]


# Prefix that launches a child process with the environment variables that decide the git repo location removed — so that even when run inside the hook of an outer repo,
# the git repo of each group becomes the root (the variables are removed without reading the environment, so they are passed via env -u).
_WITHOUT_GIT_ENV: tuple[str, ...] = (
    "env",
    "-u",
    "GIT_DIR",
    "-u",
    "GIT_WORK_TREE",
    "-u",
    "GIT_INDEX_FILE",
    "-u",
    "GIT_COMMON_DIR",
    "-u",
    "GIT_OBJECT_DIRECTORY",
)


def _run_installed_semgrep_batch(
    semgrep: str,
    config_path: Path,
    fixture_sets: dict[str, tuple[dict[str, str], ...]],
) -> dict[str, frozenset[str]]:
    """Run one semgrep for all example groups and output the ids of the rules that matched, per group.

    Write the examples in a per-group dir and make each a git repo — semgrep takes the nearest git root of each target dir as the
    project root, so paths.include pinned to the root (the ``/controllers/**`` form) works per group, and example
    paths may overlap between groups. ``--project-root`` can be passed only once, so it is not passed. Even if the tree is
    placed inside an outer git repo, or a commit hook passes GIT_DIR, run with the git environment variables removed so the root does not fall to that repo
    (if it did, an include pinned to the root would silently die — measured 2026-10-03: all matching examples of 7 rules missed inside an outer repo).
    """
    cases = {f"case-{index}": key for index, key in enumerate(fixture_sets)}
    with tempfile.TemporaryDirectory(prefix="doeff-adr-installed-semgrep-") as tmp:
        root = Path(tmp)
        for case, key in cases.items():
            (root / case).mkdir()
            # Put an empty .semgrepignore at the group root: without it, when walking a dir semgrep applies its default excludes (tests/, test/, build/ and others),
            # silently skipping the matching examples under tests/ and turning "the rule does not match" red (#1201 — when 532225d21 changed the examples from being named
            # individually to passing the group dir, 4 of enforce_002 went red). Going back to naming them makes an include pinned to the root ineffective at the group root.
            # Write it before the examples — if an example has the same name, _write_semgrep_structured_fixtures stops with FileExistsError.
            (root / case / ".semgrepignore").write_text("", encoding="utf-8")
            _write_semgrep_structured_fixtures(root / case, fixture_sets[key])
            # If GIT_DIR remains, git init re-initializes the repo at GIT_DIR instead of the group dir.
            subprocess.run(
                [*_WITHOUT_GIT_ENV, "git", "init", "-q", str(root / case)],
                check=True,
                capture_output=True,
            )
        results = _run_semgrep(
            semgrep,
            config_path,
            [Path(case) for case in cases],
            cwd=root,
            project_root=None,
            launcher=_WITHOUT_GIT_ENV,
        )
    return {
        key: frozenset(result.check_id for result in results if Path(result.path).parts[0] == case)
        for case, key in cases.items()
    }


def _semgrep_config(spec: SemgrepSpec) -> dict[str, Any]:
    if spec.pattern is None:
        raise AssertionError(f"{spec.id}: inline semgrep config requires pattern")
    return {
        "rules": [
            {
                "id": spec.id,
                "languages": list(spec.languages),
                "message": spec.message,
                "severity": spec.severity,
                "pattern": spec.pattern,
            }
        ]
    }


def _write_inline_fixture_files(
    root: Path,
    fixtures: tuple[FixtureSpec, ...],
    languages: tuple[str, ...],
) -> list[Path]:
    paths: list[Path] = []
    extension = _inline_fixture_extension(languages)
    for idx, content in enumerate(fixtures):
        if isinstance(content, dict):
            path = root / content["relative-path"]
            source = content["source"]
        else:
            path = root / f"fixture_{idx}{extension}"
            source = content
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"semgrep fixture already exists: {path}")
        path.write_text(source, encoding="utf-8")
        paths.append(path)
    return paths


def _inline_fixture_extension(languages: tuple[str, ...]) -> str:
    language = languages[0] if languages else "generic"
    return {
        "generic": ".txt",
        "python": ".py",
        "javascript": ".js",
        "typescript": ".ts",
        "json": ".json",
        "yaml": ".yaml",
    }.get(language, ".txt")


def _write_semgrep_structured_fixtures(
    root: Path, fixtures: tuple[dict[str, str], ...]
) -> list[Path]:
    paths: list[Path] = []
    for fixture in fixtures:
        relative_path = Path(fixture["relative-path"])
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"semgrep fixture already exists: {path}")
        path.write_text(fixture["source"], encoding="utf-8")
        paths.append(relative_path)
    return paths


def _semgrep_error_report(stdout: str) -> str:
    """Turn the stdout (the --json answer) of a failed semgrep into a readable chunk of reasons.

    List the kind, rule, file, and message of each item of errors, one per line (the first 10 when there are many). When it is not JSON or has no errors,
    output the head of stdout as is (nothing is discarded).
    """
    try:
        payload = json.loads(stdout)
    except ValueError:
        return f"stdout (not JSON):\n{stdout[:2000]}"
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not isinstance(errors, list) or not errors:
        return f"stdout:\n{stdout[:2000]}"
    lines = [
        " ".join(
            f"{key}={str(error.get(key))[:600]}"
            for key in ("type", "level", "rule_id", "path", "message")
            if isinstance(error, dict) and error.get(key) is not None
        )
        or str(error)[:600]
        for error in errors[:10]
    ]
    return f"semgrep errors ({len(errors)}):\n" + "\n".join(lines)


def _run_semgrep(
    semgrep: str,
    config: Path,
    paths: list[Path],
    *,
    cwd: Path | None = None,
    project_root: Path | None,
    launcher: tuple[str, ...] = (),
) -> list[SemgrepResult]:
    proc = subprocess.run(
        [
            *launcher,
            semgrep,
            # The meaning of the check is decided by the tree alone — if we let the scanner use the network (sending metrics,
            # querying for a new version), machine circumstances such as reachability and response time
            # leak into the check run (hangs on offline machines, timeouts on saturated networks).
            "--metrics=off",
            "--disable-version-check",
            # State the project root explicitly. If we leave it to inference from git metadata,
            # in a check tree without .git the root falls to the scanned dir itself, and only paths.include that refers to
            # segments above the target dies silently
            # (node-h measurement 2026-08-17: rules that fire and rules that do not split by the shape of include).
            # None is passed only by the caller (_run_installed_semgrep_batch) that "made a git repo per target dir and fixed the root
            # there" — because the root cannot be decided as a single one.
            *(() if project_root is None else ("--project-root", str(project_root))),
            "--quiet",
            "--json",
            "--config",
            str(config),
            *[str(path) for path in paths],
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        # With --quiet --json, the reasons for a rule-load failure, a target parse failure, and a timeout do not appear on stderr but
        # only in the errors of the stdout JSON — naming only stderr leaves the reason empty
        # (in the agora daily run only "semgrep failed with exit 2:" remained,
        # and we could not name whether it was the rule load or the semgrep version).
        raise AssertionError(
            f"semgrep failed with exit {proc.returncode}: {proc.stderr}\n"
            f"{_semgrep_error_report(proc.stdout)}"
        )
    # exit 1 is returned both for "findings present" and for "crash at startup" — only the existence of JSON is
    # evidence that the scan really ran. Silently reading a scan that did not run as "no findings" turns
    # a scanner fault into a false red/green of "the rule does not fire" (node-h measurement
    # 2026-08-17: an interpreter mismatch made semgrep crash at import time, and all installed
    # rules were misdiagnosed as "does not fire on the hit fixture").
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(
            f"semgrep produced no JSON verdict (exit {proc.returncode}) — "
            f"the scan did not run; stderr:\n{proc.stderr}"
        ) from exc
    if not isinstance(payload, dict) or "results" not in payload:
        raise AssertionError(
            f"semgrep JSON output has no results field (exit {proc.returncode}) — "
            f"stdout:\n{proc.stdout[:2000]}\nstderr:\n{proc.stderr}"
        )
    return [
        SemgrepResult(
            check_id=str(result["check_id"]),
            path=str(result["path"]),
            line=int(result["start"]["line"]),
            message=str(result["extra"]["message"]),
        )
        for result in payload["results"]
    ]


def resolved_config_path(spec: SemgrepSpec) -> Path:
    """The Semgrep config file an installed ``defsemgrep`` reads.

    ``config_base`` set (``defsemgrep … :config "x.yaml"``): a relative config is
    taken from the declaring file's directory, so a rule file can live next to
    the ADR that owns it.  Otherwise the legacy cwd-ancestor walk applies.
    """
    path = Path(spec.config)
    if path.is_absolute():
        return path
    if spec.config_base is not None:
        return (Path(spec.config_base) / path).resolve()
    return _resolve_config_path(spec.config)


def _resolve_config_path(config: str) -> Path:
    path = Path(config)
    if path.is_absolute():
        return path
    return _find_tree_root(Path.cwd(), path) / path


def _find_tree_root(start: Path, config: Path) -> Path:
    """The nearest ancestor dir that actually holds the config = the root of the check tree.

    Do not rely on git metadata (.git) — the meaning of the check is decided by the tree alone.
    A remote check tree carries only the list from git ls-files and has no .git (measured
    2026-08-17 node-h: the .git anchor wiped out every installed-rule defsemgrep).
    """
    root = start.resolve()
    while True:
        if (root / config).is_file():
            return root
        parent = root.parent
        if parent == root:
            raise RuntimeError(f"could not find semgrep config {config} in any ancestor of {start}")
        root = parent


def _ensure_installed_rule_exists(config_path: Path, rule_id: str) -> None:
    with config_path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    rules = payload.get("rules") or []
    if not any(rule.get("id") == rule_id for rule in rules):
        raise AssertionError(f"semgrep rule not found in {config_path}: {rule_id}")


def _has_rule(rule_ids: set[str], expected_rule_id: str) -> bool:
    suffix = f".{expected_rule_id}"
    return any(rule_id == expected_rule_id or rule_id.endswith(suffix) for rule_id in rule_ids)
