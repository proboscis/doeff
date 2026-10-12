# /// script
# requires-python = ">=3.11"
# ///
"""doeff-linter の事実の cache の設計検証(card ki-9743c30d49bc・依頼 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW)— 並走・決定性・書けない置き場の実行検。

使い方: uv run --script stress.py <c3|c4|c5|mutant-stale|mutant-fatal> --binary <doeff-linter の binary> [--rounds N] [--roots N]
どの検も、同じ木を cache 無し(DOEFF_LINTER_NO_CACHE=1)で走らせた答えを基準にし、cache あり・並走・壊れ・書けない置き場の答えが
同じ(根の絶対 path を <ROOT> に置き換えた出力の hash が同じ・終了状態が同じ)であることを見る。1 つでも食い違えば終了状態 1。
"""
import argparse
import hashlib
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIG = """
[tool.doeff-linter]
architecture = "architecture.hy"
enable = ["DOEFF106", "DOEFF122", "DOEFF126", "DOEFF127", "DOEFF129", "DOEFF130"]

[tool.doeff-linter.definitions]
paths = ["app"]
test_paths = ["tests/**/test_*.hy"]

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation"]
"""
ARCHITECTURE = '(defarchitecture sample :root "app" :layers [(layer core :summary "核") (layer foundation :summary "土台")] :foundation foundation)\n'


def module_source(i: int, modules: int, edit: bool) -> str:
    """tests/facts_cache_shared.rs の module_source と同じ形(定義を十数本・module 同士が呼び合う)。edit なら先頭の defn を 1 つ足す。"""
    text = "(import time)\n"
    for k in range(1, 13):
        text += f"(import app.core.mod{(i + k) % modules})\n"
    text += f"(defrecord Box{i} [value])\n(defeffect Ping{i} {{:fields [(: n int)] :answer None}})\n"
    for j in range(10):
        text += (
            f'(defk step-{i}-{j} [x] {{:tags {{:context "c" :role "program"}} :effects [Ping{i}]}}\n'
            f"  (<- y (Ping{i} {j}))\n  (<- z (Ping{i} (+ y {j})))\n  [y z x])\n"
        )
    nxt = (i + 1) % modules
    text += (
        f'(defk run-{i} [x] {{:tags {{:context "c" :role "program"}}}}\n  (<- a (step-{i}-0 x))\n  (<- b (step-{i}-9 a))\n'
        f"  (<- c (app.core.mod{nxt}.step-{nxt}-0 b))\n  c)\n"
    )
    text += f"(defn now-{i} [] (time.time))\n"
    if edit and i == 0:
        text += "(defn added-by-edit [] (time.time))\n"
    return text


