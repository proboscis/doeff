import resource
import sys

# Limit to 32GB to prevent OOM-killing the parent tmux/codex process
_MAX_RSS_BYTES = 32 * 1024 * 1024 * 1024  # 32GB
try:
    resource.setrlimit(resource.RLIMIT_AS, (_MAX_RSS_BYTES, _MAX_RSS_BYTES))
except (ValueError, resource.error):
    pass  # Some systems don't support RLIMIT_AS
