from pathlib import Path


def write_log(message: str) -> None:
    Path("workflow.log").write_text(message)
