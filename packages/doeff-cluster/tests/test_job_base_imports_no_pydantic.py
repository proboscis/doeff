"""job の土台の import の閉包に pydantic が無い事の検(card ki-b7701d5fc0dd・親 card ki-2e0dce8d1ebb)。

job の土台(shared_handlers・detached・runtime_identity・supplied_values)は defwire の解き手 doeff_hy._wire を読む。wire.hy が自前の
解き手(doeff_hy/_wire_codec.py)へ替わったので、土台を読み込む process は pydantic(import と組みで約 15 MB)を読まない。新しい
import がその閉包へ pydantic を戻したら、ここが赤になる(どの module が読んだかを名指す)。
"""

import subprocess
import sys
import textwrap

#: job の土台の module(どの job も読み込む)。
JOB_BASE = (
    "doeff_cluster.shared.protocol.shared_handlers",
    "doeff_cluster.shared.protocol.detached",
    "doeff_cluster.shared.core.runtime_identity",
    "doeff_cluster.shared.protocol.supplied_values",
)


def test_the_job_base_imports_no_pydantic() -> None:
    script = textwrap.dedent(
        """
        import importlib, sys, traceback
        import doeff_hy

        class Watch:
            # pydantic を最初に読もうとした所を名指すため(import の列の .hy と .py の file)。
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in ("pydantic", "pydantic_core"):
                    frames = [f"{f.filename}:{f.lineno}" for f in traceback.extract_stack() if f.filename.endswith((".hy", ".py"))]
                    print("pydantic imported via:", *frames[-8:], sep="\\n  ", file=sys.stderr)
                    raise ImportError("pydantic in the job base closure")
                return None

        sys.meta_path.insert(0, Watch())
        for name in sys.argv[1:]:
            importlib.import_module(name)
        loaded = sorted(name for name in sys.modules if name.split(".")[0] in ("pydantic", "pydantic_core"))
        assert not loaded, loaded
        print("ok")
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", script, *JOB_BASE], capture_output=True, text=True, timeout=300, check=False
    )
    assert done.returncode == 0, done.stderr[-4000:]
    assert done.stdout.strip() == "ok"
