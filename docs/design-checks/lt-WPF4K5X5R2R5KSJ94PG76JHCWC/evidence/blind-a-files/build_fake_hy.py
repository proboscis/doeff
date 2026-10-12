"""Hy 1.3.1 の入った site-packages から、hy.macros / hy.compiler の inspect.stack の読みを [n].frame に替えた
仮の版 1.3.1+blindframe の wheel を作る(盲検の模擬 — PyPI に無い版)。"""
import base64, hashlib, sys, zipfile
from pathlib import Path

site = Path(sys.argv[1])
out = Path(sys.argv[2])
version = "1.3.1+blindframe"
edits = {
    "hy/macros.py": [("inspect.stack()[1][0]", "inspect.stack()[1].frame", 3)],
    "hy/compiler.py": [("inspect.stack(0)[n + 1][0]", "inspect.stack(0)[n + 1].frame", 1),
                       ("inspect.stack()[1][0]", "inspect.stack()[1].frame", 1)],
}
files = {}
for p in sorted((site / "hy").rglob("*")):
    if p.is_file() and "__pycache__" not in p.parts:
        rel = p.relative_to(site).as_posix()
        data = p.read_bytes()
        for old, new, count in edits.get(rel, []):
            text = data.decode()
            assert text.count(old) == count, (rel, old, text.count(old))
            data = text.replace(old, new).encode()
        files[rel] = data
info = f"hy-{version}.dist-info"
meta = (site / "hy-1.3.1.dist-info" / "METADATA").read_text().replace("Version: 1.3.1", f"Version: {version}", 1)
files[f"{info}/METADATA"] = meta.encode()
files[f"{info}/WHEEL"] = (site / "hy-1.3.1.dist-info" / "WHEEL").read_bytes()
files[f"{info}/top_level.txt"] = b"hy\n"
record = []
for rel, data in files.items():
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    record.append(f"{rel},sha256={digest},{len(data)}")
record.append(f"{info}/RECORD,,")
files[f"{info}/RECORD"] = ("\n".join(record) + "\n").encode()
wheel = out / f"hy-{version}-py3-none-any.whl"
with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as z:
    for rel, data in files.items():
        z.writestr(rel, data)
print(wheel)
