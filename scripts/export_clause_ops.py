"""Write the data/clause_ops.json of doeff-linter out of the CLAUSE-OPS table of doeff-hy.

The single source of definition for the names and endings of clause operations (resume, transfer, finish, etc.) is the doeff-hy table CLAUSE-OPS. The Rust linter cannot evaluate Hy,
so it reads a file with only the names, endings, and argument counts written out from the table. The file is generated and not written by hand.

    uv run python scripts/export_clause_ops.py          compare the table and the file (exit 1 on mismatch; writes nothing)
    uv run python scripts/export_clause_ops.py --write  rewrite the file from the table

After adding or changing a row of the table, rewrite with --write (forgetting to is turned red by tests/test_linter_clause_ops.py).
"""

import argparse
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  install the Hy import hook (to read clause_ops.hy)
from doeff_hy import _clause_ops as clause_ops

LINTER_TABLE = Path(__file__).resolve().parent.parent / "packages" / "doeff-linter" / "data" / "clause_ops.json"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the file from the table (default only compares)")
    args = parser.parse_args(argv)
    expected = clause_ops.linter_table_json()
    if args.write:
        LINTER_TABLE.write_text(expected, encoding="utf-8")
        print(f"wrote: {LINTER_TABLE}")
        return 0
    if LINTER_TABLE.read_text(encoding="utf-8") != expected:
        print(
            f"{LINTER_TABLE} disagrees with the table CLAUSE-OPS -- rewrite with `uv run python scripts/export_clause_ops.py --write`",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
