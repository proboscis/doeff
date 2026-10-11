"""検の helper(_plain_hook_repository・_environment・_run_child)と同じ組み立てで、素の hook(改変後・原本)と枠組みの
`pre-commit run semgrep-rules`、および .git/hooks に写した hook を通した `git commit` の結末を並べる。

使い方: python driver.py <clone の根> <原本の hook> <changed> <source> <作業 dir>
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


def load(root: Path):
    spec = importlib.util.spec_from_file_location("wiring", root / "tests" / "test_lint_entrypoint_wiring.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prepared(wiring, directory: Path, changed: str, source: str):
    directory.mkdir(parents=True)
    (directory / "semgrep").symlink_to(shutil.which("semgrep"))
    repository = wiring._plain_hook_repository(directory, changed, source)
    uv = shutil.which("uv")
    env = wiring._environment(directory, f"{directory}:{Path(uv).parent}:/usr/bin:/bin")
    return repository, env


def report(label: str, outcome, rule: str) -> None:
    text = outcome.stdout + outcome.stderr
    print(f"[{label}] exit={outcome.exit_code} rule_in_output={rule in text}")


def main() -> None:
    root, original, changed, source, work = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4], Path(sys.argv[5])
    wiring = load(root)
    drop = ("SKIP", "VIRTUAL_ENV")
    rule = wiring.VOCABULARY_RULE
    for label, hook in (("patched-hook", wiring.PLAIN_HOOK), ("original-hook", original)):
        repository, env = prepared(wiring, work / label, changed, source)
        report(f"{label} sh", wiring._run_child(("/bin/sh", str(hook)), repository, env, drop), rule)
    repository, env = prepared(wiring, work / "framework", changed, source)
    report("framework pre-commit run semgrep-rules",
           wiring._run_child((sys.executable, "-m", "pre_commit", "run", "semgrep-rules"), repository, env, drop), rule)
    # Pod の準備(worker-prepare.sh の 5.)と同じく hook を .git/hooks へ写し、git commit が撃つ hook の結末を見る。
    for label, hook in (("patched-hook", wiring.PLAIN_HOOK), ("original-hook", original)):
        repository, env = prepared(wiring, work / f"commit-{label}", changed, source)
        hooks = Path(subprocess.run(["/usr/bin/git", "rev-parse", "--git-path", "hooks"], cwd=repository,
                                    capture_output=True, text=True, check=True).stdout.strip())
        hooks = hooks if hooks.is_absolute() else repository / hooks
        hooks.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hook, hooks / "pre-commit")
        (hooks / "pre-commit").chmod(0o755)
        outcome = wiring._run_child(("/usr/bin/git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                     "commit", "-qm", "add file"), repository, env, drop)
        count = subprocess.run(["/usr/bin/git", "rev-list", "--count", "HEAD"], cwd=repository,
                               capture_output=True, text=True, check=True).stdout.strip()
        report(f"git commit via .git/hooks copy of {label}", outcome, rule)
        print(f"    commits on HEAD = {count}(1 = base だけ・2 = 違反の file が commit に入った)")


if __name__ == "__main__":
    main()
