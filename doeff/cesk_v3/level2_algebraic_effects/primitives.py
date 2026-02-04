from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
from doeff.cesk_v3.level2_algebraic_effects.frames import (
    DispatchingFrame,
    EffectBase,
    Handler,
    WithHandlerFrame,
)

if TYPE_CHECKING:
    from doeff.program import Program

T = TypeVar("T")

Frame = ReturnFrame | WithHandlerFrame | DispatchingFrame


class ControlPrimitive:
    pass


@dataclass(frozen=True)
class WithHandler(ControlPrimitive, Generic[T]):
    handler: Handler
    program: Program[T]


@dataclass(frozen=True)
class Resume(ControlPrimitive):
    value: Any


@dataclass(frozen=True)
class Forward(ControlPrimitive):
    effect: EffectBase


@dataclass(frozen=True)
class Continuation:
    """A captured continuation that can be resumed later.

    Continuations are first-class values that represent "the rest of the computation."
    They can be stored, passed around, and resumed with ResumeContinuation.

    Attributes:
        cont_id: Unique identifier for one-shot tracking
        frames: The captured continuation frames

    One-shot invariant: Each continuation can only be resumed once.
    Attempting to resume a continuation twice raises RuntimeError.
    """

    cont_id: int
    frames: tuple[Frame, ...]


@dataclass(frozen=True)
class GetContinuation(ControlPrimitive):
    """Capture the current continuation as a first-class value.

    Returns a Continuation object to the handler. The DispatchingFrame
    is NOT consumed - handler can still Resume/Forward after capturing.

    This enables scheduler patterns where:
    1. Handler captures continuation (GetContinuation)
    2. Handler stores continuation for later (e.g., in a queue)
    3. Handler can Resume current computation OR switch to another

    Unlike Resume, GetContinuation does NOT immediately resume anything.
    It just returns the capability to resume later.
    """

    pass


@dataclass(frozen=True)
class ResumeContinuation(ControlPrimitive):
    """Resume a previously captured continuation with a value.

    Unlike Resume (which resumes the current dispatch's continuation),
    ResumeContinuation can resume ANY captured continuation.

    This is the key primitive for cooperative scheduling:
    - Resume(v) = resume current DF's continuation immediately
    - ResumeContinuation(k, v) = resume ANY continuation k

    When ResumeContinuation is executed:
    1. The current computation is abandoned (current K is dropped)
    2. The captured continuation k becomes the new K
    3. Execution continues with value v

    One-shot invariant: The continuation must not have been resumed before.

    Attributes:
        continuation: The captured Continuation to resume
        value: The value to send to the resumed continuation
    """

    continuation: Continuation
    value: Any
