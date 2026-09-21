"""本線 origin/main の **現物の** ensure-symlink-outcome を 2 process から同拍で叩く。

前回(会社 Mac)は 8 行を 1:1 で写した模型での測定だった。ここでは hy の module を
そのまま import して、出荷されている関数そのものを測る。
"""
import multiprocessing as mp, os, shutil, sys, tempfile, time

import hy  # noqa: F401  (hy の import hook を立てる)
from doeff_agents.sessionhost.substrate import ensure_symlink_outcome


def _one(args):
    barrier_dir, link, target = args
    # 粗い barrier: 全員が file を置いてから、2 人揃うまで回る
    me = os.path.join(barrier_dir, str(os.getpid()))
    open(me, "w").close()
    while len(os.listdir(barrier_dir)) < 2:
        pass
    try:
        return ("ok", ensure_symlink_outcome(link, target))
    except Exception as exc:  # noqa: BLE001 — 落ち方そのものが測定対象
        return ("raise", type(exc).__name__)


def main() -> int:
    root = tempfile.mkdtemp(prefix="race-real-fn-")
    target = os.path.join(root, "source")
    os.makedirs(target, exist_ok=True)
    rounds = 200
    tally = {}
    for i in range(rounds):
        home = os.path.join(root, f"home{i}")
        os.makedirs(home, exist_ok=True)
        link = os.path.join(home, "skills")
        bdir = os.path.join(root, f"b{i}")
        os.makedirs(bdir, exist_ok=True)
        with mp.Pool(2) as pool:
            for kind, val in pool.map(_one, [(bdir, link, target)] * 2):
                tally[f"{kind}:{val}"] = tally.get(f"{kind}:{val}", 0) + 1
    print(f"A 空の家へ 2 席が同拍で張る × {rounds} 回(現物の関数): {tally}")
    shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    mp.set_start_method("fork")
    sys.exit(main())
