"""Which extra to install when an import outside the core fails — the one table.

``pip install doeff`` brings the core four (doeff, doeff-vm, doeff-core-effects,
frozendict). The extra ``hy`` adds Hy (doeff-hy) for the effects and handlers
written in Hy, ``cache``, ``doeff run --hy`` and ``.hy`` programs; the extra
``http`` adds Hy and httpx for the HTTP client handler
(tests/test_core_install_extras.py). When an import a feature needs fails because
its extra is missing, the error keeps the missing module's name and ends with the
line that installs the extra: ``pip install "doeff[hy]"``.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

Extra = Literal["hy", "http"]

# doeff-hy brings hy and hyrule.
_HY_MODULES = frozenset({"doeff_hy", "hy", "hyrule"})


def _installs(extra: Extra) -> frozenset[str]:
    """The top-level modules that ``extra`` installs."""
    match extra:
        case "hy":
            return _HY_MODULES
        case "http":
            return _HY_MODULES | {"httpx"}


class MissingExtraError(ModuleNotFoundError):
    """A module that ``extra`` installs is missing; the message names the extra to install."""

    def __init__(self, missing: str, extra: Extra, reason: str) -> None:
        super().__init__(
            f"No module named {missing!r} — {reason}. "
            f'Install the extra {extra}: pip install "doeff[{extra}]"',
            name=missing,
        )
        self.extra: Extra = extra


@contextmanager
def extra_needed(extra: Extra, reason: str) -> Iterator[None]:
    """Turn a failed import of a module that ``extra`` installs into MissingExtraError.

    ``reason`` says why the feature needs the module ("the HTTP client handler sends
    with httpx"). Any other failed import passes through unchanged. An inner
    MissingExtraError is named again with this ``extra``, so the caller that knows the
    feature (the HTTP handler) decides the extra over the one that only knows the module
    (a ``.hy`` submodule needs Hy).
    """
    try:
        yield
    except ModuleNotFoundError as error:
        missing = error.name or ""
        if missing.partition(".")[0] not in _installs(extra):
            raise
        raise MissingExtraError(missing, extra, reason) from error
