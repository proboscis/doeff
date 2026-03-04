"""Trace introspection effects."""


def ProgramTrace():
    """Removed in TRACE-CAPTURE-LOG-SEPARATION."""

    raise NotImplementedError(
        "ProgramTrace was removed. Use GetTraceback(k) for handler-scoped introspection."
    )


__all__: list[str] = []
