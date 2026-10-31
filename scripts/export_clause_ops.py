"""doeff-linter の data/clause_ops.json を、doeff-hy の表 CLAUSE-OPS から書き出す。

節の操作(resume・transfer・finish など)の名と終わり方の定義元は doeff-hy の表 CLAUSE-OPS 1 か所。Rust の linter は Hy を評価できない
ので、表から名・終わり方・引数の数だけを書き出した file を読む。file は生成物で手で書かない。

    uv run python scripts/export_clause_ops.py          表と file を照らす(食い違いなら 1 で終わる・書かない)
    uv run python scripts/export_clause_ops.py --write  表から file を書き直す

表に行を足した・直した後は --write で書き直す(足し忘れは tests/test_linter_clause_ops.py が赤にする)。
"""

import argparse
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  Hy の import hook を入れる(clause_ops.hy を読むため)
from doeff_hy import _clause_ops as clause_ops

LINTER_TABLE = Path(__file__).resolve().parent.parent / "packages" / "doeff-linter" / "data" / "clause_ops.json"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="表から file を書き直す(既定は照らすだけ)")
    args = parser.parse_args(argv)
    expected = clause_ops.linter_table_json()
    if args.write:
        LINTER_TABLE.write_text(expected, encoding="utf-8")
        print(f"書いた: {LINTER_TABLE}")
        return 0
    if LINTER_TABLE.read_text(encoding="utf-8") != expected:
        print(
            f"{LINTER_TABLE} が表 CLAUSE-OPS と食い違う — `uv run python scripts/export_clause_ops.py --write` で書き直す",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
