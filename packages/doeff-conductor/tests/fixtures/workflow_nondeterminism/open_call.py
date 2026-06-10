def write_log(message: str) -> None:
    with open("workflow.log", "w") as handle:
        handle.write(message)
