# test_plain_app.py
from unittest import mock

from infra import User
from plain_app import welcome


def test_welcome():
    with (
        mock.patch("infra.load_user", return_value=User("Ada", "ada@example.com")),
        mock.patch("infra.send_mail") as send_mail,
    ):
        assert welcome(1) == "Welcome, Ada!"
    send_mail.assert_called_once_with("ada@example.com", "Welcome, Ada!")
