"""ExternalPromise for external world integration."""


from typing import TYPE_CHECKING, Any, TypeVar

import doeff_vm

if TYPE_CHECKING:
    from doeff.effects.spawn import Future

T = TypeVar("T")


ExternalPromise = doeff_vm.ExternalPromise
CreateExternalPromiseEffect = doeff_vm.CreateExternalPromiseEffect


def CreateExternalPromise() -> ExternalPromise:
    """Create a Rust-backed ExternalPromise handle."""
    from doeff import do

    @do
    def _program():
        raw_handle = yield CreateExternalPromiseEffect()
        if isinstance(raw_handle, ExternalPromise):
            return raw_handle
        raise TypeError(
            f"CreateExternalPromise expected ExternalPromise handle, got {type(raw_handle).__name__}"
        )

    return _program()


__all__ = [
    "CreateExternalPromise",
    "CreateExternalPromiseEffect",
    "ExternalPromise",
]
