# test_welcome.py
from doeff import Resume, run, with_handlers

from infra import User
from welcome import LoadUser, SendMail, welcome


def test_welcome():
    sent = []

    def fake_load_user(effect: LoadUser, k):
        return Resume(k, User("Ada", "ada@example.com"))

    def fake_send_mail(effect: SendMail, k):
        sent.append((effect.to, effect.body))
        return Resume(k, None)

    program = with_handlers([fake_load_user, fake_send_mail], welcome(1))
    assert run(program) == "Welcome, Ada!"
    assert sent == [("ada@example.com", "Welcome, Ada!")]
