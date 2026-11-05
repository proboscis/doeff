"""Storage keys for defhandler session val / session var (ADR-DOE-HY-006).

Values of session val / session var are not held in hidden Python places (module variables, handler objects, closures);
they are read and written through doeff's state effects (Get / Put — state monad). The key is decided by this one function.
The outer handler and the checker look up keys with the same function, so answering the value to that key's Get replaces
the value (without running the initializer), and receiving Put lets one observe writes.

The key shape is the same as the old lazy / lazy-val / lazy-var: `<module __name__>/<handler name>/<variable name>`
(names stay in Hy spelling, not mangled). Even when rewriting from the old forms to session val / session var,
values already in the same session are carried over as is.

Warning: even for a handler that takes arguments (`(defhandler h [cfg] …)`), the arguments do not enter the key: installing the same handler twice with different arguments
shares values within the same session (a property inherited from the old lazy-val).
"""




def session_key(module: str, handler: str, name: str) -> str:
    """The key to Get / Put the value of a session val / session var. The outer handler and the checker also look up keys with this."""
    return f"{module}/{handler}/{name}"
