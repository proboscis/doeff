"""doeff-claude-judge — doeff-system-one の Judge を Claude の構造化の答えで判定する(純粋な写しと、CLI の 1 回きりの print へ運ぶ handler)。"""
from importlib import import_module
from typing import TYPE_CHECKING

from doeff_claude_judge._handler import (
    claude_judge_handler as claude_judge_handler,
)
from doeff_claude_judge._target import (
    BorrowJudgeCredential as BorrowJudgeCredential,
)
from doeff_claude_judge._target import (
    ClaudeJudgeTarget as ClaudeJudgeTarget,
)
from doeff_claude_judge._target import (
    JudgeCredential as JudgeCredential,
)
from doeff_claude_judge._target import (
    NoJudgeCredential as NoJudgeCredential,
)
from doeff_claude_judge._wire import (
    ClaudeQuery as ClaudeQuery,
)
from doeff_claude_judge._wire import (
    MalformedAnswer as MalformedAnswer,
)
from doeff_claude_judge._wire import (
    QueryImage as QueryImage,
)
from doeff_claude_judge._wiring import (
    AccountLimitHit as AccountLimitHit,
)
from doeff_claude_judge._wiring import (
    claude_judge_stack as claude_judge_stack,
)
from doeff_claude_judge._wiring import (
    fixed_credential_lender as fixed_credential_lender,
)
from doeff_claude_judge._wiring import (
    production_printer as production_printer,
)
from doeff_claude_judge._wiring import (
    scripted_printer as scripted_printer,
)


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers; `__all__` is not used (DOEFF021).
if TYPE_CHECKING:
    from doeff_claude_judge._handler import print_request as print_request

_LAZY_EXPORTS: dict[str, str] = {
    "print_request": "doeff_claude_judge._handler",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
