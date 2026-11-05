"""ADR-DOE-ENFORCE-001 R5 / R9: anti-drop ratchet for the enforcement ledger.

The pytest version of orch's SpecInventorySpec. The list of enforcement asset names (defadr files, .semgrep.yaml rules,
ADR law / deftest / defsemgrep) fails unless it exactly matches the ledger docs/adr/enforcement-ledger.json
(the ledger is generated — `make enforcement-ledger` creates it).

- In the ledger but not in the tree: loss of enforcement — if the deletion is intended, regenerate the ledger and state the deletion explicitly
  by the name that remains in the diff.
- In the tree but not in the ledger: an addition was not recorded — regenerate the ledger.

The counting is defined at a single point, scripts/check_enforcement_ledger.py (the same home as the R7 authoring-time git pre-commit hook)
— this is the surface that puts it into default pytest collection. In addition, the ADR reading of that home (a small stdlib
reader) is checked against all real ADRs to return the same answer as the Hy reader (R9).
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
        f"The enforcement ledger and the tree disagree — ADR-DOE-ENFORCE-001 R5 / R9.\n"
        f"{checker.describe(drift)}\n"
        f"The ledger is generated — rebuild it with `make enforcement-ledger` and confirm that removed items are intended deletions."
        f" Ledger: {LEDGER}"
    )


def _hy_items(file_name: str, text: str) -> dict[str, list[str]]:
    """Collect items from the form read by the Hy reader, using the same rule as the counting home (owner = enclosing defadr)."""
    keys = {"law": "adr_laws", "deftest": "adr_deftest_enforcements",
            "defsemgrep": "adr_defsemgrep_enforcements"}
    items: dict[str, list[str]] = {key: [] for key in keys.values()}

    def name_of(form: Expression) -> str:
        if len(form) < 2 or not isinstance(form[1], (Symbol, String)):
            return "<unnamed>"
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
    # The counting home reads ADRs with the stdlib alone for the hook. On the day that reading drifts from the Hy reader,
    # the ledger silently becomes false, so compare against the Hy answer on all real ADRs (R9).
    checker = _load_checker()
    paths = sorted(ROOT.glob(checker.ADR_GLOB))
    assert paths, "no ADR found"
    for path in paths:
        text = path.read_text(encoding="utf-8")
        ours = {key: sorted(names) for key, names in checker.adr_items(path.name, text).items()}
        assert ours == _hy_items(path.name, text), f"{path.name}: the counting home's reading differs from the Hy reader"


def test_semgrep_rule_ids_agree_with_yaml_parser() -> None:
    # The counting home uses the stdlib alone, so it picks up .semgrep.yaml rule ids by line shape. On the day that pickup drifts from the
    # sequence of rule ids read by YAML syntax (indentation changes, `- id:` inside comments or strings), the ledger silently becomes false, so
    # compare against the YAML parser's answer (R9).
    checker = _load_checker()
    text = (ROOT / checker.SEMGREP_PATH).read_text(encoding="utf-8")
    parsed = [str(rule["id"]) for rule in yaml.safe_load(text)["rules"]]
    assert sorted(checker._SEMGREP_RULE.findall(text)) == sorted(parsed), (
        "the counting home's pickup of .semgrep.yaml rule ids differs from the YAML parser"
    )
