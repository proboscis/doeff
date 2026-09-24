"""E3: 提案の宣言を日次の道具の読み替え(full_stages)に通し、同じ形の 4 段を偽の命令で run_full_stages に掛ける。"""
import sys, tomllib, tempfile, os
sys.path.insert(0, os.path.expanduser("~/dotfiles/agentcli/src"))
from agentcli import land, land_config
decl = tomllib.load(open(sys.argv[1], "rb"))["gate"]
stages, why = land_config.full_stages(decl)
print("full_stages:", "OK" if stages is not None else f"REFUSED {why}")
for s in stages:
    print(f"  stage name={s.name} blocks_rest={s.blocks_rest} run_has_make_sync={'make sync' in s.run} "
          f"pop={'pytest -q' in s.run and 'make test' not in s.run and 'root' or ('make test-packages' in s.run and 'packages') or ('make test-rust' in s.run and 'rust') or '-'}")
def fake(stage_rc):
    return [land_config.FullStage(s.name, f"echo run {s.name}; exit {stage_rc.get(s.name, 0)}", s.blocks_rest, s.layers)
            for s in stages]
for label, rcs in [("root が赤(F3 の形)", {"root": 1}), ("build が赤", {"build": 2})]:
    with tempfile.TemporaryDirectory() as d:
        log = land._Log(os.path.join(d, "run.log"), echo=False)
        gr = land.run_full_stages(fake(rcs), d, log, 60, stop_on_first_failure=False)
        print(f"== {label}: 畳んだ結果 outcome={gr.outcome}")
        for st in gr.stages:
            print(f"   {st.name}: outcome={st.outcome} rc={st.rc} unexecuted={st.unexecuted!r}")
