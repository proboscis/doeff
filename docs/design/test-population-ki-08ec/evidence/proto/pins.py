"""E4: 固定のテストの試作(設計段・本実装ではない)。実装では tests/test_daily_test_population.py と ADR の deftest へ移す。"""
import json, os, re, subprocess, sys, tomllib, tempfile
from pathlib import Path

TEST_NAME = re.compile(r"(^|/)(test_[^/]*|[^/]*_test)\.py$")
POPULATION_CMDS = {"root": lambda r: "pytest" in r and "make test-packages" not in r and "make test-rust" not in r,
                   "packages": lambda r: "make test-packages" in r,
                   "rust": lambda r: "make test-rust" in r}
EXCLUDED = {  # path または dir の接頭辞(末尾 /) → 理由
    "packages/doeff-agents/conformance/": "tmux / herdr の実 pane が要る黒箱の交代ゲート。日次の宿での可否が未測定(後続の card)",
    "docs/design/": "設計の検証の模型と実験(その時点の記録で、日次の母集団ではない)",
    "ide-plugins/pycharm/test_program_detection.py": "PyCharm plugin の検出の検体(テスト関数を持たない)",
    "packages/doeff-agentic/examples/": "例示の script(__main__ で走らせる)",
    "packages/doeff-test-target/src/doeff_test_target/effects/test_effects.py": "fixture の module 名(テストではない)",
    "tools/test_python_versions.py": "Python の版を回す道具の script",
}

def pin1(decl_path):
    full = tomllib.load(open(decl_path, "rb"))["gate"]["full"]
    errs = []
    if not isinstance(full, list):
        return [f"gate.full が処理ステージの列でない({type(full).__name__})— 1 つの母集団の赤が後ろの母集団を未実行にする"]
    names = [s["name"] for s in full]
    if len(set(names)) != len(names): errs.append(f"名前が重複: {names}")
    if not full or full[0]["name"] != "build" or full[0].get("blocks_rest") is not True:
        errs.append("先頭が blocks_rest = true の build でない")
    for s in full[1:]:
        if s.get("blocks_rest"): errs.append(f"{s['name']} が blocks_rest を立てている(後ろの母集団を止める)")
    for pop, hit in POPULATION_CMDS.items():
        owners = [s["name"] for s in full[1:] if hit(s["run"])]
        if len(owners) != 1: errs.append(f"母集団 {pop} を呼ぶ処理ステージが {owners}(ちょうど 1 つでない)")
    for s in full[1:]:
        pops = [p for p, hit in POPULATION_CMDS.items() if hit(s["run"])]
        if len(pops) > 1: errs.append(f"{s['name']} が 2 つ以上の母集団 {pops} を呼ぶ")
        if pops and "make sync" not in s["run"].split(pops and {"root": "pytest", "packages": "make test-packages", "rust": "make test-rust"}[pops[0]])[0]:
            errs.append(f"{s['name']} が母集団の命令の前に make sync を持たない(自己完結でない)")
    return errs

def expected_packages(repo):
    out = set()
    for tdir in sorted(Path(repo, "packages").glob("*/tests")):
        if any(p for p in tdir.rglob("test_*.py") if "fixtures" not in p.relative_to(tdir).parts):
            out.add(tdir.parent.name)
    return out

def pin2(repo, makefile):
    repo = Path(repo).resolve()
    exp = sorted(expected_packages(repo))
    with tempfile.TemporaryDirectory() as d:
        rec = Path(d, "calls.jsonl"); fake = Path(d, "fake")
        fake.write_text("#!/usr/bin/env python3\nimport json,os,sys\n"
                        f"open({str(rec)!r},'a').write(json.dumps({{'cwd':os.getcwd(),'argv':sys.argv[1:]}})+'\\n')\n"
                        f"sys.exit(1 if any(a.rstrip('/')=='packages/{exp[0]}/tests' or a.rstrip('/')=='tests' and os.path.basename(os.getcwd())=={exp[0]!r} for a in sys.argv[1:]) else 0)\n")
        fake.chmod(0o755)
        p = subprocess.run(["make", "-s", "-f", makefile, "test-packages", f"PACKAGE_UV_RUN={fake}"],
                           cwd=repo, capture_output=True, text=True, timeout=60)
        calls = [json.loads(l) for l in rec.read_text().splitlines()] if rec.exists() else []
    errs = []
    visited = set()
    for c in calls:
        for a in c["argv"]:
            m = re.fullmatch(r"packages/([^/]+)/tests/?", a)
            if m and Path(c["cwd"]).resolve() == repo: visited.add(m.group(1))
    if visited != set(exp):
        errs.append(f"repo の根から package の tests を渡して訪ねた集合が期待と違う: 欠け {sorted(set(exp)-visited)} 余り {sorted(visited-set(exp))}"
                    f"(呼ばれた回数 {len(calls)}・cwd の例 {calls[0]['cwd'] if calls else '-'})")
    if p.returncode == 0: errs.append(f"最初の package {exp[0]} を赤にしたのに rc 0")
    if f"test-packages failed: {exp[0]}" not in p.stdout + p.stderr and f"failed: {exp[0]}" not in p.stdout + p.stderr:
        errs.append(f"最後に失敗の package {exp[0]} を名指していない")
    return errs

def pin3(repo, files, excluded):
    repo = Path(repo)
    roots = tomllib.load(open(repo / "pyproject.toml", "rb"))["tool"]["pytest"]["ini_options"]["testpaths"]
    pkg_roots = [f"packages/{p}/tests" for p in expected_packages(repo)]
    errs, orphans = [], []
    for f in files:
        if not TEST_NAME.search(f) or "/fixtures/" in f: continue
        if any(f == r or f.startswith(r.rstrip("/") + "/") for r in [*roots, *pkg_roots]): continue
        if any(f == e or (e.endswith("/") and f.startswith(e)) for e in excluded): continue
        orphans.append(f)
    if orphans: errs.append(f"どの母集団にも除外の表にも無い test 名の file {len(orphans)} 本: {orphans[:5]}…")
    stale = [e for e in excluded if not any(f == e or (e.endswith('/') and f.startswith(e)) for f in files)]
    if stale: errs.append(f"除外の表の古い行: {stale}")
    own_ini = [str(p.relative_to(repo)) for p in sorted(repo.glob("packages/*/pyproject.toml"))
               if "ini_options" in tomllib.load(open(p, "rb")).get("tool", {}).get("pytest", {})]
    if own_ini: errs.append(f"自分の pytest 設定を持つ package(root の ini と conftest が効かなくなる): {own_ini}")
    return errs

if __name__ == "__main__":
    case, repo, decl, makefile, files_list = sys.argv[1:6]
    excluded = dict(EXCLUDED)
    for drop in sys.argv[6:]: excluded.pop(drop, None)
    files = Path(files_list).read_text().splitlines()
    res = {"pin1": pin1(decl), "pin2": pin2(repo, makefile), "pin3": pin3(repo, files, excluded)}
    print(f"== {case}")
    for k, v in res.items(): print(f"   {k}: {'緑' if not v else '赤 ' + ' / '.join(v)}")
