from datetime import datetime


def build_prompt() -> str:
    return datetime.now().isoformat()
