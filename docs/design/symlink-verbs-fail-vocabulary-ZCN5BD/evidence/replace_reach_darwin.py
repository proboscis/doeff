"""`ensure-symlink-outcome` を通した時、os.replace の except 枝に届く形が在るかを Darwin で数える。

c-3JYBNJMC2RZTM1S8V43939MP42 が pod(Linux)で測った「失敗 6 形の合計到達 = 0」を、
同じ計器で会社 Mac でも確かめる(到達 0 は lstat が先に当たるコードの順序から出るので
宿に依らないはずだが、Darwin の実測が手元に無かった)。
"""
import os
import platform
import sys
import tempfile

import hy  # noqa: F401
from doeff_agents.sessionhost import substrate

real_replace = os.replace
reach = {"n": 0}


def counting_replace(src, dst, **kw):
    reach["n"] += 1
    return real_replace(src, dst, **kw)


def call(link, target):
    reach["n"] = 0
    os.replace = counting_replace
    try:
        value = f"値={substrate.ensure_symlink_outcome(link, target)}"
    except OSError as error:
        value = f"raise={type(error).__name__} errno={error.errno}"
    finally:
        os.replace = real_replace
    return reach["n"], value


base = os.path.realpath(tempfile.mkdtemp(prefix="replace-reach-"))
canon = os.path.join(base, "canon")
os.makedirs(canon)
other = os.path.join(base, "canon2")
os.makedirs(other)

print(f"# 機体 {platform.node()} / {platform.system()} {platform.release()} / python {sys.version.split()[0]}")
print(f"# 検体 = 本線 HEAD の substrate.hy ensure-symlink-outcome(動詞を通して撃つ)")
print()

rows = []


def shape(label, make_link):
    link = os.path.join(base, f"L-{label}")
    restore = make_link(link)
    n, value = call(link, canon)
    if restore:
        restore()
    rows.append((label, n, value))


shape("実体 dir(空)", lambda p: os.makedirs(p) or None)
shape("実体 dir(中身入り)", lambda p: (os.makedirs(p), open(os.path.join(p, "k.md"), "w").write("k")) and None)
shape("実体 file", lambda p: open(p, "w").write("operator の物") and None)


def parent_is_file(p):
    home = p + "-home"
    with open(home, "w") as handle:
        handle.write("operator の物")
    return None


link = os.path.join(base, "P-home")
with open(link, "w") as handle:
    handle.write("operator の物")
n, value = call(os.path.join(link, "skills"), canon)
rows.append(("親の位置に実体 file", n, value))

home = os.path.join(base, "RX-home")
os.makedirs(home)
os.chmod(home, 0o500)
n, value = call(os.path.join(home, "skills"), canon)
os.chmod(home, 0o700)
rows.append(("家が r-x", n, value))

grand = os.path.join(base, "GX-grand")
os.makedirs(grand)
os.chmod(grand, 0o500)
n, value = call(os.path.join(grand, "home", "skills"), canon)
os.chmod(grand, 0o700)
rows.append(("親 dir を作れない", n, value))

fresh = os.path.join(base, "OK-fresh")
n, value = call(fresh, canon)
rows.append(("不在 → 張る", n, value))
n, value = call(fresh, other)
rows.append(("別の先 → 張り替え", n, value))

for label, n, value in rows:
    print(f"  {label:22s} os.replace 到達 {n} 回 | {value}")

failures = [n for label, n, value in rows if "raise=" in value or "occupied" in value]
print()
print(f"-- 失敗 6 形の合計到達 = {sum(failures)} --")
print(f"-- 正常 2 形の合計到達 = {sum(n for label, n, v in rows if 'linked' in v)} --")
