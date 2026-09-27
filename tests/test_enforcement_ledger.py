"""ADR-DOE-ENFORCE-001 R5 / R9: enforcement 台帳の anti-drop ratchet。

orch の SpecInventorySpec の pytest 版。enforcement 資産(defadr の file・.semgrep.yaml の規則・
ADR の law / deftest / defsemgrep)の名の一覧が台帳 docs/adr/enforcement-ledger.json と
厳密一致しなければ fail する(台帳は生成物 — `make enforcement-ledger` が作る)。

- 台帳に在って木に無い: enforcement の喪失 — 意図した削除なら台帳を生成し直し、差分に残る
  名前で削除を明示する。
- 木に在って台帳に無い: 追加の記帳漏れ — 台帳を生成し直す。

勘定の定義点は scripts/check_enforcement_ledger.py の 1 点(R7 の著述時 git pre-commit hook と
同じ家)— ここはそれを既定 pytest 収集に載せる面。加えて、その家の ADR の読み(stdlib の
小さな読み手)が Hy の reader と同じ答えを返すことを、実物の ADR 全部で突き合わせる(R9)。
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import hy
import yaml
from hy.models import Expression, Sequence, String, Symbol

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "adr" / "enforcement-ledger.json"
CHECKER = ROOT / "scripts" / "check_enforcement_ledger.py"


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_enforcement_ledger", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_enforcement_inventory_matches_ledger() -> None:
    checker = _load_checker()
    ledger = checker.worktree_ledger(ROOT)
    actual = checker.worktree_inventory(ROOT)
    drift = checker.difference(ledger, actual)
    assert not drift, (
        f"enforcement 台帳と木が食い違う — ADR-DOE-ENFORCE-001 R5 / R9。\n"
        f"{checker.describe(drift)}\n"
        f"台帳は生成物 — `make enforcement-ledger` で作り直し、消えた項目が意図した削除かを確かめる。"
        f" 台帳: {LEDGER}"
    )


def _hy_items(file_name: str, text: str) -> dict[str, list[str]]:
    """Hy の reader で読んだ形から、勘定の家と同じ規則(持ち主 = 囲む defadr)で項目を集める。"""
    keys = {"law": "adr_laws", "deftest": "adr_deftest_enforcements",
            "defsemgrep": "adr_defsemgrep_enforcements"}
    items: dict[str, list[str]] = {key: [] for key in keys.values()}

    def name_of(form: Expression) -> str:
        if len(form) < 2 or not isinstance(form[1], (Symbol, String)):
            return "<名なし>"
        return str(form[1])

    def visit(form: object, owner: str) -> None:
        if isinstance(form, Expression) and len(form) and isinstance(form[0], Symbol):
            head = str(form[0])
            if head == "defadr":
                owner = name_of(form)
            elif head in keys:
                items[keys[head]].append(f"{owner} {name_of(form)}")
        if isinstance(form, Sequence):
            for child in form:
                visit(child, owner)

    for form in hy.read_many(text, filename=file_name):
        visit(form, file_name)
    return {key: sorted(names) for key, names in items.items()}


def test_counting_reader_agrees_with_hy_reader_on_every_adr() -> None:
    # 勘定の家は hook のために stdlib 単独で ADR を読む。その読みが Hy の reader からずれた日に
    # 台帳は黙って嘘になるので、実物の ADR 全部で Hy の答えと突き合わせる(R9)。
    checker = _load_checker()
    paths = sorted(ROOT.glob(checker.ADR_GLOB))
    assert paths, "ADR が 1 冊も見つからない"
    for path in paths:
        text = path.read_text(encoding="utf-8")
        ours = {key: sorted(names) for key, names in checker.adr_items(path.name, text).items()}
        assert ours == _hy_items(path.name, text), f"{path.name}: 勘定の家の読みが Hy の reader と違う"


def test_semgrep_rule_ids_agree_with_yaml_parser() -> None:
    # 勘定の家は stdlib 単独なので .semgrep.yaml の規則 id を行の形で拾う。その拾いが YAML の構文で読んだ
    # rules の id の並びからずれた日(字下げの変更・註や文字列の中の `- id:`)に台帳は黙って嘘になるので、
    # YAML の parser の答えと突き合わせる(R9)。
    checker = _load_checker()
    text = (ROOT / checker.SEMGREP_PATH).read_text(encoding="utf-8")
    parsed = [str(rule["id"]) for rule in yaml.safe_load(text)["rules"]]
    assert sorted(checker._SEMGREP_RULE.findall(text)) == sorted(parsed), (
        "勘定の家の .semgrep.yaml の規則 id の拾いが YAML の parser と違う"
    )
