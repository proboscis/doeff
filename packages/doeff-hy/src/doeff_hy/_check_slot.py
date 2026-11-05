"""Machine check slot — a single run of doeff-hy-check takes, before expansion and pyright, one slot shared with the checks (pytest, code-quality, doeff-linter, long-command
tasks) running on the same machine (Pod).

Why it is needed: one doeff-hy-check starts 4 expansion children and pyright, and uses about 10 GiB of RSS. The entry point agora-controllers measures by hand
(the gate of scripts/hy_type_check.hy) now takes a slot, but doeff-hy-check started directly by a conversation
(`uv run doeff-hy-check …`, `python -m doeff_hy._static_check …`) ran outside the slot. Instead of wrapping every path that launches it, the slot is taken at the single point of the entry main
(the same idea as doeff-linter's packages/doeff-linter/src/check_slot.rs).

Shape: the canonical source of the slot is `agent/tests/machine_check_slots.py` in agora's tooling dotfiles (the count, the location, and the wait limit are held by the single neighboring
machine_check_slots.json). This module holds no values; it reads the canonical source by its path under the host's home and only calls read_policy and take
(the same shape as code-quality's quality/machine_slot.py and agora-controllers' scripts/check_slot.hy).
  - A host with no canonical source (a machine where dotfiles are not placed — a machine that cloned only doeff) passes through silently.
  - When the canonical source or declaration cannot be read, when the canonical source's entry points do not line up, or when the canonical take fails inside (the canonical source follows the tip of dotfiles, and doeff is fixed by the user's
    pin), it announces in one line and passes through — a broken slot does not stop the check from running (the same as the canonical pytest admission). If a failure inside the canonical source
    escaped as a bare traceback, it could not be told apart from main's exit 1 (= a new red).
  - The policy (the answer of read_policy) and the holding (the answer of take) are not opened — they are only passed as they are to the canonical source's public entry points (take, process_width),
    without reading field names, rebuilding, or mapping to other names (the decision to shorten or extend the wait limit and the policy's field names are not placed here). Only the release entry that
    frees the holding is kept in SlotHeld. The shape is guarded by the AST check in tests/test_check_slot_opacity.py (in both the incoming and outgoing directions).
  - The number of processes allowed to run side by side inside one slot is received by passing the policy, holding, and environment to the canonical public entry process_width (SlotHeld.width). The nesting
    rule (inside an ancestor's slot, the width the outer one allotted is kept) and the env names live in the canonical source and are not copied here. For a canonical source without that entry (old dotfiles),
    the slot is taken and the width is None — the caller (static_check) runs with the default of a machine with no declaration (announcing in one line). Even if the width entry fails, the slot already taken is kept.
  - A descendant of the process that holds a slot (main called by a pytest check, doeff-hy-check started by the gate) does not take a second one. The canonical take decides this
    (via the /proc ancestor chain; on a machine without /proc, via the mark of the canonical HOLDER_ENV).
  - The canonical source announces waits without staying silent (`check-slot: … waiting — waited N seconds / limit … holder pid …`). If the limit is exceeded, SlotExpired — main starts neither expansion nor
    pyright and ends with "could not run" (exit 2) (neither a pass nor a red).
  - The env handed to the canonical source is a copy read by name, one at a time (doeff_hy._env_places.EnvironmentByName) — doeff does not know which names the canonical take reads
    (today the holder's mark, tomorrow perhaps the conversation id). If the copy were narrowed to one name, then when the canonical source read a new name,
    only doeff would silently fail to follow (the counterexample of blind review A).
  - While holding, the mark to descendants (the canonical HOLDER_ENV) is not placed in env. Descendants (expansion children, pyright, and the python pyright starts) do not take a slot, and placing it would
    require writing os.environ (doeff-linter's DOEFF004). On Linux the canonical source decides descendants by the /proc ancestor chain.
There is no entry to change the canonical source's path or values through env (with such an entry, only the check of a conversation that changed it would count with a different number and the slot would split — the note at the head of the canonical source).
"""

import importlib.util
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TextIO

from doeff_hy._env_places import EnvironmentByName

#: Location of the canonical source (relative to the host's home — the same as doeff-linter's check_slot.rs and code-quality's quality/machine_slot.py).
CANON_UNDER_HOME = Path("dotfiles", "agent", "tests", "machine_check_slots.py")
#: Head of the name of the module that read the canonical source (the same key as dotfiles' admission broad_run_admission _check_slots and code-quality — inside pytest it
#: uses the instance the admission read, and does not create a second instance in the same process).
MODULE_KEY_PREFIX = "ai_machine_check_slots@"
#: Head of the announcement line (the same as the canonical MARK).
MARK = "check-slot:"
#: Among the canonical public entry points: functions that, if they do not line up, make it pass through without taking a slot (required), functions for which a slot is still taken when missing (optional — without them the width does not arrive), and
#: the names of exceptions to catch. These 3 sets are the only names this module may read from the canonical source (guarded by tests/test_check_slot_opacity.py).
CANON_CALLS = ("read_policy", "take")
CANON_OPTIONAL_CALLS = ("process_width",)
CANON_ERRORS = ("PolicyUnreadable", "SlotWaitExpired")


