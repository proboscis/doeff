"""Test file for doeff marker functionality."""
from doeff import Program, do


def basic_interpreter(program: Program):  # doeff: interpreter
    """A basic interpreter marked with doeff comment."""
    pass


def advanced_interpreter(  # doeff: interpreter
    program: Program,
    config: dict = None
):
    """An interpreter with marker on function definition line."""
    pass


@do
def some_transform(  # doeff: transform
    tgt: Program):
    """A transform function marked with doeff comment."""
    pass


def kleisli_function():  # doeff: kleisli
    """A Kleisli function marked with doeff comment."""
    pass


# Functions without markers (should still be detected by type analysis)
def unmarked_interpreter(program: Program):
    """An interpreter without marker, should be detected by parameter type."""
    pass


@do
def unmarked_kleisli():
    """A Kleisli function without marker, detected by @do decorator."""
    pass