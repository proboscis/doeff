"""The hand-written .pyi left next to a .hy are the fixed few the tool cannot make.

The build hook (tools/doeff_hy_stub_hook.py) makes the .pyi of every .hy under each package's stub-dirs, but leaves a hand-written one (no tool mark on
line 1) alone. A hand-written .pyi lags its .hy: service_build.pyi missed peak_mib and settle_mib, and the job stubs missed filled, while the .hy had them.
So the .hy contracts carry the types, the tool renders them, and a hand-written .pyi stays only where the tool cannot make the shape — each says why on
line 1, and the table below names them all. A new hand-written .pyi turns this red: put the types in the .hy contracts (or teach the tool the shape) instead.
"""

import tomllib
from pathlib import Path

from doeff_hy._static_stub import generated

CHECKOUT = Path(__file__).resolve().parents[3]

#: The packages whose build makes the .pyi of their .hy ([tool.hatch.build.hooks.custom] stub-dirs).
STUB_PACKAGES = ("doeff-cluster", "doeff-core-effects", "doeff-hy", "doeff-records")

#: Line 1 of a kept hand-written .pyi starts with this, followed by what the tool cannot make.
KEPT_HEAD = "# Hand-written stub kept: "

#: The hand-written .pyi kept next to a .hy (relative to the checkout) — the reason is on line 1 of each.
KEPT = frozenset(
    {
        # ByteLog / DeltaStore are ABCs whose membership is structural (MethodShape.__subclasshook__); the tool renders nominal classes.
        "packages/doeff-cluster/src/doeff_cluster/_coordinator/protocol/store.pyi",
        # The recursive JSON aliases differ from the runtime one-level FrozenJson union; FrozenMap's V is covariant with a default.
        "packages/doeff-hy/src/doeff_hy/_frozen.pyi",
        # parse / parse-json answer the type passed in (type[_W] -> _W | Malformed); a defk cannot say that.
        "packages/doeff-hy/src/doeff_hy/_wire.pyi",
        # A macro module: only the runtime helpers macro expansions call are declared.
        "packages/doeff-hy/src/doeff_hy/macros.pyi",
    }
)


def _stub_dirs(package: str) -> list[Path]:
    """The dirs whose .hy get a .pyi from the package's build hook."""
    root = CHECKOUT / "packages" / package
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return [root / d for d in config["tool"]["hatch"]["build"]["hooks"]["custom"]["stub-dirs"]]


def _hand_written() -> frozenset[str]:
    """Every .pyi next to a .hy under the stub-dirs that the tool did not make."""
    return frozenset(
        str(source.with_suffix(".pyi").relative_to(CHECKOUT))
        for package in STUB_PACKAGES
        for directory in _stub_dirs(package)
        for source in directory.rglob("*.hy")
        if source.with_suffix(".pyi").is_file() and not generated(source.with_suffix(".pyi"))
    )


def test_the_hand_written_stubs_are_only_the_kept_ones() -> None:
    assert sorted(_hand_written()) == sorted(KEPT)


def test_each_kept_stub_says_why_on_its_first_line() -> None:
    heads = {path: (CHECKOUT / path).read_text(encoding="utf-8").partition("\n")[0] for path in KEPT}
    assert [path for path, head in heads.items() if not head.startswith(KEPT_HEAD) or head == KEPT_HEAD.rstrip()] == []
