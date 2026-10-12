import sys, types, traceback
import hy, hy.compiler, hy.errors
import doeff_hy  # noqa: F401
path = sys.argv[1]
src = open(path, encoding="utf-8").read()
module = types.ModuleType("bad_val")
try:
    hy.compiler.hy_compile(hy.read_many(src, filename=path), module, filename=path, source=src)
    print("NO ERROR")
except Exception as e:
    print("TYPE:", type(e).__module__ + "." + type(e).__name__)
    print("is SyntaxError:", isinstance(e, SyntaxError))
    print("filename/lineno:", getattr(e, "filename", None), getattr(e, "lineno", None))
    print("MSG:", str(e).strip().splitlines()[-1][:200])
    frames = [f"{fs.filename.rsplit('/',1)[-1]}:{fs.name}" for fs in traceback.extract_tb(e.__traceback__)]
    print("TB tail:", frames[-3:])
