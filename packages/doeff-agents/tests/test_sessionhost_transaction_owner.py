"""store の connection の上で transaction を開き閉じるのは db-immediate-transaction だけ(ADR-DOE-AGENTS-004 R15)。

sessionhost の source を走査し、transaction を開閉する綴り(BEGIN / COMMIT / END / ROLLBACK / SAVEPOINT /
RELEASE の SQL、connection の commit / rollback、暗黙に COMMIT する executescript)を、許した定義の外で
見つけたら赤にする。R15 の振る舞いの検は「開く側」を lease の 3 関数で確かめるだけで、「閉じる側」を
誰が持つかは見ていなかった。

出自: 依頼 lt-A5KGD83R9HQJ2K172V61A6VMG9 の盲検 A — cache_receipt_put の ``conn.commit()`` は、
transaction の本体の中で呼ばれると本体の途中で transaction を終わらせ、それまでの書き込みを確定させた
(本番の actor の connection は autocommit なので、transaction の外では何もしない 1 行だった)。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import hy
import hy.models

SESSIONHOST = Path(__file__).resolve().parents[1] / "src" / "doeff_agents" / "sessionhost"

#: 開閉してよい定義(sessionhost からの相対 path, トップレベルの定義の名)→ 理由。
OWNERS: dict[tuple[str, str], str] = {
    ("store.hy", "db-immediate-transaction"): "明示の transaction の唯一の型",
    ("store.hy", "db-migrate"): "開いた拍の schema 適用(actor が動く前・executescript は暗黙に COMMIT する)",
}

_CONTROL_WORDS = r"(BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE)\b"
SQL_CONTROL = re.compile(r"^\s*" + _CONTROL_WORDS, re.IGNORECASE)
HY_CONTROL = re.compile(
    r"\(\.(commit|rollback|executescript)[\s)]|\(\.execute(many)?\s+\S+\s+\"\s*" + _CONTROL_WORDS,
    re.IGNORECASE,
)


def hy_sites(path: Path) -> list[tuple[str, int]]:
    """Hy の file の中の開閉の綴りを (囲むトップレベルの定義の名, 行) で返す。"""
    text = path.read_text(encoding="utf-8")
    spans: list[tuple[int, int, str]] = []
    reader = hy.HyReader(use_current_readers=False)
    for form in hy.read_many(text, filename=str(path), reader=reader, skip_shebang=True):
        if (isinstance(form, hy.models.Expression) and len(form) > 1
                and isinstance(form[1], hy.models.Symbol)):
            spans.append((form.start_line, form.end_line, str(form[1])))
    sites: list[tuple[str, int]] = []
    for match in HY_CONTROL.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        owner = next((name for start, end, name in spans if start <= line <= end), "<module>")
        sites.append((owner, line))
    return sites


def python_sites(path: Path) -> list[tuple[str, int]]:
    """Python の file の中の開閉の呼び出しを (囲むトップレベルの定義の名, 行) で返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites: list[tuple[str, int]] = []

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = owner
            if owner == "<module>" and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                name = child.name
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                attr = child.func.attr
                first = child.args[0] if child.args else None
                if attr in ("commit", "rollback", "executescript") or (
                    attr in ("execute", "executemany")
                    and isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and SQL_CONTROL.match(first.value)
                ):
                    sites.append((name, child.lineno))
            visit(child, name)

    visit(tree, "<module>")
    return sites


def transaction_control_outside_owners(root: Path) -> list[str]:
    """root の下の .hy / .py で、OWNERS の外に在る開閉の綴りを「path:行 (定義)」で返す。"""
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix not in (".hy", ".py"):
            continue
        rel = path.relative_to(root).as_posix()
        sites = hy_sites(path) if path.suffix == ".hy" else python_sites(path)
        found.extend(f"{rel}:{line} ({owner})" for owner, line in sites
                     if (rel, owner) not in OWNERS)
    return found


def test_only_the_transaction_type_opens_and_closes_transactions() -> None:
    violations = transaction_control_outside_owners(SESSIONHOST)
    assert violations == [], (
        "store の connection の上で transaction を開閉するのは db-immediate-transaction だけ"
        "(ADR-DOE-AGENTS-004 R15)。helper は commit / rollback を撃たず、明示の transaction が要るなら"
        f"本体を defk にして db-immediate-transaction に渡す: {violations}"
    )


def test_every_owner_still_exists() -> None:
    # 許した定義が消えた・改名された時に、許しの行が黙って残って次の違反を 1 つ隠さないため。
    present = {(path.relative_to(SESSIONHOST).as_posix(), owner)
               for path in SESSIONHOST.rglob("*.hy")
               for owner, _line in hy_sites(path)}
    assert set(OWNERS) <= present, sorted(set(OWNERS) - present)


def test_scanner_finds_each_spelling(tmp_path: Path) -> None:
    # 走査器そのものの正常例と違反例: 各綴りを、許した定義の外なら拾い、許した定義の中なら拾わない。
    (tmp_path / "store.hy").write_text(
        '(defk db-immediate-transaction [conn body]\n'
        '  (.execute conn "BEGIN IMMEDIATE")\n'
        '  (.execute conn "COMMIT"))\n'
        '(defk sneaky [conn]\n'
        '  (.commit conn)\n'
        '  (.rollback conn)\n'
        '  (.executescript conn "x")\n'
        '  (.execute conn "savepoint s1"))\n',
        encoding="utf-8",
    )
    (tmp_path / "helper.py").write_text(
        "def put(conn):\n"
        "    conn.execute('INSERT INTO t VALUES (1)')\n"
        "    conn.commit()\n"
        "    conn.execute('ROLLBACK')\n",
        encoding="utf-8",
    )
    assert transaction_control_outside_owners(tmp_path) == [
        "helper.py:3 (put)", "helper.py:4 (put)",
        "store.hy:5 (sneaky)", "store.hy:6 (sneaky)", "store.hy:7 (sneaky)", "store.hy:8 (sneaky)",
    ]
