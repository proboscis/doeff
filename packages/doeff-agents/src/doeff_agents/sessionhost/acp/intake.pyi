"""intake.hy の公開面の型(受け付けの係と直列の区間の handler — card acp:kanban-issue:ki-e786e72e2ae7)。"""

from doeff import Program

class IntakeBook:
    pending: dict[str, str]
    tasks: dict[str, object]
    done: list[object]
    def __init__(self) -> None: ...

def with_inline_intake(program: Program) -> Program: ...
