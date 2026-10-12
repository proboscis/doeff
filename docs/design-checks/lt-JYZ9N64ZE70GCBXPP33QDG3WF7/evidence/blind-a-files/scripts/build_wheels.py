"""venv の site-packages の Hy 1.3.1 と funcparserlib から wheel を作る。--rename OLD=NEW で Hy の source の文字列を置き換える
(将来の Hy が module の macro の表の名を替えた版の代役)。"""
import base64, hashlib, sys, zipfile
from pathlib import Path

site = Path(sys.argv[1])
out = Path(sys.argv[2])
renames = [tuple(a.split("=", 1)) for a in sys.argv[3:]]
out.mkdir(parents=True, exist_ok=True)
SKIP_META = {"INSTALLER", "REQUESTED", "RECORD", "direct_url.json", "uv_build.json", "uv_cache.json"}


def build(pkg: str, dist: str, wheel_name: str, rename: bool) -> None:
    rows = []
    path = out / wheel_name
    changed = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(arc: str, data: bytes) -> None:
            zf.writestr(arc, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            rows.append(f"{arc},sha256={digest},{len(data)}")
        for f in sorted((site / pkg).rglob("*")):
            if f.is_dir() or "__pycache__" in f.parts:
                continue
            data = f.read_bytes()
            if rename and f.suffix in (".py", ".hy"):
                text = data.decode("utf-8")
                for old, new in renames:
                    if old in text:
                        changed += text.count(old)
                        text = text.replace(old, new)
                data = text.encode("utf-8")
            add(f.relative_to(site).as_posix(), data)
        for f in sorted((site / dist).rglob("*")):
            if f.is_dir() or f.name in SKIP_META:
                continue
            add(f.relative_to(site).as_posix(), f.read_bytes())
        rows.append(f"{dist}/RECORD,,")
        zf.writestr(f"{dist}/RECORD", "\n".join(rows) + "\n")
    print(f"{path.name}: replaced {changed} occurrence(s)")


build("hy", "hy-1.3.1.dist-info", "hy-1.3.1-py3-none-any.whl", True)
build("funcparserlib", "funcparserlib-1.0.1.dist-info", "funcparserlib-1.0.1-py2.py3-none-any.whl", False)
