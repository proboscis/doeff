# A program outside myapp: no marked interpreter is on its module path, so doeff run falls back to its built-in
# default interpreter, which ignores envs.
from doeff import Ask, do


@do
def job():
    return (yield Ask("timeout"))


job_program = job()


@do
def ping():
    return "pong"


ping_program = ping()
