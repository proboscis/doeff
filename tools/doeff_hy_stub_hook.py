"""Hatch build hook that makes the type declarations (.pyi) of a package's Hy modules from the .hy at build time.

The .pyi of a .hy are not committed: a committed .pyi could reach main stale whenever a commit changed a .hy without
regenerating it (no commit hook ran, another .hy's macro changed, the tool changed), and users' strict type checks then read the new names as
Unknown. Every install of the package builds it — the editable build of `uv sync` / `make sync` in this repo and in users that take the package by
path, and the wheel build of users that take it by git — so the hook runs `python -m doeff_hy._static_stub --build` there and the .pyi next to each
.hy is what the tool makes from that .hy. The tool reads the expansion cache, so a build expands only the .hy whose expansion changed.

The hook runs in the isolated build environment, which holds only the third-party modules the expansion imports (build-system.requires: hy,
frozendict, doeff-vm). doeff, doeff-hy and doeff-core-effects are read from this checkout's sources instead of being build requirements: doeff-hy and
doeff depend on doeff-core-effects, whose own build runs this hook, so requiring them would make the build dependencies a cycle.

Each package that uses it links its hatch_build.py here (the same shape as tools/doeff_cargo_backend.py) and names, under
[tool.hatch.build.hooks.custom], the import root (stub-root) and the dirs whose .hy get a .pyi (stub-dirs). An sdist carries the .pyi it made
(tool.hatch.build artifacts), so a build from an extracted sdist — no sibling packages — finds the tool missing and keeps those.
The doeff-cluster worker turns the hook off (STUBS_ENV) for the roots it prepares, which run programs and are never type-checked.
"""

import os
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

#: The variable that turns the hook off (value "off"): the doeff-cluster worker sets it for the uv sync of the roots it runs — they are never
#: type-checked, and expanding every .hy there costs a cold build about 100 seconds and four expansion processes (native_wheel.STUBS_ENV).
STUBS_ENV = "DOEFF_HY_STUBS"

#: The sources read in place of build requirements, relative to the checkout root (the doeff package, the tool and the modules it imports).
SOURCE_PATHS = (
    ".",
    "packages/doeff-hy/src",
    "packages/doeff-core-effects",
    "packages/doeff-hy-pytest/src",
)


class HyStubHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        """Make the package's .pyi before hatchling collects the files, so the editable install and the wheel both see what the .hy declare."""
        if os.environ.get(STUBS_ENV) == "off":
            return
        package = Path(self.root)
        checkout = package.parents[1]
        if not (
            checkout / "packages" / "doeff-hy" / "src" / "doeff_hy" / "_static_stub.py"
        ).is_file():
            return
        root = package / str(self.config["stub-root"])
        dirs = [str(package / str(d)) for d in self.config["stub-dirs"]]
        environment = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(str(checkout / p) for p in SOURCE_PATHS),
            # The sources are the shared checkout's: compiling them must not leave __pycache__ there.
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        command = [
            sys.executable,
            "-m",
            "doeff_hy._static_stub",
            "--build",
            "--root",
            str(root),
            *dirs,
        ]
        done = subprocess.run(command, cwd=package, env=environment, check=False)
        if done.returncode != 0:
            raise RuntimeError(
                f"doeff_hy._static_stub --build failed for {package.name} (exit {done.returncode}); the .hy above did not expand"
            )
