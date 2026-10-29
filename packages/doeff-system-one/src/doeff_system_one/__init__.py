"""doeff-system-one — 較正された判定器(System One)への問いを effect で表す。"""
from importlib import import_module
from typing import TYPE_CHECKING

from doeff_system_one._effects import (
    Judge as Judge,
)
from doeff_system_one._handlers import (
    ScriptedJudgeState as ScriptedJudgeState,
)
from doeff_system_one._handlers import (
    scripted_judge_handler as scripted_judge_handler,
)
from doeff_system_one._judgment import (
    STATE_CHARS_ADVISED as STATE_CHARS_ADVISED,
)
from doeff_system_one._judgment import (
    answered as answered,
)
from doeff_system_one._judgment import (
    calibration_ok as calibration_ok,
)
from doeff_system_one._judgment import (
    chosen as chosen,
)
from doeff_system_one._judgment import (
    clip as clip,
)
from doeff_system_one._judgment import (
    crosses as crosses,
)
from doeff_system_one._programs import (
    judge as judge,
)
from doeff_system_one._programs import (
    judge_many as judge_many,
)
from doeff_system_one._types import (
    Answer as Answer,
)
from doeff_system_one._types import (
    Image as Image,
)
from doeff_system_one._types import (
    JudgeError as JudgeError,
)
from doeff_system_one._types import (
    JudgeUsageLimited as JudgeUsageLimited,
)
from doeff_system_one._types import (
    Question as Question,
)
from doeff_system_one._types import (
    Verdict as Verdict,
)
from doeff_system_one._types import (
    answer_choice as answer_choice,
)
from doeff_system_one._types import (
    answer_noul as answer_noul,
)
from doeff_system_one._types import (
    answer_score as answer_score,
)
from doeff_system_one._types import (
    choice as choice,
)
from doeff_system_one._types import (
    noul as noul,
)
from doeff_system_one._types import (
    score as score,
)


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers; `__all__` is not used (DOEFF021).
if TYPE_CHECKING:
    from doeff_system_one._handlers.testing import Script as Script
    from doeff_system_one._types import Kind as Kind

_LAZY_EXPORTS: dict[str, str] = {
    "Script": "doeff_system_one._handlers.testing",
    "Kind": "doeff_system_one._types",
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
