"""局所の require(関数の中の require)で入った macro を変えた時、使う側が作り直されるかを、uv.lock の版の Hy の使い捨ての環境で測る。
検の file の補助(_run_on_pypi_hy・_edit)を path で読んで使う(検の file は書き換えない)。"""
import importlib.util
import sys
import tempfile
from pathlib import Path

TEST = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("guard_test_helpers", TEST)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)

with tempfile.TemporaryDirectory(dir=sys.argv[2]) as tmp:
    root = Path(tmp)
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "macros.hy").write_text("(defmacro answer [] 11)\n")
    (package / "user.hy").write_text("(defn f [] (require pkg.macros [answer]) (answer))\n(setv value (f))\n")
    first = helpers._run_on_pypi_hy(root, "before-hy")
    helpers._edit(package / "macros.hy", "(defmacro answer [] 12)\n")
    second = helpers._run_on_pypi_hy(root, "before-hy")
    third = helpers._run_on_pypi_hy(root, "before-hy")
    print("local-require", first, second, third, "hy", helpers._locked_hy_version())
