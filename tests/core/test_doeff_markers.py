"""Test file for doeff marker functionality."""
from doeff import Program, do


def basic_interpreter(program: Program):  # doeff: interpreter
    """A basic interpreter marked with doeff comment."""


def advanced_interpreter(  # doeff: interpreter
    program: Program,
    config: dict = None
):
    """An interpreter with marker on function definition line."""


@do
def some_transform(  # doeff: transform
    tgt: Program):
    """A transform function marked with doeff comment."""


def kleisli_function():  # doeff: kleisli
    """A Kleisli function marked with doeff comment."""


# Functions without markers (should still be detected by type analysis)
def unmarked_interpreter(program: Program):
    """An interpreter without marker, should be detected by parameter type."""


@do
def unmarked_kleisli():
    """A Kleisli function without marker, detected by @do decorator."""
