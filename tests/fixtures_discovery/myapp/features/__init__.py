"""Features module."""

from doeff import Program

# Features environment (overrides and adds)
# doeff: default
features_env: Program[dict] = Program.pure({"log_level": "DEBUG", "feature_flags": {"new_ui": True, "beta": False}})
