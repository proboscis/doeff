"""Types for doeff_hy._session (called by the expansion of defhandler's session val / session var).

The implementation session.py is annotated, but pyright in strict mode flags a module of a package without a stub as "stub missing",
so, like the other public modules (wire.pyi, record.pyi, etc.), the types are declared in a stub.
"""

def session_key(module: str, handler: str, name: str) -> str: ...
