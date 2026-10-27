from __future__ import annotations

import doeff_vm  # fires
import doeff_vm.doeff_vm as ext  # fires
from doeff_vm import EffectBase  # fires
from doeff_vm._effect_types import declare_passes  # fires

import doeff_vm_like_name
from doeff import EffectBase as RootEffectBase
from doeff._vm import native

NAMES = (doeff_vm, ext, EffectBase, declare_passes, doeff_vm_like_name, RootEffectBase, native)
