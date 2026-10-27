"""The VM's types are read from doeff's root; doeff is the one package that imports doeff_vm.

The user's answer "via-root" (lt-XM33P1YN6QBWX9VFMHT0T439FA, agora-redesign ki-ff728b00369a): a user or
a subpackage writes handlers and program nodes with ``from doeff import ...``. Every type doeff_vm
exports is reachable from doeff as the same object — under its own name, or, for the two nodes
whose plain name doeff keeps for something else, under the root's name for the node. The
semgrep rule ``doeff-vm-imported-only-by-doeff`` keeps the subpackages from importing doeff_vm
(tests/semgrep/test_vm_imported_only_by_doeff_rule.py).
"""

from __future__ import annotations

import doeff_vm
import pytest
from doeff_vm import doeff_vm as compiled_vm

import doeff
from doeff import _vm

# doeff's name for a VM node whose plain name the root does not give: ``WithHandler`` stays removed
# (tests/core/test_spec_gaps.py), and ``WithObserve`` is the wrapper that also takes a plain callable.
ROOT_NAME_OF = {"WithHandler": "WithHandlerType", "WithObserve": "WithObserveRaw"}


def _vm_types() -> list[str]:
    """The classes doeff_vm exports — the names a handler or a node builder may need."""
    return sorted(
        name
        for name, value in vars(doeff_vm).items()
        if not name.startswith("_") and isinstance(value, type)
    )


@pytest.mark.parametrize("name", _vm_types())
def test_every_vm_type_is_the_same_object_on_doeffs_root(name: str) -> None:
    root_name = ROOT_NAME_OF.get(name, name)
    assert getattr(doeff, root_name) is getattr(doeff_vm, name)


def test_the_types_added_for_writing_handlers_are_on_the_root() -> None:
    """The 7 types that were reachable only through doeff_vm before (ki-ff728b00369a)."""
    from doeff import (
        BindOpener,
        Call,
        DoFunction,
        HandlerSpec,
        IRStream,
        TailEval,
        WithBoundaries,
    )

    assert (BindOpener, Call, DoFunction, HandlerSpec, IRStream, TailEval, WithBoundaries) == (
        doeff_vm.BindOpener,
        doeff_vm.Call,
        doeff_vm.DoFunction,
        doeff_vm.HandlerSpec,
        doeff_vm.IRStream,
        doeff_vm.TailEval,
        doeff_vm.WithBoundaries,
    )


def test_the_internal_module_hands_out_the_compiled_module_itself() -> None:
    """``doeff._vm.native`` is the extension, so a reader looks a name up when it runs and a test
    that replaces a counter replaces it for every reader."""
    assert _vm.native is compiled_vm