@dataclass(frozen=True)
class SlotHeld:
    """The check may run. let_go = the entry that releases the slot taken (None for pass-through or when an ancestor holds it); width = the number of processes the canonical source answered may run side by side inside one slot
    (None if the canonical entry is absent, failed, or passed through — the caller runs with the default of a machine with no declaration).
    The canonical holding itself is not kept — only the release entry (the content of the holding is closed inside take_slot)."""

    let_go: Callable[[], None] | None = None
    width: int | None = None

    def release(self) -> None:
        """Release the slot taken (does nothing on pass-through, and nothing the second time — the canonical release ignores a second call)."""
        if self.let_go is not None:
            self.let_go()


@dataclass(frozen=True)
class SlotExpired:
    """The slot wait exceeded the limit declared by the canonical source — the check did not run (neither red nor a pass). line = the canonical source's one announcement line."""

    line: str


def slot_label(root: Path, conversation: str | None) -> str:
    """The holder's announcement (pure) — the name of the root being checked (the worktree name) and, if any, the conversation id (the same shape as the announcement in the canonical pytest and command wrapper)."""
    place = root.name or str(root)
    return f"doeff-hy-check {place}" + (f" ({conversation})" if conversation else "")


def _named(out: TextIO, text: str) -> None:
    """Write the reason for passing through to out as one announcement line (do not silently run outside the slot)."""
    out.write(f"{MARK} {text}\n")
    out.flush()


def canon_module(home: Path, out: TextIO) -> ModuleType | None:
    """Read the canonical source by the host's path. None for a host without it (pass-through). If it cannot be read, announce one line and return None. If a module that read the same file exists, that one
    (inside pytest the admission has already read the canonical source)."""
    path = home / CANON_UNDER_HOME
    if not path.is_file():
        return None
    real = os.path.realpath(path)
    key = MODULE_KEY_PREFIX + real
    loaded = sys.modules.get(key)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(key, real)
    if spec is None or spec.loader is None:
        _named(out, f"cannot read the slot canonical source {real}, so running without taking a slot")
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module  # the canonical dataclass reads sys.modules
    try:
        spec.loader.exec_module(module)
    except Exception as error:  # a broken canonical source (syntax, import) is announced and passed through — it does not stop the check from running
        sys.modules.pop(key, None)
        _named(
            out, f"cannot read the slot canonical source {real}, so running without taking a slot ({type(error).__name__}: {error})"
        )
        return None
    return module


def unfit_names(module: ModuleType) -> list[str]:
    """Among the canonical entry points, names that are not callable functions or not exception classes (pure). Empty = the entry points line up."""
    found = vars(module)
    return [name for name in CANON_CALLS if not callable(found.get(name))] + [
        name for name in CANON_ERRORS if not _error_class(found.get(name))
    ]


def _error_class(value: object) -> bool:
    return isinstance(value, type) and issubclass(value, BaseException)


def absent_optional(module: ModuleType) -> list[str]:
    """Among the canonical optional entry points, those that are not callable (pure). Empty = all present. A slot is still taken without them."""
    found = vars(module)
    return [name for name in CANON_OPTIONAL_CALLS if not callable(found.get(name))]


def take_slot(home: Path, label: str, out: TextIO) -> SlotHeld | SlotExpired:
    """Take one slot shared with the checks running on the same machine (if full, the canonical source announces the holder and waits). home = the host's home where the canonical source is looked for;
    label = the holder's announcement; out = where the announcement lines go. The policy and holding are passed to the canonical entry points as they are, without opening them. The answer = SlotHeld
    (holding the slot until release is called; width = the parallel count the canonical source answered), or SlotExpired if the wait limit was exceeded."""
    module = canon_module(home, out)
    if module is None:
        return SlotHeld()
    unfit = unfit_names(module)
    if unfit:
        _named(
            out,
            f"the entry points of the slot canonical source {os.path.realpath(home / CANON_UNDER_HOME)} do not line up ({', '.join(unfit)}), so running without taking a slot",
        )
        return SlotHeld()
    environ = EnvironmentByName()
    try:
        policy = module.read_policy()
        holding = module.take(policy, label, environ, out)
    except module.PolicyUnreadable as error:
        _named(out, f"cannot read the declaration, so running without taking a slot — {error}")
        return SlotHeld()
    except module.SlotWaitExpired as error:
        return SlotExpired(str(error))
    except Exception as error:  # a failure inside the canonical source (code at the tip of another repo) or a calling mismatch — announce and pass through (the note at the head)
        _named(
            out, f"the slot canonical take failed, so running without taking a slot ({type(error).__name__}: {error})"
        )
        return SlotHeld()
    let_go = None if holding is None else holding.release
    width = None
    absent = absent_optional(module)
    if absent:
        _named(out, f"the slot canonical source lacks the parallel-count entry ({', '.join(absent)}), so running with the default parallel count of a machine with no declaration")
    else:
        try:  # even if the width entry fails, keep the slot taken (let_go was handed over above) — do not put it in the same enclosure as a take failure
            width = module.process_width(policy, holding, environ)
        except Exception as error:
            _named(
                out,
                f"the slot canonical process_width failed, so running with the default parallel count of a machine with no declaration ({type(error).__name__}: {error})",
            )
    return SlotHeld(let_go, _positive(width, out))


def _positive(width: object, out: TextIO) -> int | None:
    """Pass the parallel count the canonical source answered if it is a positive integer, otherwise announce one line and return None (a check of the entry's shape, not a judgment of the number)."""
    if width is None:
        return None
    if isinstance(width, int) and not isinstance(width, bool) and width >= 1:
        return width
    _named(out, f"the answer of the slot canonical process_width is not a positive integer ({width!r}), so running with the default parallel count of a machine with no declaration")
    return None
