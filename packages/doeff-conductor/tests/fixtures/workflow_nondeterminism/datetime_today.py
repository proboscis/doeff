import datetime


def build_prompt() -> str:
    return datetime.datetime.today().isoformat()
