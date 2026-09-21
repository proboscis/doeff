"""共有の家への symlink の据え付けの競りを測る(計画段の証拠・2026-09-21T10:0xZ・会社 Mac CA-20038667)。

substrate.hy ensure-symlink-outcome(origin/main f3d8b9a1 の 8 行)を Python に 1:1 で写し、
A: 空の家へ 2 席が同拍で張る(barrier で揃える)× 200 回 — 例外の数。
B: 1 席が別の先へ張り替え続ける間、もう 1 席が link を読み続ける — 「根が無い瞬間」を観測した数。
背景 = 依頼者 c-3JYBNJMC2RZTM1S8V43939MP42 の pod の観測(郵便 lt-6P2WV98TDN84JQHC2SCF04XMN8): 家は資格ごとで
1 つの家を 3 会話が共有し、手番の最中に新しい家が鋳られた。同拍の据え付けは実在する形。
使い方: python3 symlink_install_race.py(cwd の下に一時 dir を作る)。"""
import os, sys, tempfile, multiprocessing as mp, time


UNCHANGED, LINKED, OCCUPIED = "unchanged", "linked", "occupied-by-real-entity"

def ensure_symlink_outcome(link, target):  # 逐語の写し
    if os.path.islink(link):
        if os.readlink(link) == target:
            return UNCHANGED
        os.unlink(link)
        os.symlink(target, link)
        return LINKED
    if os.path.exists(link):
        return OCCUPIED
    parent = os.path.dirname(link)
    if parent:
        os.makedirs(parent, exist_ok=True)
    os.symlink(target, link)
    return LINKED

def worker_a(barrier, link, target, q):
    barrier.wait()
    try:
        q.put(("ok", ensure_symlink_outcome(link, target)))
    except Exception as e:
        q.put(("exc", type(e).__name__))

def run_a(rounds=200):
    exc = {}; ok = 0
    for _ in range(rounds):
        d = tempfile.mkdtemp(prefix="home-", dir=os.getcwd())
        link = os.path.join(d, "skills"); target = os.path.join(d, "src"); os.mkdir(target)
        barrier = mp.Barrier(2); q = mp.Queue()
        ps = [mp.Process(target=worker_a, args=(barrier, link, target, q)) for _ in range(2)]
        [p.start() for p in ps]; [p.join() for p in ps]
        for _ in range(2):
            kind, val = q.get()
            if kind == "exc": exc[val] = exc.get(val, 0) + 1
            else: ok += 1
    return ok, exc

def relinker(link, t1, t2, n, done):
    for i in range(n):
        ensure_symlink_outcome(link, t1 if i % 2 else t2)
    done.set()

def run_b(n=20000):
    d = tempfile.mkdtemp(prefix="home-", dir=os.getcwd())
    link = os.path.join(d, "skills"); t1 = os.path.join(d, "s1"); t2 = os.path.join(d, "s2")
    os.mkdir(t1); os.mkdir(t2); ensure_symlink_outcome(link, t1)
    done = mp.Event(); p = mp.Process(target=relinker, args=(link, t1, t2, n, done)); p.start()
    reads = missing = 0
    while not done.is_set():
        reads += 1
        if not os.path.lexists(link): missing += 1
    p.join()
    return reads, missing

if __name__ == "__main__":
    mp.set_start_method("fork")
    ok, exc = run_a()
    print(f"A 空の家へ 2 席が同拍で張る × 200 回: 成功 {ok} 件 / 例外 {sum(exc.values())} 件 {exc}")
    reads, missing = run_b()
    print(f"B 張り替え 20000 回の間に link を {reads} 回読んだ: 根が無い瞬間 {missing} 回")
