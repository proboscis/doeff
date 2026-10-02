# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pgserver==0.1.4"]
# ///
"""使い捨ての PostgreSQL の binary の置き場を 1 行で出す(agora-redesign #2830)。

doeff の Python には pgserver の wheel が無い(cp39〜cp312 だけ)ので、この script を uv の分けた Python 3.12 の環境で
走らせ(`uv run --script --locked`・版と hash は隣の shim.py.lock が固定する — doeff の uv.lock には足さない)、wheel に同梱された
PostgreSQL 16 の bin の dir を標準出力の最後の行へ書く。検の部品(隣の disposable_postgres.py)はその dir の initdb・pg_ctl・
createdb・psql を直に使う — binary そのものは Python の版に縛られない。手本 = agora-controllers の
controllers/foundation/tests/postgres_binaries/shim.py(同じ版と lock)。
"""

from pathlib import Path

import pgserver


def main() -> None:
    """同梱の PostgreSQL の bin の dir を出す — 検の部品が initdb と pg_ctl を呼ぶため。"""
    print(Path(pgserver.__file__).resolve().parent / "pginstall" / "bin")


if __name__ == "__main__":
    main()
