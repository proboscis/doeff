"""盲検 A の再現: KW を _compile_hy で読み、派生の項の数と、その塊の body facts の rewraps を出す(1 process 1 回)。"""
import pickle, sys
from doeff_effect_analyzer import program_effects as pe
KW = """
(defn clause [effect wrap]
  (setv prog effect.program)
  (setv prog (wrap prog :times 3))
  prog)
"""
print("analyzer:", pe.__file__)
read = pe._compile_hy(KW, "/src/k.hy", "k")
directory = pe._hy_cache_dir()
place = pe._hy_cache_places(KW, "k").derived
entries = sorted(directory.glob("*/*.pickle"))
print("derived place:", place.name[:12], "entries:", len(entries), "returned:", type(read).__name__)
for path in sorted(place.glob("*.pickle")):
    with path.open("rb") as handle:
        pickle.load(handle); cached = pickle.load(handle)
    print("rewraps in this reader's entry:", [{k: len(v) for k, v in facts.rewraps.items()} for raw in cached.chunks for _, facts in pickle.loads(raw).body_facts])
