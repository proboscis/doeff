"""doeff-records の import の閉包に pydantic が無い事の検(card ki-138189339298・親 card ki-2e0dce8d1ebb)。

表の行の型の読み書き(_typed.hy)は、pydantic の TypeAdapter から doeff_hy.wire_codec の行の解き手(row_codec_of)へ替わった。
記録を読み書きする process は pydantic(import と組みで約 15 MB)を読まない。新しい import が doeff-records のどこかの module の閉包へ
pydantic を戻したら、ここが赤になる(どの module が読んだかを名指す)。
"""

import pkgutil
import subprocess
import sys
import textwrap

import doeff_records


def test_every_records_module_imports_no_pydantic() -> None:
    modules = sorted(info.name for info in pkgutil.iter_modules(doeff_records.__path__, "doeff_records."))
    assert "doeff_records._typed" in modules, modules
    script = textwrap.dedent(
        """
        import importlib, sys, traceback
        import doeff_hy

        asked = []

        class Watch:
            # pydantic を探した所を控え、読み込まれた時にだけ名指すため(yarl のように在るかを探すだけの所は読み込まない)。
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in ("pydantic", "pydantic_core"):
                    asked.append([f"{f.filename}:{f.lineno}" for f in traceback.extract_stack() if f.filename.endswith((".hy", ".py"))][-8:])
                return None

        sys.meta_path.insert(0, Watch())
        for name in sys.argv[1:]:
            importlib.import_module(name)
        loaded = sorted(name for name in sys.modules if name.split(".")[0] in ("pydantic", "pydantic_core"))
        for frames in asked if loaded else []:
            print("pydantic asked via:", *frames, sep="\\n  ", file=sys.stderr)
        assert not loaded, loaded
        print("ok")
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", script, *modules], capture_output=True, text=True, timeout=300, check=False
    )
    assert done.returncode == 0, done.stderr[-4000:]
    assert done.stdout.strip() == "ok"
