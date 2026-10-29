# What a test run passes by hand: its own interpreter and its own env.
from doeff_core_effects import reader, scheduled, state, writer

from doeff import Program, Pure, run


def test_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    return run(scheduled(state()(writer(reader(env=run(env))(prog)))))


test_env: Program[dict] = Pure({"db_host": "fake-db", "timeout": 1})
