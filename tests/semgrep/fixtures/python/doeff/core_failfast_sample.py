def load_optional_value_bad() -> object | None:
    try:
        return object()
    except Exception:
        return None
