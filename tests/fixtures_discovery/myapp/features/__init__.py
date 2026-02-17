from __future__ import annotations

from doeff import Program


# doeff: default
features_env: Program[dict] = Program.pure({"feature_flag": True})
