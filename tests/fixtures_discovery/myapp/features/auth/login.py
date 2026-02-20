from __future__ import annotations

from doeff import Ask, do


@do
def login_program():
    auth_method = yield Ask("auth_method")
    timeout = yield Ask("timeout")
    return f"Login via {auth_method} (timeout: {timeout}s)"