def checkout(root: Path, modules: int, edit: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(CONFIG)
    (root / "architecture.hy").write_text(ARCHITECTURE)
    (root / "app/core").mkdir(parents=True, exist_ok=True)
    for i in range(modules):
        (root / f"app/core/mod{i}.hy").write_text(module_source(i, modules, edit))


def command(binary: Path, root: Path, cache: Path | None, env_extra: dict[str, str] | None = None) -> tuple[list[str], dict[str, str], Path]:
    env = dict(os.environ, DOEFF_LINTER_CHECK_SLOT="1")
    env.pop("DOEFF_LINTER_NO_CACHE", None)
    env.pop("DOEFF_LINTER_CACHE_DIR", None)
    if cache is None:
        env["DOEFF_LINTER_NO_CACHE"] = "1"
    else:
        env["DOEFF_LINTER_CACHE_DIR"] = str(cache)
    env.update(env_extra or {})
    return [str(binary), "--output-format", "editor-json", "--no-log"], env, root


def digest(stdout: bytes, root: Path) -> str:
    """根の絶対 path を <ROOT> に置き換えた出力の hash(位置の path 以外は作業木によらず同じはず)。"""
    text = stdout.decode(errors="replace").replace(str(root), "<ROOT>")
    try:
        report = json.loads(text)
        report.pop("linter", None)
        text = json.dumps(report, sort_keys=True, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def run_once(binary: Path, root: Path, cache: Path | None, env_extra: dict[str, str] | None = None) -> dict:
    argv, env, cwd = command(binary, root, cache, env_extra)
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=600)
    return {"exit": done.returncode, "answer": digest(done.stdout, root), "size": len(done.stdout), "stderr": done.stderr.decode(errors="replace")}


def baseline(binary: Path, root: Path) -> dict:
    result = run_once(binary, root, None)
    assert result["exit"] in (0, 1), result
    assert result["size"] > 1000, "基準の答えが空に近い(検が空振りになる)"
    return {"exit": result["exit"], "answer": result["answer"], "size": result["size"]}


def objects_of(cache: Path) -> list[Path]:
    objects = cache / "objects"
    return [p for p in objects.rglob("*") if p.is_file()] if objects.exists() else []


def age_everything(cache: Path, seconds: int) -> None:
    """置き場の全 file の更新時刻を過去へ送る(片づけが「使っている根」として守る 10 分を越えさせる)。"""
    past = time.time() - seconds
    for path in cache.rglob("*"):
        if path.is_file():
            os.utime(path, (past, past))


class Verdict:
    def __init__(self) -> None:
        self.checked = 0
        self.bad: list[str] = []

    def expect(self, label: str, got: dict, want: dict) -> None:
        self.checked += 1
        problems = []
        if got["exit"] != want["exit"]:
            problems.append(f"終了状態 {got['exit']} (基準 {want['exit']})")
        if got["answer"] != want["answer"]:
            problems.append(f"答え {got['answer']} (基準 {want['answer']})")
        if "panicked" in got.get("stderr", ""):
            problems.append("stderr に panic")
        if problems:
            self.bad.append(f"{label}: {' / '.join(problems)}")
            print(f"  食い違い {label}: {' / '.join(problems)}")

    def finish(self, title: str) -> int:
        print(f"{title}: 確かめた実行 {self.checked} 本・食い違い {len(self.bad)} 本")
        for line in self.bad[:20]:
            print("  -", line)
        return 1 if self.bad else 0


def c3(binary: Path, rounds: int, roots: int, modules: int, seed: int) -> int:
    """並走・片づけ・途中死・壊れた事実で、全部の完走した実行の答えが cache 無しと同じ。"""
    rng = random.Random(seed)
    verdict = Verdict()
    work = Path(tempfile.mkdtemp(prefix="c3-"))
    cache = work / "cache"
    trees = [work / f"tree{k}" for k in range(roots)]
    reference = {}
    for edit in (False, True):
        probe = work / f"probe-{edit}"
        checkout(probe, modules, edit)
        reference[edit] = baseline(binary, probe)
        shutil.rmtree(probe)
    print(f"基準(cache 無し): 木 A {reference[False]} / 木 B(mod0 を 1 つ編集){reference[True]}")
    killed = completed = swept = corrupted = removed_by_sweep = 0
    for number in range(rounds):
        edit = number % 2 == 1
        for tree in trees:
            if tree.exists():
                shutil.rmtree(tree)
            checkout(tree, modules, edit)  # 作り直しで更新時刻は全部新しい(checkout し直した作業木)
        # 片づけが事実を消せる状態にする: 全 file を古くし、番の記録を消し、上限を事実の合計より小さくする。
        cap_env: dict[str, str] = {}
        aged: set[Path] = set()
        if number % 3 == 1:
            age_everything(cache, 3 * 24 * 3600) if cache.exists() else None
            for record in (".swept", ".swept-cap", ".swept-cap.lock"):
                (cache / record).unlink(missing_ok=True)
            cap_env["DOEFF_LINTER_CACHE_MAX_BYTES"] = str(rng.choice([200_000, 1_000_000, 3_000_000]))
            aged = set(objects_of(cache)) if cache.exists() else set()
            swept += 1
        # 壊れた事実を混ぜる(切り詰め・ゴミ・空)。
        if number % 3 == 2 and cache.exists():
            files = objects_of(cache)
            for path in rng.sample(files, k=min(len(files), max(1, len(files) // 5))):
                choice = rng.choice(["truncate", "garbage", "empty"])
                if choice == "truncate":
                    path.write_bytes(path.read_bytes()[: max(1, path.stat().st_size // 2)])
                elif choice == "garbage":
                    path.write_bytes(os.urandom(max(8, path.stat().st_size)))
                else:
                    path.write_bytes(b"")
                corrupted += 1
        processes = []
        for index, tree in enumerate(trees):
            argv, env, cwd = command(binary, tree, cache, cap_env)
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append((index, tree, proc, rng.random() < 0.3))
        # 約 3 割の実行は、始まって少しして SIGKILL する(途中死)。
        time.sleep(rng.uniform(0.05, 0.4))
        for _, _, proc, doomed in processes:
            if doomed and proc.poll() is None:
                proc.send_signal(signal.SIGKILL)
        for index, tree, proc, doomed in processes:
            stdout, stderr = proc.communicate(timeout=600)
            if proc.returncode == -signal.SIGKILL:
                killed += 1
                continue
            completed += 1
            result = {"exit": proc.returncode, "answer": digest(stdout, tree), "stderr": stderr.decode(errors="replace")}
            verdict.expect(f"round{number} root{index} edit={edit} cap={cap_env.get('DOEFF_LINTER_CACHE_MAX_BYTES', '-')}", result, reference[edit])
        removed_by_sweep += len([path for path in aged if not path.exists()])
        # 途中死の後の置き場でも、次に 1 本だけ走らせて同じ答え(置き場に壊れが残っていても作り直す)。
        after = run_once(binary, trees[0], cache, cap_env)
        verdict.expect(f"round{number} 途中死の後の 1 本 edit={edit}", after, reference[edit])
    leftovers = [p for p in cache.rglob("*") if p.is_file() and p.name.startswith(".") and p.name.endswith(".tmp")]
    print(f"c3: 完走 {completed}・殺した {killed}・片づけを起こした round {swept}・壊した事実 {corrupted}・古くして片づけに差し出した事実のうち実行の間に消えた数 {removed_by_sweep}・書きかけの一時 file の残り {len(leftovers)}")
    shutil.rmtree(work)
    return verdict.finish("c3")


def c4(binary: Path, modules: int, seed: int) -> int:
    """置き場の状態(空・温・一部欠け・壊れ・表だけ欠け・事実だけ欠け)と thread の数を替えても、答えは同じ。"""
    rng = random.Random(seed)
    verdict = Verdict()
    work = Path(tempfile.mkdtemp(prefix="c4-"))
    tree = work / "tree"
    checkout(tree, modules)
    reference = baseline(binary, tree)
    print(f"基準(cache 無し): {reference}")
    answers: dict[str, str] = {}

    def state_empty(cache: Path) -> None:
        shutil.rmtree(cache, ignore_errors=True)

    def warm(cache: Path) -> None:
        shutil.rmtree(cache, ignore_errors=True)
        run_once(binary, tree, cache)

    def state_warm(cache: Path) -> None:
        warm(cache)

    def state_third_missing(cache: Path) -> None:
        warm(cache)
        files = objects_of(cache)
        for path in rng.sample(files, k=len(files) // 3):
            path.unlink()

    def state_corrupt(cache: Path) -> None:
        warm(cache)
        files = objects_of(cache)
        for path in rng.sample(files, k=len(files) // 2):
            path.write_bytes(os.urandom(max(8, path.stat().st_size)))

    def state_objects_gone(cache: Path) -> None:
        warm(cache)
        shutil.rmtree(cache / "objects")

    def state_tables_gone(cache: Path) -> None:
        warm(cache)
        for child in cache.iterdir():
            if child.is_dir() and child.name != "objects":
                shutil.rmtree(child)

    def state_tables_corrupt(cache: Path) -> None:
        warm(cache)
        for child in cache.iterdir():
            if child.is_dir() and child.name != "objects":
                for path in child.rglob("*"):
                    if path.is_file() and path.name not in ("root.path", "used"):
                        path.write_bytes(os.urandom(max(8, path.stat().st_size)))

    # 温めた後に、同じ根の中で 1 file を編集する(大きさも変わる)— 同じ根の古い表の行・古い事実が残る置き場に、中身の違う木を当てる。
    edited = work / "edited"
    checkout(edited, modules, edit=True)
    reference_edited = baseline(binary, edited)
    print(f"基準(cache 無し・mod0 を編集した木): {reference_edited}")

    def state_edited_in_place(cache: Path) -> None:
        checkout(tree, modules, edit=False)
        warm(cache)
        checkout(tree, modules, edit=True)
    states = {
        "空": (state_empty, tree, reference),
        "温": (state_warm, tree, reference),
        "事実が 1/3 欠け": (state_third_missing, tree, reference),
        "事実の半分が壊れ": (state_corrupt, tree, reference),
        "事実が全部無い": (state_objects_gone, tree, reference),
        "表が全部無い": (state_tables_gone, tree, reference),
        "表が全部壊れ": (state_tables_corrupt, tree, reference),
        "温めた後に同じ根で 1 file を編集": (state_edited_in_place, tree, reference_edited),
    }
    for name, (prepare, target, want) in states.items():
        for threads in (1, 2, 8):
            cache = work / "cache"
            prepare(cache)
            result = run_once(binary, target, cache, {"RAYON_NUM_THREADS": str(threads)})
            answers[f"{name}/thread={threads}"] = result["answer"]
            verdict.expect(f"状態={name} thread={threads}", result, want)
            # 同じ状態の直後にもう 1 本(2 本目も同じ答え)。
            second = run_once(binary, target, cache, {"RAYON_NUM_THREADS": str(threads)})
            verdict.expect(f"状態={name} thread={threads} の 2 本目", second, want)
    distinct = sorted(set(answers.values()))
    print(f"c4: 状態 {len(states)} × thread 3 の答えの種類 = {len(distinct)}({distinct}) — 木 A の答え 1 つと、編集した木の答え 1 つ")
    shutil.rmtree(work)
    return verdict.finish("c4")


def c5(binary: Path, modules: int) -> int:
    """書けない・読み専用・作れない置き場でも、答えは cache 無しと同じで失敗しない。"""
    verdict = Verdict()
    work = Path(tempfile.mkdtemp(prefix="c5-"))
    tree = work / "tree"
    checkout(tree, modules)
    reference = baseline(binary, tree)
    print(f"基準(cache 無し): {reference}")

    warm = work / "warm"
    run_once(binary, tree, warm)
    # 1. 温かい読み専用の置き場(読むだけ・何も書けない)
    shutil.copytree(warm, work / "ro-warm")
    for path in sorted((work / "ro-warm").rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    (work / "ro-warm").chmod(0o555)
    verdict.expect("温かい読み専用", run_once(binary, tree, work / "ro-warm"), reference)
    # 2. 空の読み専用の置き場
    (work / "ro-empty").mkdir()
    (work / "ro-empty").chmod(0o555)
    verdict.expect("空の読み専用", run_once(binary, tree, work / "ro-empty"), reference)
    # 書けなかった事の確かめ(書けていたら検が空振りになる): 空の読み専用の置き場には何も増えていない。
    verdict.checked += 1
    if any((work / "ro-empty").iterdir()):
        verdict.bad.append("空の読み専用の置き場に file が書かれた(読み専用が効いていない)")
    # 3. 置き場の path の途中が普通の file(dir を作れない)
    (work / "blocker").write_text("file")
    verdict.expect("途中が file", run_once(binary, tree, work / "blocker" / "cache"), reference)
    verdict.checked += 1
    if (work / "blocker").is_dir():
        verdict.bad.append("途中が file の path に dir ができた(前提が効いていない)")
    # 4. 根の dir は書けるが objects だけ読み専用
    shutil.copytree(warm, work / "ro-objects")
    shutil.rmtree(work / "ro-objects" / "objects")
    (work / "ro-objects" / "objects").mkdir()
    (work / "ro-objects" / "objects").chmod(0o555)
    verdict.expect("objects だけ読み専用(事実は無い)", run_once(binary, tree, work / "ro-objects"), reference)
    # 5. 置き場が存在しない path(作れる)・相対 path
    verdict.expect("まだ無い置き場", run_once(binary, tree, work / "fresh"), reference)
    # 6. 更新時刻を全部 1970 年に固定した置き場(mtime を使えない FS に近い)
    shutil.copytree(warm, work / "epoch")
    for path in (work / "epoch").rglob("*"):
        os.utime(path, (0, 0))
    verdict.expect("更新時刻が全部 1970 年", run_once(binary, tree, work / "epoch"), reference)
    # 7. 木の file の更新時刻を全部 1970 年に(表の stamp が全行食い違う)
    for path in tree.rglob("*"):
        if path.is_file():
            os.utime(path, (0, 0))
    verdict.expect("木の更新時刻が全部 1970 年(温かい置き場)", run_once(binary, tree, work / "epoch"), reference)
    for path in sorted(work.rglob("*"), reverse=True):
        try:
            path.chmod(0o755 if path.is_dir() else 0o644)
        except OSError:
            pass
    shutil.rmtree(work, ignore_errors=True)
    return verdict.finish("c5")


def stale(binary: Path, modules: int) -> int:
    """検の感度(mutant-stale): 行の stamp を信じ過ぎる変種は、file を編集した後の答えが cache 無しと食い違う — c4 型の検が拾える事を見る。"""
    verdict = Verdict()
    work = Path(tempfile.mkdtemp(prefix="stale-"))
    tree = work / "tree"
    checkout(tree, modules)
    cache = work / "cache"
    run_once(binary, tree, cache)
    # 同じ大きさ・同じ更新時刻のまま中身だけ変える(stamp 一致 → 古い事実が答えに入る変種を露わにする)
    target = tree / "app/core/mod0.hy"
    stat = target.stat()
    text = target.read_text()
    edited = text.replace("(defn now-0 [] (time.time))", "(defn now-X [] (time.time))")
    assert len(edited.encode()) == len(text.encode())
    target.write_text(edited)
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    reference = baseline(binary, tree)
    result = run_once(binary, tree, cache)
    verdict.expect("同じ大きさ・同じ更新時刻で中身だけ変えた後", result, reference)
    shutil.rmtree(work)
    return verdict.finish("stale")


def fatal(binary: Path, modules: int) -> int:
    """検の感度(mutant-fatal): 書き失敗を致命にする変種は、読み専用の置き場で失敗する — c5 型の検が拾える事を見る。"""
    verdict = Verdict()
    work = Path(tempfile.mkdtemp(prefix="fatal-"))
    tree = work / "tree"
    checkout(tree, modules)
    reference = baseline(binary, tree)
    (work / "ro-empty").mkdir()
    (work / "ro-empty").chmod(0o555)
    verdict.expect("空の読み専用", run_once(binary, tree, work / "ro-empty"), reference)
    (work / "ro-empty").chmod(0o755)
    shutil.rmtree(work)
    return verdict.finish("fatal")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("check", choices=["c3", "c4", "c5", "mutant-stale", "mutant-fatal"])
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--roots", type=int, default=6)
    parser.add_argument("--modules", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20261012)
    args = parser.parse_args()
    binary = args.binary.resolve()
    print(f"# 対象の binary: {binary} (sha256 {hashlib.sha256(binary.read_bytes()).hexdigest()[:16]})")
    print(f"# 引数: check={args.check} rounds={args.rounds} roots={args.roots} modules={args.modules} seed={args.seed}")
    code = {
        "c3": lambda: c3(binary, args.rounds, args.roots, args.modules, args.seed),
        "c4": lambda: c4(binary, args.modules, args.seed),
        "c5": lambda: c5(binary, args.modules),
        "mutant-stale": lambda: stale(binary, args.modules),
        "mutant-fatal": lambda: fatal(binary, args.modules),
    }[args.check]()
    sys.exit(code)


if __name__ == "__main__":
    main()
