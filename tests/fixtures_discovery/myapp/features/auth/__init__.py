"""Auth feature module."""

from doeff import Program, ProgramInterpreter


def auth_interpreter(prog: Program[any]) -> any:
    """
    Custom auth interpreter (closer than base).
    # doeff: interpreter, default
    """
    engine = ProgramInterpreter()
    # Could add auth-specific handling here
    return engine.run(prog).value


# Auth-specific environment
# doeff: default
auth_env: dict = {"auth_provider": "oauth2", "token_expiry": 3600}
