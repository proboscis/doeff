# Not shown in the article: proves the claim "drop the second patch and the real send_mail runs".
from unittest import mock

import infra
from infra import User
from plain_app import welcome


def test_no_real_call_when_both_patched():
    infra.CALLS.clear()
    with (
        mock.patch("infra.load_user", return_value=User("Ada", "ada@example.com")),
        mock.patch("infra.send_mail"),
    ):
        assert welcome(1) == "Welcome, Ada!"
    assert infra.CALLS == []


def test_forgot_one_patch_still_passes_and_runs_the_real_mailer():
    infra.CALLS.clear()
    with mock.patch("infra.load_user", return_value=User("Ada", "ada@example.com")):
        assert welcome(1) == "Welcome, Ada!"
    assert infra.CALLS == ["REAL send_mail('ada@example.com')"]


def test_from_import_makes_the_patch_miss():
    # "Change `import infra` to `from infra import load_user, send_mail` and both patches miss."
    import types

    src = (
        "from infra import load_user, send_mail\n"
        "def welcome(user_id):\n"
        "    user = load_user(user_id)\n"
        "    body = f'Welcome, {user.name}!'\n"
        "    send_mail(user.email, body)\n"
        "    return body\n"
    )
    mod = types.ModuleType("plain_app_from")
    exec(src, mod.__dict__)
    infra.CALLS.clear()
    with (
        mock.patch("infra.load_user", return_value=User("Ada", "ada@example.com")),
        mock.patch("infra.send_mail"),
    ):
        result = mod.welcome(1)
    assert result == "Welcome, Real Person!"
    assert infra.CALLS == ["REAL load_user(1)", "REAL send_mail('real@example.com')"]
