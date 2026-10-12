"""venv に入った PyPI の Hy 1.3.1 を元に、Hy の内部を 1 か所だけ替えた失敗ケースの wheel を作る(uv の find-links で入れる)。

使い方: uv run --no-project python -I build_hy_variants.py <site-packages> <出力の dir> <変種> [版の名](版の名 = uv.lock を上げた便の代役)
変種: base(替えない)・get(macroexpand が module の表を .get で引く)・table(_hy_macros の名を替える)・
reader(_hy_reader_macros の名を替える)・gensym(gensym の名の形を替える)・anon(get_anon_var の名の形を替える)・
tryget(macroexpand が表を `名 in 表` で確かめず、`表[名]` の KeyError で無いと知る)・export(_hy_export_macros の名を替える)
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


TRYGET_NEW = """                next(
                    (found
                        for mod in (module, builtins)
                        for found in [_hy_table_entry(mod, fn)]
                        if found is not None),
                    None))"""
TRYGET_HELPER = """

def _hy_table_entry(mod, fn):
    try:
        return getattr(mod, "_hy_macros", {})[fn]
    except KeyError:
        return None
"""


def patch(variant: str, rel: str, text: str) -> str:
    if variant == "get" and rel == "hy/macros.py":
        assert GET_OLD in text
        return text.replace(GET_OLD, GET_NEW)
    if variant == "tryget" and rel == "hy/macros.py":
        assert GET_OLD in text
        return text.replace(GET_OLD, TRYGET_NEW) + TRYGET_HELPER
    if variant == "localstate" and rel == "hy/compiler.py":
        # 盲検 B の代役と同じ替え方(blind-b-files/scripts/build_wheels.py の local-state): Hy の振る舞いは同じで、compiler の局所の
        # 状態を new_local_state を通さずに積む。
        old = "    def local_state(self):\n        self.new_local_state()\n"
        assert old in text
        return text.replace(old, "    def local_state(self):\n        self.local_state_stack.append(dict(macros = {}))\n")
    if variant == "export" and rel.endswith((".py", ".hy")):
        return re.sub(r"(?<![A-Za-z0-9_])_hy_export_macros(?![A-Za-z0-9_])", "_hy_export_macro_names", text)
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


def build(
    site: Path, out: Path, dist: str, packages: list[str], variant: str, version: str | None = None
) -> Path:
    """version を渡すと、版の名(dist-info・METADATA・hy.__version__)を替えた wheel にする(uv.lock を上げた便の代役)。"""
    info = site / f"{dist}.dist-info"
    old_version = dist.rsplit("-", 1)[1]
    named = dist if version is None else f"{dist.rsplit('-', 1)[0]}-{version}"
    files: list[tuple[str, bytes]] = []
    for package in packages:
        for p in sorted((site / package).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                rel = p.relative_to(site).as_posix()
                data = p.read_bytes()
                if variant != "base" and p.suffix in (".py", ".hy"):
                    data = patch(variant, rel, data.decode("utf-8")).encode("utf-8")
                if version is not None and rel == "hy/__init__.py":
                    old = f"__version__ = '{old_version}'"
                    assert old in data.decode("utf-8")
                    data = data.decode("utf-8").replace(old, f"__version__ = '{version}'").encode("utf-8")
                files.append((rel, data))
    for name in ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "LICENSE"):
        if (info / name).is_file():
            data = (info / name).read_bytes()
            if version is not None and name == "METADATA":
                data = data.replace(f"Version: {old_version}".encode(), f"Version: {version}".encode(), 1)
            files.append((f"{named}.dist-info/{name}", data))
    if (info / "licenses").is_dir():
        for p in sorted((info / "licenses").rglob("*")):
            if p.is_file():
                files.append((f"{named}.dist-info/{p.relative_to(info).as_posix()}", p.read_bytes()))
    dist = named
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
    version = sys.argv[4] if len(sys.argv) > 4 else None
    print(build(site, out, "hy-1.3.1", ["hy"], variant, version))
    print(build(site, out, "funcparserlib-1.0.1", ["funcparserlib"], "base"))
