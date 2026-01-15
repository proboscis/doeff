"""Base module for test app."""

from doeff import Program, CESKInterpreter


def base_interpreter(prog: Program[any]) -> any:
    """
    Base interpreter for myapp.
    # doeff: interpreter, default
    """
    engine = CESKInterpreter()
    return engine.run(prog).value


# Base environment
# doeff: default
base_env: Program[dict] = Program.pure({"db_host": "localhost", "log_level": "INFO", "timeout": 10})
