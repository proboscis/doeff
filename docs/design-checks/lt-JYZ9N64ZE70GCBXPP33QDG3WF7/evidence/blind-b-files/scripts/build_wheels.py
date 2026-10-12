"""venv の site-packages の hy 1.3.1 と funcparserlib 1.0.1 から、使い捨ての環境へ入れる wheel を作る(手元の代役)。

使い方: python build_wheels.py <site-packages> <出力の dir> <variant>
variant:
  plain        — hy をそのまま(代役の作り方の確かめ)
  local-state  — HyASTCompiler.local_state が new_local_state を呼ばず、同じ dict を直に積む(Hy の振る舞いは同じ・
                 new_local_state の名は残る — __init__ が呼ぶ)
"""

import base64
import hashlib
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

SITE = Path(sys.argv[1])
OUT = Path(sys.argv[2])
VARIANT = sys.argv[3]

LOCAL_STATE_BEFORE = """    @contextmanager
    def local_state(self):
        self.new_local_state()
"""
LOCAL_STATE_AFTER = """    @contextmanager
    def local_state(self):
        self.local_state_stack.append(dict(macros = {}))
"""


def _hash(data: bytes) -> str:
    """RECORD の行の hash の欄(wheel の形の決まり)を作るため。"""
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _files(top: Path) -> tuple[tuple[str, Path], ...]:
    """wheel に入れる file(site-packages からの相対の名と path)を、__pycache__ を除いて挙げるため。"""
    paths = (top,) if top.is_file() else tuple(path for path in top.rglob("*") if path.is_file())
    return tuple(
        sorted((path.relative_to(SITE).as_posix(), path) for path in paths if "__pycache__" not in path.parts)
    )


def _wheel(name: str, version: str, package: str, dist_files: tuple[str, ...], edit: Callable[[str, bytes], bytes]) -> Path:
    """site-packages に入っている 1 つの distribution を、中身を edit で替えた py3-none-any の wheel に組み直すため。"""
    dist = f"{name}-{version}.dist-info"
    target = OUT / f"{name}-{version}-py3-none-any.whl"
    entries = (
        *((arcname, edit(arcname, path.read_bytes())) for arcname, path in _files(SITE / package)),
        *((arcname, path.read_bytes()) for extra in dist_files for arcname, path in _files(SITE / dist / extra)),
    )
    record = "".join(f"{arcname},{_hash(data)},{len(data)}\n" for arcname, data in entries) + f"{dist}/RECORD,,\n"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as wheel:
        for arcname, data in entries:
            wheel.writestr(arcname, data)
        wheel.writestr(f"{dist}/RECORD", record)
    return target


def _edit_hy(arcname: str, data: bytes) -> bytes:
    """variant の Hy の内部の変更(局所の状態の積み方)を compiler.py に入れるため。"""
    if VARIANT == "local-state" and arcname == "hy/compiler.py":
        text = data.decode()
        assert text.count(LOCAL_STATE_BEFORE) == 1, "local_state の形が想定と違う"
        return text.replace(LOCAL_STATE_BEFORE, LOCAL_STATE_AFTER).encode()
    return data


def _unchanged(_arcname: str, data: bytes) -> bytes:
    """funcparserlib は替えずに入れるため。"""
    return data


OUT.mkdir(parents=True, exist_ok=True)
print(_wheel("hy", "1.3.1", "hy", ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "licenses"), _edit_hy))
print(_wheel("funcparserlib", "1.0.1", "funcparserlib", ("METADATA", "WHEEL", "LICENSE"), _unchanged))
