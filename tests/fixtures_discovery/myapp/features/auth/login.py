"""Login program."""

from doeff import Ask, do


@do
def login_program() -> str:
    """Example login program that uses environment."""
    auth_provider = yield Ask("auth_provider")
    timeout = yield Ask("timeout")
    return f"Login via {auth_provider} (timeout: {timeout}s)"
