# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pgserver==0.1.4"]
# ///
"""Print the location of the disposable PostgreSQL binaries on one line.

Python of doeff has no pgserver wheel (only cp39 to cp312), so this script is run in a Python 3.12 environment split off by uv
(`uv run --script --locked`; version and hash are pinned by the neighboring shim.py.lock -- not added to the uv.lock of doeff), and it writes the bin dir of the PostgreSQL 16 bundled in the wheel
on the last line of standard output. The test component (the neighboring disposable_postgres.py) uses initdb, pg_ctl,
createdb, and psql in that dir directly -- the binaries themselves are not tied to the Python version. Model = the
shim.py of agora-controllers at controllers/foundation/tests/postgres_binaries/ (same version and lock).
"""

from pathlib import Path

import pgserver


def main() -> None:
    """Print the bin dir of the bundled PostgreSQL -- because the test component calls initdb and pg_ctl."""
    print(Path(pgserver.__file__).resolve().parent / "pginstall" / "bin")


if __name__ == "__main__":
    main()
