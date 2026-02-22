def _traceback_fallback_bad() -> str:
    try:
        return "ok"
    except Exception:
        return "fallback"
