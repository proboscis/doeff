"""Console-script shim for the Hy session host (C3).

`python -m` / console scripts cannot target a ``.hy`` module directly —
importing :mod:`hy` first registers the Hy import hook, after which the
host module loads like any other.  Keep this file logic-free: the real
entry is ``doeff_agents.sessionhost.host.main``.
"""

import hy  # noqa: F401  # registers the .hy importer

from doeff_agents.sessionhost import host


def main() -> None:
    host.main()
