"""公開の刻を過ぎた回を公開済みに直してmainに入れ、Zennの記事のbranchのリンクを張り直す1回(card ki-9c84cc3b4e19)。

agoraの担当の道具の組の同期のjob(agora-controllersのagent-toolset-sync — プログラムのreconcile)が、毎日8:10 JSTと
起動の時に、doeffのmainの先端の作業木でこの命令を走らせる。1回は次の2段で、何度走らせても同じ所に落ち着く。

1. 直し: 公開の刻を過ぎた公開予定の回のうち、Zennで開ける回を「公開済み <URL> <刻>」に直す(record_published.record)。
   直した回があれば、公開順の表だけを変えるcommitをbranch(既定wt/zenn-record-sweep)へpushし、mainへのマージの列に
   載せ(知らせの宛先は--notifyの連絡のid)、main入りを待つ(既定600秒 — 列の便は普通は数十秒で入る)。
   同じbranchの便が列に在る間は、直さない(同じ直しを2度載せない)。
2. リンクの張り直し: 直しがmainに入った回だけ、mainの書き出しを`push_zenn_branch.py --keep-schedule`で
   zenn-contentへ送る。公開済みに直した回への他の回の本文のリンクが、ZennのURLになる。直す回の無い日は送らない。
   予約と公開の設定(publishedとpublished_at)を変える書き出しは送らない — この命令は予約も公開もしない
   (予約は、その1本の公開の許可を得た会話がpush_zenn_branch.pyで置き、その時にリンクも一緒に送られる)。

閲覧の数(agoraの見回りのjob — プログラムのreconcile — の段「閲覧の数」・JSTの9時)は、mainの表で公開済みの回だけを
数える。8:10の直しは9時の前にmainに入る。

終わりのcode: 0 = 直す回が無いか、直してmainに入れてリンクを張った・1 = 刻を過ぎたのにZennで開けない回が在るか、
前の直しの便がまだ列に在るか、便がmainに入らなかったか、リンクの張り直しが止まった(同期のjobが健康の帯の項を
赤にし、見回りの検査が見回りの会話へ知らせる)。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import record_published
import render_release as release
from render_release import JST

SUMMARY = "公開の刻を過ぎた回を公開済みに直してmainに入れ、Zennの記事のbranchのリンクを張り直す。"
# 道具のdirと公開計画の、repoの根からのpath(テストは偽のrepoの根を渡す)。
TOOLS = Path("publications/zenn-use-cases-v0")
PLAN = TOOLS / "publication-plan.md"
# マージの列の便が入った状態と、終わった状態(merge-queueのmerge_queue/land/core/answers.hyのENDED-STATES)。
LANDED = frozenset({"landed", "pruned"})
ENDED = LANDED | {"failed", "cancelled"}
# main入りを確かめ直す間隔の秒。
POLL_SECONDS = 10.0


@dataclass(frozen=True)
class Done:
    """命令1つの結末(終わりのcodeと、標準出力と標準エラーを合わせた文)。"""

    rc: int
    out: str


@dataclass(frozen=True)
class LandItem:
    """マージの列の便1つ(依頼のidと状態)。"""

    id: str
    status: str


@dataclass(frozen=True)
class Recorded:
    """段1の結末(終わりのcodeと、直しがこの回でmainに入ったか)。"""

    rc: int
    landed: bool


@dataclass(frozen=True)
class Sweep:
    """1回の設定(repoの根・今・知らせの宛先・agoraの命令・便のbranch・remote・記事のbranch・main入りを待つ秒)と、
    外の世界に触る関数(ZennのURLが開けるか・待ち)。"""

    root: Path
    now: datetime
    notify: str
    agora: tuple[str, ...]
    land_branch: str = "wt/zenn-record-sweep"
    remote: str = "origin"
    zenn_branch: str = "zenn-content"
    land_wait: float = 600.0
    reachable: Callable[[str], bool] = record_published.opens
    sleep: Callable[[float], None] = time.sleep


def run(argv: tuple[str, ...], cwd: Path) -> Done:
    """命令を1回走らせ、終わりのcodeと出力を返す(失敗でも止まらない — 呼び手が判じる)。"""
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    return Done(done.returncode, (done.stdout + done.stderr).strip())


def git(sweep: Sweep, *args: str) -> str:
    """repoの根でgitを1回走らせて標準出力を返す。失敗したら、gitのエラーを表示して止まる。"""
    done = run(("git", *args), sweep.root)
    if done.rc != 0:
        sys.exit(f"git {' '.join(args)} が失敗しました:\n{done.out}")
    return done.out


def land_json(sweep: Sweep, *args: str) -> object:
    """`agora land … --json`を1回走らせ、答えのJSONを返す。読めなければ表示して止まる。"""
    done = run((*sweep.agora, "land", *args, "--repo", str(sweep.root), "--json"), sweep.root)
    try:
        answer = json.loads(done.out.splitlines()[-1]) if done.rc == 0 and done.out else None
    except json.JSONDecodeError:
        answer = None
    if answer is None:
        sys.exit(f"agora land {' '.join(args)} が答えませんでした(code {done.rc}):\n{done.out}")
    return answer


def item_of(value: object) -> LandItem:
    """答えのJSONの便1つを読む。"""
    if not isinstance(value, dict) or not isinstance(value.get("id"), str):
        sys.exit(f"マージの列の便が読めません: {value!r}")
    return LandItem(value["id"], str(value.get("status")))


def live_items(sweep: Sweep) -> tuple[LandItem, ...]:
    """便のbranchの、列に在る便を返す(`agora land list --branch`)。"""
    answer = land_json(sweep, "list", "--branch", sweep.land_branch)
    requests = answer.get("requests") if isinstance(answer, dict) else None
    if not isinstance(requests, list):
        sys.exit(f"agora land list の答えに requests がありません: {answer!r}")
    return tuple(item_of(value) for value in requests)


def wait_landed(sweep: Sweep, request: str) -> str:
    """便requestが終わるか、land_wait秒を過ぎるまで待ち、最後に読んだ状態を返す。"""
    waited = 0.0
    while True:
        status = item_of(land_json(sweep, "show", request)).status
        if status in ENDED or waited >= sweep.land_wait:
            return status
        sweep.sleep(POLL_SECONDS)
        waited += POLL_SECONDS


def commit_message(recorded: tuple[record_published.Recorded, ...]) -> str:
    """公開済みに直したcommitの本文。"""
    lines = "".join(
        f"- {row.name}: {row.url}(公開の刻 {release.zenn_time(row.at)} JST)\n" for row in recorded
    )
    names = "・".join(row.slug for row in recorded)
    return (
        f"公開の刻を過ぎた回を公開済みに直す({names})\n\n"
        "record_sweep.pyが、Zennで開けることを確かめて公開順の表を直した(card ki-9c84cc3b4e19)。\n\n"
        f"{lines}"
    )


def land_recorded(sweep: Sweep, recorded: tuple[record_published.Recorded, ...]) -> Recorded:
    """表を直したcommitを便のbranchへpushして列に載せ、main入りを待つ。

    mainに入れば作業木をmainの先端へ進め(段2の書き出しの元をmainにする)、入らなければ直す前の木へ戻す。
    """
    base = git(sweep, "rev-parse", "HEAD")
    git(sweep, "add", str(PLAN))
    git(sweep, "commit", "--quiet", "-m", commit_message(recorded))
    # 列への登記は、checkoutの便のbranchのrefから先端を読む(作業木はdetachedのまま — branchを掴まない)。
    git(sweep, "branch", "--force", sweep.land_branch, "HEAD")
    refspec = f"{sweep.land_branch}:refs/heads/{sweep.land_branch}"
    git(sweep, "push", "--quiet", "--force", sweep.remote, refspec)
    title = "公開の刻を過ぎた回を公開済みに直す: " + "・".join(row.slug for row in recorded)
    flags = ("--branch", sweep.land_branch, "--title", title, "--requester-entity", sweep.notify)
    request = item_of(land_json(sweep, "request", *flags)).id
    status = wait_landed(sweep, request)
    if status in LANDED:
        git(sweep, "fetch", "--quiet", sweep.remote, "main")
        git(sweep, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        print(f"{request}: mainに入った({git(sweep, 'rev-parse', '--short=12', 'HEAD')})")
        return Recorded(0, landed=True)
    git(sweep, "checkout", "--quiet", "--detach", base)
    reason = (
        f"便を{status}にした"
        if status in ENDED
        else f"{sweep.land_wait:g}秒の内にmainに入れなかった"
    )
    print(
        f"{request}: マージの列が{reason}({sweep.land_branch})。"
        "リンクは張り直していない — 便がmainに入った後に`push_zenn_branch.py`で送る"
    )
    return Recorded(1, landed=False)


def record_step(sweep: Sweep) -> Recorded:
    """段1: 刻を過ぎて開ける回を公開済みに直し、mainに入れる。"""
    waiting = tuple(item for item in live_items(sweep) if item.status not in ENDED)
    if waiting:
        listed = "・".join(f"{item.id}({item.status})" for item in waiting)
        print(f"前の直しの便がまだ列に在るので、直さない: {listed}")
        return Recorded(1, landed=False)
    result = record_published.record((sweep.root / PLAN).read_text(), sweep.now, sweep.reachable)
    for missing in result.unreachable:
        at = release.zenn_time(missing.at)
        print(f"{missing.name}: 公開の刻 {at} JST を過ぎたのに、{missing.url} が開けない")
    rc = 1 if result.unreachable else 0
    if not result.recorded:
        print("公開済みに直す回は無い" if not result.unreachable else "公開済みに直した回は無い")
        return Recorded(rc, landed=False)
    for done in result.recorded:
        print(f"{done.name}: 公開済み {done.url}(公開の刻 {release.zenn_time(done.at)} JST)")
    (sweep.root / PLAN).write_text(result.plan)
    landed = land_recorded(sweep, result.recorded)
    return Recorded(max(rc, landed.rc), landed.landed)


def relink_step(sweep: Sweep) -> int:
    """段2: mainの書き出しを、予約と公開の設定を変えずにzenn-contentへ送る。終わりのcodeを返す。"""
    script = sweep.root / TOOLS / "push_zenn_branch.py"
    argv = (sys.executable, str(script), "--keep-schedule", "--now", sweep.now.isoformat())
    flags = ("--remote", sweep.remote, "--branch", sweep.zenn_branch)
    done = run((*argv, *flags), sweep.root)
    print(done.out)
    return 0 if done.rc == 0 else 1


def sweep_once(sweep: Sweep) -> int:
    """1回を走らせ、終わりのcodeを返す(リンクは、直しがこの回でmainに入った時だけ張り直す)。"""
    recorded = record_step(sweep)
    return max(recorded.rc, relink_step(sweep)) if recorded.landed else recorded.rc


def main() -> None:
    """1回を走らせて、終わりのcodeで終わるため。"""
    parser = argparse.ArgumentParser(description=SUMMARY)
    parser.add_argument(
        "--notify", required=True, help="マージの列の便の結末を知らせる連絡のid(m-…)"
    )
    parser.add_argument("--agora", default="agora", help="agoraの命令(既定はPATHのagora)")
    parser.add_argument(
        "--now",
        type=datetime.fromisoformat,
        default=None,
        help="公開の刻と比べる今(時差つきのISO 8601・既定は今の刻)",
    )
    parser.add_argument("--land-wait", type=float, default=600.0, help="main入りを待つ秒(既定600)")
    args = parser.parse_args()
    now = args.now or datetime.now(JST)
    sweep = Sweep(
        root=release.SOURCE.parents[1],
        now=now if now.tzinfo else now.replace(tzinfo=JST),
        notify=args.notify,
        agora=(args.agora,),
        land_wait=args.land_wait,
    )
    sys.exit(sweep_once(sweep))


if __name__ == "__main__":
    main()
