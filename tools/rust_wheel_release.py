"""doeff の Rust の package の wheel を、機体の外の置き場(公開の GitHub の release `rust-wheels`)へ上げる — 置き場の書き手の 1 つ
(ADR-DOE-BUILD-001 R5 の追補・設計 docs/design/rust-wheel-distribution)。走るのは doeff の GitHub Actions
(.github/workflows/rust-wheels.yml)の中だけで、資格は Actions の GITHUB_TOKEN(contents: write)だけ。

package ごとに build の口(tools/doeff_cargo_backend.py)を `uv build --wheel` で通す — 口は置き場を引くので、同じ鍵の wheel が
置き場に在れば取って終わり(報告の出どころ remote・上げない)。無ければ口が組み、出どころ built の wheel だけを asset
`<package>-<鍵>.whl`(手元の保存先の dir の名 + .whl — 口が引く名と同じ)として上げる。同じ名の asset は上書きしない(在れば上げない)。
上げた後、asset が KEEP_ASSETS を越えていれば古い順に消す(release の asset は 1000 個が上限)。

使い方: python tools/rust_wheel_release.py --tag rust-wheels --python 3.14t packages/doeff-vm packages/doeff-linter …
(env GH_TOKEN・GITHUB_REPOSITORY が要る — Actions が渡す)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# 残す asset の数(上限 1000 の手前 — 1 回の上げで越えない余り)。
KEEP_ASSETS = 800


@dataclass(frozen=True)
class Reported:
    """build の口の報告の 1 行: project = package の名・wheel = 手元の保存先の中の wheel・source = 出どころ(local・remote・built)。"""

    project: str
    wheel: Path
    source: str

    @property
    def asset(self) -> str:
        """置き場の asset の名(手元の保存先の dir の名 `<package>-<鍵>` + .whl — 口の _remote_wheel が引く名)。"""
        return f"{self.wheel.parent.name}.whl"


def _gh(*argv: str) -> subprocess.CompletedProcess[str]:
    """gh を撃って結果を返す(rc は呼び手が読む)。"""
    return subprocess.run(["gh", *argv], capture_output=True, text=True, check=False)


def _ensure_release(repo: str, tag: str) -> None:
    """置き場の release が無ければ作る(asset の入れ物 — 版の告知ではないので latest にしない)。"""
    if _gh("release", "view", tag, "--repo", repo).returncode == 0:
        return
    made = _gh(
        "release", "create", tag, "--repo", repo, "--prerelease", "--latest=false", "--title", "Rust wheels (build cache)",
        "--notes", "doeff の build の口(tools/doeff_cargo_backend.py)が引く Rust の wheel の置き場。asset の名 = <package>-<鍵>.whl。"
        "CI(.github/workflows/rust-wheels.yml)だけが書く。設計 = docs/design/rust-wheel-distribution。",
    )
    if made.returncode != 0:
        sys.exit(f"rust_wheel_release: release {tag} を作れない — {made.stderr.strip()}")


def _build(package: Path, python: str, store: Path) -> tuple[Reported, ...]:
    """package を build の口に通し、口の報告の行を返す(口が置き場を引く — 在れば取るだけで組まない)。手元の保存先 = store
    (この走りだけの空の dir — 報告の wheel は上げ終わるまでそこに在る)。"""
    with tempfile.TemporaryDirectory(prefix="rust-wheel-release-") as work:
        report = Path(work) / "report.jsonl"
        env = {**os.environ, "DOEFF_WHEEL_CACHE": str(store), "DOEFF_WHEEL_REPORT": str(report)}
        done = subprocess.run(["uv", "build", "--wheel", "--python", python, "--out-dir", str(Path(work) / "out"), str(package)], env=env, check=False)
        if done.returncode != 0:
            sys.exit(f"rust_wheel_release: {package} の uv build が rc {done.returncode}")
        rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()] if report.exists() else []
    return tuple(Reported(project=row["project"], wheel=Path(row["wheel"]), source=row["source"]) for row in rows)


def _upload(repo: str, tag: str, reported: Reported) -> None:
    """口が組んだ wheel を asset の名で上げる。同じ名が既に在れば上書きせずに知らせる(同じ鍵の別の走りが先に上げた)。"""
    with tempfile.TemporaryDirectory(prefix="rust-wheel-asset-") as work:
        asset = Path(work) / reported.asset
        shutil.copy2(reported.wheel, asset)
        sent = _gh("release", "upload", tag, str(asset), "--repo", repo)
    if sent.returncode == 0:
        print(f"rust_wheel_release: 上げた — {reported.asset}({reported.wheel.name}・{reported.wheel.stat().st_size} byte)")
    elif "already exists" in sent.stderr:
        print(f"rust_wheel_release: 同じ名が在る(上書きしない)— {reported.asset}")
    else:
        sys.exit(f"rust_wheel_release: {reported.asset} を上げられない — {sent.stderr.strip()}")


def _prune(repo: str, tag: str) -> None:
    """release の asset が KEEP_ASSETS を越えていれば、作った時刻の古い順に越えた分を消す。"""
    release = _gh("api", f"repos/{repo}/releases/tags/{tag}", "--jq", ".id")
    listed = _gh("api", "--paginate", f"repos/{repo}/releases/{release.stdout.strip()}/assets", "--jq", ".[] | [.id, .created_at, .name] | @tsv")
    if release.returncode != 0 or listed.returncode != 0:
        sys.exit(f"rust_wheel_release: asset の一覧を読めない — {release.stderr.strip()} {listed.stderr.strip()}")
    assets = sorted((line.split("\t") for line in listed.stdout.splitlines() if line), key=lambda row: row[1])
    for asset_id, _, name in assets[: max(0, len(assets) - KEEP_ASSETS)]:
        gone = _gh("api", "-X", "DELETE", f"repos/{repo}/releases/assets/{asset_id}")
        print(f"rust_wheel_release: 古い asset を消した — {name}" if gone.returncode == 0 else f"rust_wheel_release: {name} を消せない — {gone.stderr.strip()}")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="doeff の Rust の package の wheel を置き場(release rust-wheels)へ上げる")
    parser.add_argument("--tag", required=True, help="置き場の release の tag(rust-wheels)")
    parser.add_argument("--python", required=True, help="組む Python(使う側の venv と同じ ABI — 3.14t)")
    parser.add_argument("packages", nargs="+", type=Path, help="build の口を通す package の dir")
    args = parser.parse_args(argv)
    repo = os.environ["GITHUB_REPOSITORY"]
    _ensure_release(repo, args.tag)
    with tempfile.TemporaryDirectory(prefix="rust-wheel-store-") as store:
        reported = tuple(row for package in args.packages for row in _build(package, args.python, Path(store)))
        for row in reported:
            if row.source == "built":
                _upload(repo, args.tag, row)
            else:
                print(f"rust_wheel_release: 置き場か手元に在る(上げない)— {row.asset}・{row.source}")
    _prune(repo, args.tag)


if __name__ == "__main__":
    main(sys.argv[1:])
