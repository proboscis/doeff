"""The error of an Ask whose key the env does not hold (reader, lazy_ask strict).

The message names the keys the env does hold and the close names (difflib), keeps
its newlines in ``str()`` (KeyError's own ``str()`` quotes the message and folds
them into ``\\n``), and recommends no CLI flag to a program run from Python — the
CLI adds its own ``--hy`` hint (tests/cli/test_cli_missing_env_key_hint.py).
The error stays a KeyError, so ``except KeyError`` still catches it.

Card: doeff-publish 8 の誤りの表の 7 (e08_reader_missing_key).
"""

from __future__ import annotations

import pickle

import pytest
from doeff_core_effects import Ask, Local, Try, lazy_ask, reader, scheduled, try_handler

from doeff import MissingEnvKeyError, do, run

LEGACY_CLI_FLAGS = ("--set", "--env", "--interpreter", "doeff run")


@do
def ask(key):
    return (yield Ask(key))


def reader_miss(env, key="greting") -> MissingEnvKeyError:
    with pytest.raises(KeyError) as caught:
        run(reader(env=env)(ask(key)))
    return caught.value


def lazy_ask_miss(env, key="greting", *, local=None) -> MissingEnvKeyError:
    @do
    def body():
        inner = ask(key) if local is None else Local(local, ask(key))
        return (yield Try(inner))

    result = run(scheduled(lazy_ask(env=env, strict=True)(try_handler(body()))))
    assert result.is_err(), result
    return result.error


class TestMessageNamesTheEnv:
    def test_reader_names_the_close_key_and_the_env_keys(self):
        error = reader_miss({"greeting": "hello"})

        assert str(error).splitlines() == [
            "Ask: key not found: 'greting'",
            "  Did you mean 'greeting'?",
            "  Keys in the env: 'greeting'",
            "  To provide it, put the key in the env of the handler that answers Ask:"
            " reader(env={...}) or lazy_ask(env={...}).",
        ]

    def test_lazy_ask_strict_names_keys_of_the_env_with_local_overrides(self):
        error = lazy_ask_miss({"greeting": "hello"}, local={"farewell": "bye"})

        lines = str(error).splitlines()
        assert lines[0] == "Ask: key not found: 'greting'"
        assert "  Did you mean 'greeting'?" in lines
        assert "  Keys in the env: 'greeting', 'farewell'" in lines

    def test_a_large_env_shows_a_few_keys_and_counts_the_rest(self):
        env = {f"key_{i:02d}": i for i in range(30)}

        lines = str(reader_miss(env, key="missing")).splitlines()

        keys_line = next(line for line in lines if line.startswith("  Keys in the env"))
        assert keys_line == (
            "  Keys in the env (8 of 30): 'key_00', 'key_01', 'key_02', 'key_03',"
            " 'key_04', 'key_05', 'key_06', 'key_07', … 22 more"
        )
        assert not any(line.startswith("  Did you mean") for line in lines)

    def test_an_empty_env_is_said_to_be_empty(self):
        lines = str(reader_miss({})).splitlines()

        assert "  The env is empty." in lines
        assert not any(line.startswith("  Keys in the env") for line in lines)

    def test_non_string_keys_are_listed_and_not_compared(self):
        class Database:
            pass

        lines = str(reader_miss({Database: object(), "greeting": "hello"})).splitlines()

        assert lines[0] == "Ask: key not found: 'greting'"
        assert "  Did you mean 'greeting'?" in lines
        assert f"  Keys in the env: {Database!r}, 'greeting'" in lines


class TestMessageFromPythonRecommendsNoCliFlag:
    @pytest.mark.parametrize("miss", [reader_miss, lazy_ask_miss], ids=["reader", "lazy_ask"])
    def test_no_legacy_cli_flag(self, miss):
        message = str(miss({"greeting": "hello"}))

        for flag in LEGACY_CLI_FLAGS:
            assert flag not in message


class TestErrorType:
    def test_str_keeps_the_newlines(self):
        error = reader_miss({"greeting": "hello"})

        assert "\\n" not in str(error)
        assert str(error).count("\n") >= 3
        assert not str(error).startswith(("'", '"'))

    @pytest.mark.parametrize("miss", [reader_miss, lazy_ask_miss], ids=["reader", "lazy_ask"])
    def test_is_a_key_error_of_its_own_type(self, miss):
        error = miss({"greeting": "hello"})

        assert MissingEnvKeyError is not KeyError
        assert isinstance(error, MissingEnvKeyError)
        assert isinstance(error, KeyError)
        assert error.key == "greting"
        assert error.args == ("greting",)

    def test_except_key_error_still_catches_it(self):
        try:
            run(reader(env={})(ask("missing")))
        except KeyError as error:
            caught = error
        assert isinstance(caught, MissingEnvKeyError)

    def test_survives_pickling_with_its_message(self):
        error = reader_miss({"greeting": "hello"})

        copy = pickle.loads(pickle.dumps(error))

        assert type(copy) is MissingEnvKeyError
        assert copy.key == "greting"
        assert str(copy) == str(error)
