"""S5: 同じ source を、空の置き場で 6 つの process が同時に外す。各 process は始めの合図を待ってから _compile_hy を呼び、
展開の回数を出す。全部が終わった後、展開の項と派生の項が 1 つずつで読める事を確かめる。"""
import os, sys, time
from multiprocessing import get_context
SOURCE = "(require doeff-hy.macros [defk <-])\n(setv answer 42)\n" + "".join(f"(setv v{i} {i})\n" for i in range(400))

def one(start: float) -> tuple[int, str]:
    from doeff_effect_analyzer import hy_expansion, program_effects as pe
    counted = []
    real = hy_expansion._expand_hy
    hy_expansion._expand_hy = lambda s, f, m: counted.append(m) or real(s, f, m)
    while time.time() < start:
        time.sleep(0.001)
    tree = pe._compile_hy(SOURCE, "/src/c.hy", "c")
    return len(counted), type(tree).__name__

if __name__ == "__main__":
    start = time.time() + 3.0
    with get_context("spawn").Pool(6) as pool:
        results = pool.map(one, [start] * 6)
    print("per process (expansions, returned):", results)
    from doeff_effect_analyzer import hy_expansion, program_effects as pe
    directory = pe._hy_cache_dir()
    derived = sorted(directory.glob("*/*.pickle")); expanded = sorted((directory / "expansions").glob("*/*.pickle"))
    leftovers = sorted(str(p.relative_to(directory)) for p in directory.rglob("*.tmp"))
    print("derived entries:", len(derived), "expansion entries:", len(expanded), "temporary files left:", leftovers)
    print("expansion entry readable:", all(hy_expansion._read_expansion(p) is not None for p in expanded))
    print("derived entry readable:", all(pe._read_cached_tree(p) is not None for p in derived))
