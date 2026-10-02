"""Runtime registry for executable ADR contracts.

Hy macros expand into calls in this module.  The registry is intentionally
small: it records ADR specs and executable enforcement specs, then exposes
assertions that pytest-generated functions can call.
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
    """semgrep の --json の結果 1 件のうち、検が読む欄 — JSON の境界で型のある値にして、使い手が dict を引かないため。"""

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
# installed の defsemgrep の例の組 1 つに当たった規則の id(鍵 = semgrep・設定の path と中身・例の中身の digest)。
# 同じ設定を読む規則の例を 1 回の semgrep で確かめ、同じ process の残りの検はここから読む(agora-redesign #2976 I-3)。
_INSTALLED_VERDICTS: dict[str, frozenset[str]] = {}


def clear_registry() -> None:
    _ADRS.clear()
    _ENFORCEMENTS.clear()


@contextmanager
def isolated_registry() -> Generator[None]:
    """空の登録簿で中を走らせ、出る時に入る前の登録簿へ戻す。

    収集で import された ADR は収集の時点で登録簿に載る — 空にしたまま返す test があると、後で走るその ADR の条文の
    test が KeyError で落ちる(agora-redesign #1211)。test が登録簿を空にしたい時はこれを使う。
    """
    saved_adrs = dict(_ADRS)
    saved_enforcements = dict(_ENFORCEMENTS)
    clear_registry()
    try:
        yield
    finally:
        clear_registry()
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
    _ensure_new_enforcement_id(enforcement_id)
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
        bad_result = _run_semgrep(semgrep, config, bad_files, project_root=root)
        good_result = _run_semgrep(semgrep, config, good_files, project_root=root)
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
    config_path = resolved_config_path(spec)
    if not config_path.is_file():
        raise AssertionError(f"{spec.id}: semgrep config does not exist: {config_path}")
    _ensure_installed_rule_exists(config_path, spec.installed_rule_id)
    hit_rule_ids = _installed_fixture_rule_ids(semgrep, config_path, spec, polarity="hit")
    clean_rule_ids = _installed_fixture_rule_ids(semgrep, config_path, spec, polarity="clean")
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
    semgrep: str, config_path: Path, spec: SemgrepSpec, *, polarity: str
) -> set[str]:
    fixtures = spec.hit_fixtures if polarity == "hit" else spec.clean_fixtures
    if not spec.expand_hy:
        return set(_installed_verdict(semgrep, config_path, spec, fixtures))
    from doeff_adr.semgrep_hy import scan_with_hy_expansion

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
) -> frozenset[str]:
    """例の組 1 つに当たった規則の id — 同じ設定を読む規則の例をまとめて 1 回の semgrep で確かめた答えから読む。

    規則の検 1 本ごとに semgrep を起こすと、1 回の起動の費用(約 2.3 秒)が「規則の数 × 2」だけ重なる
    (agora-controllers の defadr_turn_boundary は 7 規則で 14 回・約 33 秒 — agora-redesign #2976 I-3)。最初の検の時に、
    同じ設定を読む登録済みの規則の当たる例と当たらない例を全部集めて 1 回で回し、答えを覚える。
    """
    config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    key = _fixture_set_key(semgrep, config_path, config_digest, fixtures)
    if key not in _INSTALLED_VERDICTS:
        siblings = {
            _fixture_set_key(
                semgrep, config_path, config_digest, sibling_fixtures
            ): sibling_fixtures
            for sibling in _ENFORCEMENTS.values()
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
        _INSTALLED_VERDICTS.update(_run_installed_semgrep_batch(semgrep, config_path, pending))
    return _INSTALLED_VERDICTS[key]


def _fixture_set_key(
    semgrep: str, config_path: Path, config_digest: str, fixtures: tuple[dict[str, str], ...]
) -> str:
    """覚えた答えを引く鍵 — semgrep・設定・例のどれかが変われば別の鍵になり、古い答えを使い回さないため。"""
    material = json.dumps(
        [semgrep, str(config_path), config_digest, list(fixtures)], sort_keys=True
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# 子 process を、git の repo の場所を決める環境変数を外して起こす前置き — 外の repo の hook の中で回っても、
# 組ごとの git の repo が root になるようにするため(環境を読まずに外すので env -u で渡す)。
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
    """例の組ごとに、当たった規則の id を 1 回の semgrep で出す。

    組ごとの dir に例を書き、組ごとに git の repo にする — semgrep は対象の dir ごとに一番近い git の root を
    project root に取るので、root に固定した paths.include(``/controllers/**`` の形)が組ごとに効き、組の間で
    例の path が重なってもよい。``--project-root`` は 1 つしか渡せないので渡さない。外の git の repo の中に木を
    置いても、commit の hook が GIT_DIR を渡していても root がそちらへ落ちないよう、git の環境変数を外して回す
    (落ちると root に固定した include が黙って死ぬ — 実測 2026-10-03: 外の repo の中で 7 規則の当たる例が全部外れた)。
    """
    cases = {f"case-{index}": key for index, key in enumerate(fixture_sets)}
    with tempfile.TemporaryDirectory(prefix="doeff-adr-installed-semgrep-") as tmp:
        root = Path(tmp)
        for case, key in cases.items():
            _write_semgrep_structured_fixtures(root / case, fixture_sets[key])
            # GIT_DIR が残ると git init は組の dir ではなく GIT_DIR の repo を初期化し直す。
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
    """semgrep が失敗した時の stdout(--json の答え)から、理由を読める塊にするため。

    errors の各項の種類・規則・file・文言を 1 行ずつ並べる(多い時は頭の 10 件)。JSON でない・errors が無い時は
    stdout の頭をそのまま出す(何も捨てない)。
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
            # 検査の意味は tree だけで決まる — scanner に network(metrics 送信・
            # 新版照会)を許すと、到達性や応答時間という機体の事情が検査の実行に
            # 混入する(オフライン機で hang、飽和網で timeout)。
            "--metrics=off",
            "--disable-version-check",
            # project root は明示宣言する。git metadata からの推論に任せると、
            # .git の無い検査 tree で root が走査対象 dir 自身に落ち、対象より
            # 上のセグメントを参照する paths.include だけが無音で死ぬ(zeus 実測
            # 2026-08-17: 発火する rule としない rule が include の形で割れた)。
            # None は「対象の dir ごとに git の repo を作って root を固めた」呼び手
            # (_run_installed_semgrep_batch)だけが渡す — root を 1 つに決められないため。
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
        # --quiet --json では、規則の読みの失敗・対象の parse の失敗・時間切れの理由は stderr に出ず、
        # stdout の JSON の errors にだけ載る — stderr だけを名乗ると理由が空のまま落ちる
        # (agora-redesign #2865: herdr-hud の日次で「semgrep failed with exit 2:」だけが残り、
        # 規則の読みか semgrep の版かを名指せなかった)。
        raise AssertionError(
            f"semgrep failed with exit {proc.returncode}: {proc.stderr}\n"
            f"{_semgrep_error_report(proc.stdout)}"
        )
    # exit 1 は「findings あり」と「起動時 crash」の両方が返す — JSON の実在だけが
    # scan が本当に走った証拠。走らなかった scan を「発火なし」と黙読すると、
    # scanner の故障が『rule が発火しない』という偽の赤/緑に化ける(zeus 実測
    # 2026-08-17: interpreter 不整合で semgrep が import 時に crash し、installed
    # rule 全滅が『hit fixture に発火しない』と誤診された)。
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
    """config を実際に持つ最近接の祖先 dir = 検査 tree の root。

    git metadata(.git)を根拠にしない — 検査の意味は tree だけで決まる。
    遠隔検査 tree は git ls-files の名簿だけを運び .git を持たない(実測
    2026-08-17 zeus: .git 錨のせいで installed-rule defsemgrep が全滅した)。
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
