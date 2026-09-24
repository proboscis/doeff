"""今の gate.full(1 本の文字列)から、提案の 4 段の宣言を機械的に組み立てる(設計段の最小実験)。"""
import json, sys, tomllib
src = sys.argv[1]
cfg = tomllib.load(open(src, "rb"))
full = cfg["gate"]["full"]
assert isinstance(full, str), type(full)
prefix, sep, rest = full.partition('sh -c "')
assert sep and rest.endswith('"'), full
inner = rest[:-1].split(" && ")
assert len(inner) == 4, inner
sync, root, packages, rust = inner
assert sync.endswith("make sync") and "pytest" in root and "make test-packages" in packages and "make test-rust" in rust, inner
def stage(name, cmds, blocks=False):
    run = prefix + 'sh -c "' + " && ".join(cmds) + '"'
    s = "  { name = %s, run = %s" % (json.dumps(name), json.dumps(run, ensure_ascii=False))
    return s + (", blocks_rest = true }" if blocks else " }")
lines = ["full = ["]
lines.append(stage("build", [sync], True) + ",")
lines.append(stage("root", [sync, root]) + ",")
lines.append(stage("packages", [sync, packages]) + ",")
lines.append(stage("rust", [sync, rust]))
lines.append("]")
new_full = "\n".join(lines)
text = open(src, encoding="utf-8").read()
old_line = [l for l in text.splitlines() if l.startswith("full = ")]
assert len(old_line) == 1
print(text.replace(old_line[0], new_full))
