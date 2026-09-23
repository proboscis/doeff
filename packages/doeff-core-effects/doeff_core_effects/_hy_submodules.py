"""Load Hy (and its import hook) only when a ``.hy`` submodule of this package is imported.

This package used to import doeff_hy eagerly (effects.py pulled in the Hy HTTP
effects), which registered Hy's import hook as a side effect of every
``import doeff``; importing ``doeff_core_effects.http_effects`` or
``._memo_handlers_impl`` directly relied on that. The hook is now registered on
the first import of a ``.hy`` submodule instead (import floor of ``import doeff``
47 → 28 MiB, measured 2026-09-23). Importing this module installs the finder.
"""

import os
import sys

_PACKAGE = __name__.rpartition(".")[0]
_PACKAGE_DIR = os.path.dirname(__file__)


class HySubmoduleFinder:
    @classmethod
    def find_spec(cls, fullname, path=None, target=None):  # noqa: ARG003 - MetaPathFinder protocol
        prefix = _PACKAGE + "."
        if not fullname.startswith(prefix):
            return
        leaf = fullname[len(prefix):].replace("-", "_")
        if "." in leaf or not os.path.exists(os.path.join(_PACKAGE_DIR, leaf + ".hy")):
            return
        import doeff_hy  # noqa: F401 - registers Hy's import hook

        # Returning None lets the finders after this one see the .hy file.


if HySubmoduleFinder not in sys.meta_path:
    sys.meta_path.insert(0, HySubmoduleFinder)
