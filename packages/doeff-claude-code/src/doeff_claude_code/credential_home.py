# moved-submodule-shim: doeff_claude_code.credential_home -> doeff_claude_code._credential_home
"""Moved: ``doeff_claude_code.credential_home`` is now ``doeff_claude_code._credential_home`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.credential_home…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._credential_home import CLI_SCOPES as CLI_SCOPES
    from doeff_claude_code._credential_home import CREDENTIAL_HOMES_DIR as CREDENTIAL_HOMES_DIR
    from doeff_claude_code._credential_home import CREDENTIALS_FILE as CREDENTIALS_FILE
    from doeff_claude_code._credential_home import KEPT_DIRS as KEPT_DIRS
    from doeff_claude_code._credential_home import UNLINKED_NAMES as UNLINKED_NAMES
    from doeff_claude_code._credential_home import LentCredential as LentCredential
    from doeff_claude_code._credential_home import Path as Path
    from doeff_claude_code._credential_home import close_credential_home as close_credential_home
    from doeff_claude_code._credential_home import credential_document as credential_document
    from doeff_claude_code._credential_home import open_credential_home as open_credential_home
    from doeff_claude_code._credential_home import replace_credential as replace_credential
    from doeff_claude_code._credential_home import write_new_file as write_new_file

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._credential_home", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._credential_home")
