"""Runtime registry for executable ADR contracts.

Hy macros expand into calls in this module.  The registry is intentionally
small: it records ADR specs and executable enforcement specs, then exposes
assertions that pytest-generated functions can call.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

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
    languages: tuple[str, ...] = ("generic",)
    message: str = "ADR Semgrep enforcement failed"
    severity: str = "ERROR"
    bad: tuple[FixtureSpec, ...] = ()
    good: tuple[FixtureSpec, ...] = ()
    hit_fixtures: tuple[dict[str, str], ...] = ()
    clean_fixtures: tuple[dict[str, str], ...] = ()
    mode: EnforcementMode = "green"


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


def clear_registry() -> None:
    _ADRS.clear()
    _ENFORCEMENTS.clear()


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


def _ensure_new_enforcement_id(enforcement_id: str) -> None:
    if enforcement_id in _ENFORCEMENTS:
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


def register_deftest_enforcement(
    enforcement_id: str,
    *,
    mode: EnforcementMode = "green",
) -> EnforcementRef:
    _ensure_new_enforcement_id(enforcement_id)
    normalized_mode = _normalize_enforcement_mode(mode)
    ref = EnforcementRef(id=enforcement_id, kind="deftest", mode=normalized_mode)
    _ENFORCEMENTS[enforcement_id] = ref
    return ref


def register_semgrep_enforcement(
    enforcement_id: str,
    *,
    pattern: str | None = None,
    rule_id: str | None = None,
    config: str = ".semgrep.yaml",
    languages: list[str] | tuple[str, ...] | None = None,
    message: str = "ADR Semgrep enforcement failed",
    severity: str = "ERROR",
    bad: list[FixtureSpec] | tuple[FixtureSpec, ...] | None = None,
    good: list[FixtureSpec] | tuple[FixtureSpec, ...] | None = None,
    hit_fixtures: list[dict[str, str]] | tuple[dict[str, str], ...] | None = None,
    clean_fixtures: list[dict[str, str]] | tuple[dict[str, str], ...] | None = None,
    mode: EnforcementMode = "green",
) -> SemgrepSpec:
    _ensure_new_enforcement_id(enforcement_id)
    if pattern is None and rule_id is None:
        raise ValueError("defsemgrep requires either pattern= or rule_id=")
    normalized_mode = _normalize_enforcement_mode(mode)
    spec = SemgrepSpec(
        id=enforcement_id,
        pattern=pattern,
        installed_rule_id=rule_id,
        config=config,
        languages=tuple(languages or ("generic",)),
        message=message,
        severity=severity,
        bad=tuple(bad or ()),
        good=tuple(good or ()),
        hit_fixtures=tuple(hit_fixtures or ()),
        clean_fixtures=tuple(clean_fixtures or ()),
        mode=normalized_mode,
    )
    _ENFORCEMENTS[enforcement_id] = spec
    return spec


def register_adr(
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
    **metadata: Any,
) -> AdrSpec:
    if adr_id in _ADRS:
        raise ValueError(f"duplicate ADR id: {adr_id}")
    normalized_status = _normalize_adr_status(status)
    refs = tuple(_coerce_enforcement_ref(item) for item in _tuple_of_any(enforcement))
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
        metadata=metadata,
    )
    _ADRS[adr_id] = spec
    return spec


def _coerce_enforcement_ref(item: Any) -> EnforcementRef:
    if isinstance(item, EnforcementRef):
        return item
    if isinstance(item, SemgrepSpec):
        return EnforcementRef(id=item.id, kind="defsemgrep", mode=item.mode)
    if isinstance(item, str):
        known = _ENFORCEMENTS.get(item)
        if isinstance(known, SemgrepSpec):
            return EnforcementRef(id=item, kind="defsemgrep", mode=known.mode)
        if isinstance(known, EnforcementRef):
            return known
        return EnforcementRef(id=item, kind="unknown")
    raise TypeError(f"unsupported ADR enforcement reference: {item!r}")


def adr_ids() -> list[str]:
    return sorted(_ADRS)


def enforcement_ids() -> list[str]:
    return sorted(_ENFORCEMENTS)


def get_adr(adr_id: str) -> AdrSpec:
    return _ADRS[adr_id]


def get_enforcement(enforcement_id: str) -> EnforcementRef | SemgrepSpec:
    return _ENFORCEMENTS[enforcement_id]


def assert_adr_contract(adr_id: str) -> None:
    spec = get_adr(adr_id)
    if spec.status == "accepted" and not spec.enforcement:
        raise AssertionError(f"{adr_id}: accepted ADR must have executable enforcement")
    for ref in spec.enforcement:
        if ref.id not in _ENFORCEMENTS:
            raise AssertionError(f"{adr_id}: enforcement {ref.id!r} is not registered")
        if ref.mode == "expected-red" and not spec.plans:
            raise AssertionError(
                f"{adr_id}: expected-red enforcement {ref.id!r} must be tied to a plan"
            )


def assert_all_adr_contracts() -> None:
    for adr_id in adr_ids():
        assert_adr_contract(adr_id)


def assert_semgrep_enforcement(enforcement_id: str) -> None:
    spec = get_enforcement(enforcement_id)
    if not isinstance(spec, SemgrepSpec):
        raise AssertionError(f"{enforcement_id}: registered enforcement is not a Semgrep rule")
    semgrep = shutil.which("semgrep")
    if semgrep is None:
        raise AssertionError("semgrep executable is required for defsemgrep enforcement")
    if spec.installed_rule_id is not None:
        _assert_installed_semgrep_enforcement(semgrep, spec)
        return
    _assert_inline_semgrep_enforcement(semgrep, spec)


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
        bad_result = _run_semgrep(semgrep, config, bad_files)
        good_result = _run_semgrep(semgrep, config, good_files)
    if not bad_result:
        raise AssertionError(f"{spec.id}: defsemgrep did not match any bad fixture")
    if good_result:
        raise AssertionError(f"{spec.id}: defsemgrep matched good fixtures: {good_result!r}")


def _assert_installed_semgrep_enforcement(semgrep: str, spec: SemgrepSpec) -> None:
    if not spec.hit_fixtures:
        raise AssertionError(f"{spec.id}: installed defsemgrep requires hit fixtures")
    if not spec.clean_fixtures:
        raise AssertionError(f"{spec.id}: installed defsemgrep requires clean fixtures")
    if spec.installed_rule_id is None:
        raise AssertionError(f"{spec.id}: installed defsemgrep requires rule_id=")
    config_path = _resolve_config_path(spec.config)
    _ensure_installed_rule_exists(config_path, spec.installed_rule_id)
    with tempfile.TemporaryDirectory(prefix="doeff-adr-installed-semgrep-") as tmp:
        root = Path(tmp)
        hit_targets = _write_semgrep_structured_fixtures(root, spec.hit_fixtures)
        clean_targets = _write_semgrep_structured_fixtures(root, spec.clean_fixtures)
        hit_results = _run_semgrep(semgrep, config_path, hit_targets, cwd=root)
        clean_results = _run_semgrep(semgrep, config_path, clean_targets, cwd=root)
    hit_rule_ids = _result_rule_ids(hit_results)
    clean_rule_ids = _result_rule_ids(clean_results)
    if not _has_rule(hit_rule_ids, spec.installed_rule_id):
        raise AssertionError(
            f"{spec.id}: installed semgrep rule did not fire on hit fixtures: "
            f"{spec.installed_rule_id}"
        )
    if _has_rule(clean_rule_ids, spec.installed_rule_id):
        raise AssertionError(
            f"{spec.id}: installed semgrep rule fired on clean fixtures: {spec.installed_rule_id}"
        )


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


def _run_semgrep(
    semgrep: str,
    config: Path,
    paths: list[Path],
    *,
    cwd: Path | None = None,
) -> list[dict[str, Any]]:
    proc = subprocess.run(
        [
            semgrep,
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
        raise AssertionError(f"semgrep failed with exit {proc.returncode}: {proc.stderr}")
    payload = json.loads(proc.stdout or "{}")
    return list(payload.get("results", []))


def _resolve_config_path(config: str) -> Path:
    path = Path(config)
    if path.is_absolute():
        return path
    return _find_repo_root(Path.cwd()) / path


def _find_repo_root(start: Path) -> Path:
    root = start.resolve()
    while True:
        if (root / ".git").exists():
            return root
        parent = root.parent
        if parent == root:
            raise RuntimeError(f"could not find repository root from {start}")
        root = parent


def _ensure_installed_rule_exists(config_path: Path, rule_id: str) -> None:
    with config_path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    rules = payload.get("rules") or []
    if not any(rule.get("id") == rule_id for rule in rules):
        raise AssertionError(f"semgrep rule not found in {config_path}: {rule_id}")


def _result_rule_ids(results: list[dict[str, Any]]) -> set[str]:
    return {str(result["check_id"]) for result in results}


def _has_rule(rule_ids: set[str], expected_rule_id: str) -> bool:
    suffix = f".{expected_rule_id}"
    return any(rule_id == expected_rule_id or rule_id.endswith(suffix) for rule_id in rule_ids)
