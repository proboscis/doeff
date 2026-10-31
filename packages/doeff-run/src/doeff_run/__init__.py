"""doeff_run: the effects, values and handlers of the package (and their types), read on first use."""

from importlib import import_module
from typing import TYPE_CHECKING

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_run._values import ResourceLimits as ResourceLimits
    from doeff_run._values import RunGone as RunGone
    from doeff_run._values import RunHandle as RunHandle
    from doeff_run._values import RunLive as RunLive
    from doeff_run._values import RunPending as RunPending
    from doeff_run._values import RunSpec as RunSpec
    from doeff_run._values import RunState as RunState
    from doeff_run._values import RunStopped as RunStopped
    from doeff_run._values import StopCause as StopCause
    from doeff_run._values import VolumeMount as VolumeMount
    from doeff_run._effects import RefusalWhy as RefusalWhy
    from doeff_run._effects import RunAccepted as RunAccepted
    from doeff_run._effects import RunEvent as RunEvent
    from doeff_run._effects import RunEventPage as RunEventPage
    from doeff_run._effects import RunNameInUse as RunNameInUse
    from doeff_run._effects import RunReadEvents as RunReadEvents
    from doeff_run._effects import RunRefused as RunRefused
    from doeff_run._effects import RunRemove as RunRemove
    from doeff_run._effects import RunRemoveOutcome as RunRemoveOutcome
    from doeff_run._effects import RunStart as RunStart
    from doeff_run._effects import RunStartOutcome as RunStartOutcome
    from doeff_run._effects import RunStatus as RunStatus
    from doeff_run._effects import RunStillAlive as RunStillAlive
    from doeff_run._effects import RunStop as RunStop
    from doeff_run._effects import RunStopOutcome as RunStopOutcome
    from doeff_run._effects import StopFailed as StopFailed
    from doeff_run._faults import RunVanish as RunVanish
    from doeff_run._fake import FakeBehavior as FakeBehavior
    from doeff_run._fake import FakeRunWorld as FakeRunWorld
    from doeff_run._fake import fake_run_handler as fake_run_handler

_LAZY_EXPORTS: dict[str, str] = {
    "ResourceLimits": "doeff_run._values",
    "RunGone": "doeff_run._values",
    "RunHandle": "doeff_run._values",
    "RunLive": "doeff_run._values",
    "RunPending": "doeff_run._values",
    "RunSpec": "doeff_run._values",
    "RunState": "doeff_run._values",
    "RunStopped": "doeff_run._values",
    "StopCause": "doeff_run._values",
    "VolumeMount": "doeff_run._values",
    "RefusalWhy": "doeff_run._effects",
    "RunAccepted": "doeff_run._effects",
    "RunEvent": "doeff_run._effects",
    "RunEventPage": "doeff_run._effects",
    "RunNameInUse": "doeff_run._effects",
    "RunReadEvents": "doeff_run._effects",
    "RunRefused": "doeff_run._effects",
    "RunRemove": "doeff_run._effects",
    "RunRemoveOutcome": "doeff_run._effects",
    "RunStart": "doeff_run._effects",
    "RunStartOutcome": "doeff_run._effects",
    "RunStatus": "doeff_run._effects",
    "RunStillAlive": "doeff_run._effects",
    "RunStop": "doeff_run._effects",
    "RunStopOutcome": "doeff_run._effects",
    "StopFailed": "doeff_run._effects",
    "RunVanish": "doeff_run._faults",
    "FakeBehavior": "doeff_run._fake",
    "FakeRunWorld": "doeff_run._fake",
    "fake_run_handler": "doeff_run._fake",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import hy  # noqa: F401 - the modules behind `_` are Hy modules; importing hy lets Python find them

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
