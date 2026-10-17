# Not shown in the article: proves the "forget one handler" snippet and its message.
import pytest
from doeff import Resume, UnhandledEffect, run, with_handlers

import infra
from infra import User
from welcome import LoadUser, welcome

# The first line of the error, as the article shows it.
MISSING_SEND_MAIL = (
    "no handler found for effect: SendMail (SendMail(to='ada@example.com', body='Welcome, Ada!'))"
)


def fake_load_user(effect: LoadUser, k):
    return Resume(k, User("Ada", "ada@example.com"))


def test_forgetting_a_handler_is_an_error_and_nothing_real_runs():
    infra.CALLS.clear()
    with pytest.raises(UnhandledEffect) as err:
        run(with_handlers([fake_load_user], welcome(1)))
    assert str(err.value).splitlines()[0] == MISSING_SEND_MAIL
    assert infra.CALLS == []


def test_untyped_fake_swallows_the_other_effect():
    # Limits section: "A handler without an annotation receives every effect."
    def untyped_fake(effect, k):
        return Resume(k, User("Ada", "ada@example.com"))

    # SendMail was answered by the fake too, so the run still returns.
    assert run(with_handlers([untyped_fake], welcome(1))) == "Welcome, Ada!"
