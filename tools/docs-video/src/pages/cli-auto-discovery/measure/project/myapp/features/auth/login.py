# The program the CLI runs: it only asks for what it needs.
from doeff import Ask, do


@do
def login():
    host = yield Ask("db_host")
    timeout = yield Ask("timeout")
    return f"{host}, timeout {timeout}"


login_program = login()
