"""venv に入った PyPI の Hy 1.3.1 を元に、Hy の内部を 1 か所だけ替えた失敗ケースの wheel を作る(uv の find-links で入れる)。

使い方: python build_hy_variants.py <site-packages> <出力の dir> <変種>
変種: base(替えない)・get(macroexpand が module の表を .get で引く)・table(_hy_macros の名を替える)・
reader(_hy_reader_macros の名を替える)・gensym(gensym の名の形を替える)・anon(get_anon_var の名の形を替える)
"""

import base64
import hashlib
import re
import sys
import zipfile
from pathlib import Path

GET_OLD = """                next(
                    (mod._hy_macros[fn]
                        for mod in (module, builtins)
                        if fn in getattr(mod, "_hy_macros", ())),
                    None))"""
GET_NEW = """                next(
                    (found
                        for mod in (module, builtins)
                        for found in [getattr(mod, "_hy_macros", {}).get(fn)]
                        if found is not None),
                    None))"""


def patch(variant: str, rel: str, text: str) -> str:
    if variant == "get" and rel == "hy/macros.py":
        assert GET_OLD in text
        return text.replace(GET_OLD, GET_NEW)
    if variant == "table" and rel.endswith((".py", ".hy")):
        return re.sub(r"(?<![A-Za-z0-9_])_hy_macros(?![A-Za-z0-9_])", "_hy_macro_table", text)
    if variant == "reader" and rel.endswith((".py", ".hy")):
        return re.sub(r"(?<![A-Za-z0-9_])_hy_reader_macros(?![A-Za-z0-9_])", "_hy_reader_macro_table", text)
    if variant == "gensym" and rel == "hy/core/util.hy":
        old = '(.format "_hy_gensym_{}_{}" g n)'
        assert old in text
        return text.replace(old, '(.format "_hy_gs{}_{}" n g)')
    if variant == "anon" and rel == "hy/compiler.py":
        old = 'return f"_hy_{base}{name}_{self.anon_var_count}"'
        assert old in text
        return text.replace(old, 'return f"_hy_{base}{self.anon_var_count}{name}"')
    return text


def build(site: Path, out: Path, dist: str, packages: list[str], variant: str) -> Path:
    info = site / f"{dist}.dist-info"
    files: list[tuple[str, bytes]] = []
    for package in packages:
        for p in sorted((site / package).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                rel = p.relative_to(site).as_posix()
                data = p.read_bytes()
                if variant != "base" and p.suffix in (".py", ".hy"):
                    data = patch(variant, rel, data.decode("utf-8")).encode("utf-8")
                files.append((rel, data))
    for name in ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "LICENSE"):
        if (info / name).is_file():
            files.append((f"{dist}.dist-info/{name}", (info / name).read_bytes()))
    if (info / "licenses").is_dir():
        for p in sorted((info / "licenses").rglob("*")):
            if p.is_file():
                files.append((f"{dist}.dist-info/{p.relative_to(info).as_posix()}", p.read_bytes()))
    record = []
    for rel, data in files:
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{rel},sha256={digest},{len(data)}")
    record.append(f"{dist}.dist-info/RECORD,,")
    out.mkdir(parents=True, exist_ok=True)
    wheel = out / f"{dist}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in files:
            z.writestr(rel, data)
        z.writestr(f"{dist}.dist-info/RECORD", "\n".join(record) + "\n")
    return wheel


if __name__ == "__main__":
    site, out, variant = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    print(build(site, out, "hy-1.3.1", ["hy"], variant))
    print(build(site, out, "funcparserlib-1.0.1", ["funcparserlib"], "base"))
