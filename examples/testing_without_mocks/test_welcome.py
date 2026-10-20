# test_welcome.py
from doeff import Resume, do, handler, run

from infra import User
from welcome import LoadUser, SendMail, welcome


@handler
@do
def fake_load_user(effect: LoadUser, k):
    return (yield Resume(k, User("Ada", "ada@example.com")))


def test_welcome():
    sent = []

    @handler
    @do
    def fake_send_mail(effect: SendMail, k):
        sent.append((effect.to, effect.body))
        return (yield Resume(k, None))

    assert run(fake_send_mail(fake_load_user(welcome(1)))) == "Welcome, Ada!"
    assert sent == [("ada@example.com", "Welcome, Ada!")]
