# No interpreter here, one env: it overrides the base env's timeout.
from doeff import Program, Pure

# doeff: default
features_env: Program[dict] = Pure({"api_key": "secret123", "timeout": 20})
