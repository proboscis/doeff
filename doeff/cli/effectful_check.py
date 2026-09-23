"""``doeff-effectful-check PATH ...``: the @effectful rewrite's checks, without importing.

Runs the same validation the import hook runs (doeff/_effectful_rewrite.py) on every
``.py`` file under the given paths that mentions ``effectful``, and prints each refusal as
``path:line:column: message``. Exit status: 0 = clean, 1 = refusals, 2 = usage error.
"""

import sys
from collections.abc import Sequence

from doeff._effectful_rewrite import check_paths


def main(argv: Sequence[str] | None = None) -> int:
    paths = list(sys.argv[1:] if argv is None else argv)
    if not paths:
        print("usage: doeff-effectful-check PATH [PATH ...]", file=sys.stderr)
        return 2
    errors = check_paths(paths)
    for error in errors:
        print(f"{error.filename}:{error.lineno}:{error.offset}: {error.msg}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
