"""The VM internals that doeff's own subpackages use — not user API.

doeff is the one package that imports doeff_vm. A program and its handlers import the VM's
types from ``doeff`` (``from doeff import EffectBase, Resume, WithBoundaries``); the
subpackages in this repository that need what is below the types (the Rust handlers, the
VM's counters, the handler annotations the VM reads) import it from here
(agora-redesign ki-ff728b00369a).

``native`` is the compiled module itself, so a reader looks a name up when it runs: a name
missing from an older build is found missing where it is used, and a test that replaces a
counter replaces it for every reader.
"""

from doeff_vm import doeff_vm as _compiled
from doeff_vm._effect_types import PassedEffects as PassedEffects
from doeff_vm._effect_types import declare_passes as declare_passes
from doeff_vm._effect_types import handler_spec as handler_spec
from doeff_vm._not_a_program import program_shown as program_shown
from doeff_vm._not_a_program import uncalled_do_function_call as uncalled_do_function_call
from doeff_vm._unhandled_hint import (
    register_unhandled_effect_hint as register_unhandled_effect_hint,
)

native = _compiled
