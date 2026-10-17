# trace.py
from doeff import Pass, do, handler, run, with_handlers

from production import db_load_user, smtp_send_mail
from welcome import welcome


@do
def trace(effect, k):
    print("effect:", effect)
    return (yield Pass(effect, k))  # hand it on to the next handler out


program = handler(trace)(welcome(1))
print(run(with_handlers([db_load_user, smtp_send_mail], program)))
