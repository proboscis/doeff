"""os.replace(仮 symlink → 据わっている物) が形ごとに何の errno を返すかを測る。

扉 2 の直し(`except OSError` を errno で割る)は、この表が機体ごとに違うと成り立たない。
"""
import errno as E
import os
import platform
import sys
import tempfile

base = os.path.realpath(tempfile.mkdtemp(prefix="rename-errno-"))
canon = os.path.join(base, "canon")
os.makedirs(canon)

print(f"# 機体 {platform.node()} / {platform.system()} {platform.release()} / python {sys.version.split()[0]}")

def probe(label, make):
    dest = os.path.join(base, label)
    make(dest)
    staged = os.path.join(base, f".{label}.tmp")
    os.symlink(canon, staged)
    try:
        os.replace(staged, dest)
        kind = "実体 file" if os.path.isfile(dest) else "?"
        print(f"  {label:22s} → 成功(黙って置き換えた)")
    except OSError as error:
        name = E.errorcode.get(error.errno, "?")
        print(f"  {label:22s} → {type(error).__name__} errno={error.errno} {name} ({os.strerror(error.errno)})")
    finally:
        if os.path.islink(staged):
            os.unlink(staged)

print("-- os.replace(仮 symlink, 据わっている物) --")
probe("dir-empty", lambda p: os.makedirs(p))
probe("dir-nonempty", lambda p: (os.makedirs(p), open(os.path.join(p, "k.md"), "w").write("k")))
probe("file-real", lambda p: open(p, "w").write("operator の物"))
probe("symlink-other", lambda p: os.symlink(base, p))

print("-- 参考: os.symlink(…, 据わっている物) --")
for label, make in (
    ("dir-empty", lambda p: os.makedirs(p)),
    ("file-real", lambda p: open(p, "w").write("x")),
    ("symlink-other", lambda p: os.symlink(base, p)),
):
    dest = os.path.join(base, f"s-{label}")
    make(dest)
    try:
        os.symlink(canon, dest)
        print(f"  s-{label:20s} → 成功(あり得ない)")
    except OSError as error:
        name = E.errorcode.get(error.errno, "?")
        print(f"  s-{label:20s} → {type(error).__name__} errno={error.errno} {name}")
