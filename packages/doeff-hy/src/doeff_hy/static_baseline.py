"""doeff-hy-check の基点との比べ(`--baseline <json>`・`--write-baseline <json>`・agora-redesign #2153)。

考えは doeff-linter の `--baseline-report`(packages/doeff-linter/src/baseline.rs)と同じ: 保存した既知の一覧に照らして
「前から在る赤」と「新しい赤」を分け、新しい赤だけを止める。違いは 2 つ。

- 識別子 = `(path, 規則, 文言)` で、行番号を含めない。上に行を足しただけで同じ赤が新しい赤にならない。
- 同じ識別子の赤は**個数で**比べる(多重集合)。型の赤は同じ文言で 1 file に何度も出るので、集合で比べると
  2 つ目の同じ赤を見逃す。基点より多い分だけが新しい赤。
- file の移動・改名: 規則と文言が同じで path だけ違い、基点のその識別子が今は減っている時、1 対 1 で同じ赤とみなす
  (linter の new_criticals と同じ)。

基点の file の形(JSON・版 1):
    {"version": 1, "errors": [{"path": ..., "rule": ..., "message": ..., "line": ...}, ...]}
`line` は読み手の参考で、比べには使わない。
"""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

BASELINE_VERSION = 1


class Reported(Protocol):
    """基点と比べる診断の形(static_check.Diagnostic が満たす)。"""

    @property
    def path(self) -> str: ...
    @property
    def line(self) -> int: ...
    @property
    def severity(self) -> str: ...
    @property
    def rule(self) -> str: ...
    @property
    def message(self) -> str: ...


_D = TypeVar("_D", bound=Reported)


@dataclass(frozen=True)
class Identity:
    """行番号を含めない赤の識別子。"""

    path: str
    rule: str
    message: str

    @property
    def without_path(self) -> tuple[str, str]:
        return (self.rule, self.message)


@dataclass(frozen=True)
class Baseline:
    """基点の赤の識別子の多重集合。"""

    counts: Counter[Identity]


@dataclass(frozen=True)
class Split(Generic[_D]):
    """この実行の赤を、基点に在る物と新しい物に分けた結果(どちらも元の順)。"""

    known: tuple[_D, ...]
    new: tuple[_D, ...]


class BaselineUnreadable(ValueError):
    """基点の file が読めない・形が違う(呼び手は終了コード 2)。"""


def identity(diagnostic: Reported) -> Identity:
    """基点と照らす鍵を作る(行番号を落とし、行のずれで同じ赤を新しい赤にしないため)。"""
    return Identity(diagnostic.path, diagnostic.rule, diagnostic.message)


def errors_of(diagnostics: list[_D]) -> list[_D]:
    """止める対象 = error だけを取り出す(warning は基点の比べに入れない)。"""
    return [d for d in diagnostics if d.severity == "error"]


def baseline_json(diagnostics: list[Reported]) -> str:
    """この実行の赤を基点の file の形にする(並びは path・行の順で固定)。"""
    rows = sorted(
        (
            {"path": d.path, "rule": d.rule, "message": d.message, "line": d.line}
            for d in errors_of(diagnostics)
        ),
        key=lambda row: (str(row["path"]), int(row["line"]), str(row["rule"]), str(row["message"])),
    )
    return json.dumps({"version": BASELINE_VERSION, "errors": rows}, ensure_ascii=False, indent=1) + "\n"


def _identity_of_row(row: object) -> Identity:
    """基点の file の 1 行を識別子へ読む(JSON の境界 — 欄が欠けた基点を黙って空と読まないため)。"""
    match row:
        case {"path": str(path), "rule": str(rule), "message": str(message)}:
            return Identity(path, rule, message)
        case _:
            raise BaselineUnreadable(f"基点の赤に欄 path・rule・message が揃っていない: {row!r}")


def read_baseline(path: Path) -> Baseline:
    """`--baseline` の file を読む。読めない・版が違う時は BaselineUnreadable(新しい赤を黙って通さないため)。"""
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BaselineUnreadable(f"基点 {path} を読めない: {error}") from error
    match loaded:
        case {"version": 1, "errors": list(rows)}:
            return Baseline(Counter(_identity_of_row(row) for row in rows))
        case {"version": version}:
            raise BaselineUnreadable(f"基点 {path} の版 {version!r} は読めない(読める版 = {BASELINE_VERSION})")
        case _:
            raise BaselineUnreadable(f"基点 {path} の形が違う(version と errors の列が要る)")


def split(baseline: Baseline, diagnostics: list[_D]) -> Split[_D]:
    """この実行の赤を基点に照らして分ける。同じ識別子は基点の個数までが既存、超えた分が新しい。"""
    errors = errors_of(diagnostics)
    current = Counter(identity(d) for d in errors)
    # file の移動・改名: 基点で減った識別子を、path を除いた鍵ごとに数えて使える枠にする。
    vanished = Counter(
        ident.without_path
        for ident, count in (baseline.counts - current).items()
        for _ in range(count)
    )
    budget = baseline.counts.copy()
    ordered = sorted(errors, key=lambda d: (d.path, d.line))
    verdicts: dict[int, bool] = {}
    for diagnostic in ordered:
        key = identity(diagnostic)
        if budget[key] > 0:
            budget[key] -= 1
            verdicts[id(diagnostic)] = True
        elif vanished[key.without_path] > 0:
            vanished[key.without_path] -= 1
            verdicts[id(diagnostic)] = True
        else:
            verdicts[id(diagnostic)] = False
    return Split(
        known=tuple(d for d in errors if verdicts[id(d)]),
        new=tuple(d for d in errors if not verdicts[id(d)]),
    )
