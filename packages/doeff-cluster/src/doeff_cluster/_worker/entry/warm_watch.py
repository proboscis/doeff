"""待ちの子から分かれた子 A が、入口 B を fork した後に exec する見張り(card ki-97e47900b302)。

A は待ちの子の fork なので、読み込み済みの module と heap(数百 MB)の写しを持つ。A が fork のまま job の終わりまで見張ると、写しを B と
待ちの子と分け合い続け、待ちの子が消えた後(root の世代の入れ替え)や B が heap を書き替えた後は、その頁を A だけが持つ(zeus の
daily-verify の A で PSS 270 MB・Private_Dirty 80 MB)。A の見張り(shim と同じ shim_code・刻の書き手・知らせの中継・exit の file)は
読み込んだ module を使わないので、A は B を fork した直後に、この小さな入口へ exec して写しを手放す:
  待ちの子 ── fork ──▶ A ── fork ──▶ B(読み込み済みの入口)
                        └ exec ──▶ この入口(shim・line_stamp と標準の module だけを読む — pid・session・cgroup・子孫の引き取り手は A のまま)
A が exec の前に整えた物は exec を越えて残る: session と group の先頭・cgroup・子孫の引き取り(prctl の PR_SET_CHILD_SUBREAPER は
execve で消えない)・標準入力(worker の pipe か service の入力の pipe)・fd 1 と 2(刻の pipe の書き口)・環境(頼みの env)・cwd・
TERM の block。A は B を起こす前から TERM を block し、block のまま exec する — exec の窓に届いた TERM は保留のまま残り、この入口が
shim_code で TERM の扱いを据えた後に block を解いた時に、止めの期限として受ける(既定の扱いで A が落ちて exit の file が残らない事を
防ぐ)。exec の窓に B が終われば、shim_code の待ちが最初の回収で拾う。

見張りの組(WatchPlan)は 1 つの JSON の引数で渡す(argv に env の値は載せない — 環境は exec の環境として渡る)。exec した interpreter は
-I(PYTHON* の環境変数と cwd を読まない — 頼みの env は job の物)で起こし、探し道は待ちの子の sys.path をそのまま渡す(同じ root の
同じ module を読む)。exec に失敗したら、A は同じ watched を exec せずに走らせる(写しは持ったまま — 今までの形)。

使い方(A が組む — 手では起こさない): <python> -I [-B] -c BOOT <探し道の JSON> <見張りの組の JSON>
"""

import json
import os
import signal
import sys
from dataclasses import dataclass
from typing import NoReturn

from doeff_cluster._foundation.process_environ import named_environ
from doeff_cluster._worker.entry.line_stamp import (
    JOB_LOG_ENV_NAMES,
    StampedLines,
    WriterEnds,
    job_log_plan,
    shipping_of,
)
from doeff_cluster._worker.entry.shim import NoticeRelay, become_subreaper, exit_status, shim_code

# 層 entry の文脈と役の名乗り(DOEFF104)— 隣の warm_child.py・shim.py と同じ。
MODULE_TAGS = {"context": "worker", "role": "main"}

# exec した interpreter が最初に走らせる 1 行: 待ちの子の探し道を据えてから、この入口の main に見張りの組を渡す。
BOOT = (
    "import json, sys; sys.path[:] = json.loads(sys.argv[1]); "
    "from doeff_cluster._worker.entry.warm_watch import main; main(sys.argv[2])"
)


@dataclass(frozen=True)
class WatchPlan:
    """A の見張りの組: job = A が fork した入口 B の pid・grace_seconds = 止めの合図から B を待つ猶予・exit_path = 終了コードを書く file・
    ends = 刻の書き手の fd(line_stamp の stamp_ends)・relay = 退きの知らせの中継の口(None = 中継しない — task。read_fd は B に渡した
    読み口の番号で、A はもう持たない)・orphan_seconds = worker の消失の後に B が自分で終わるのを待つ秒(None = すぐ止める)。"""

    job: int
    grace_seconds: float
    exit_path: str
    ends: WriterEnds
    relay: NoticeRelay | None
    orphan_seconds: float | None


class Adopted:
    """shim_code の spawn の部品: A が exec の前に fork した B の pid を答え、B を起こす前から block していた TERM を解く(shim_code は
    TERM の扱いを据えてから spawn を呼ぶので、保留の TERM は止めの期限として受ける)。"""

    def __init__(self, job: int) -> None:
        """A が fork した B の pid を持つため。"""
        self.job = job

    def __call__(self) -> int:
        """TERM の block を解いて B の pid を返すため。"""
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
        return self.job


def descriptors_of(plan: WatchPlan) -> tuple[int, ...]:
    """exec を越えて見張りへ渡す fd(刻の書き手の 4 つと、知らせの pipe の書き口)— A は exec の前にこれらを継がせる設定にする。"""
    relayed = () if plan.relay is None else (plan.relay.write_fd,)
    ends = plan.ends
    return (ends.source, ends.target, ends.stop_read, ends.stop_write, *relayed)


def plan_text(plan: WatchPlan) -> str:
    """見張りの組を exec の引数 1 つ(JSON)に綴るため。"""
    ends = plan.ends
    relay = plan.relay
    return json.dumps(
        {
            "job": plan.job,
            "graceSeconds": plan.grace_seconds,
            "exitPath": plan.exit_path,
            "ends": [ends.source, ends.target, ends.stop_read, ends.stop_write],
            "relay": None if relay is None else [relay.env_name, relay.read_fd, relay.write_fd],
            "orphanSeconds": plan.orphan_seconds,
        }
    )


def plan_of(text: str) -> WatchPlan:
    """exec の引数の JSON を見張りの組に読むため(綴るのは同じ module の plan_text だけ — 形が違えば名を挙げて落ちる)。"""
    match json.loads(text):
        case {
            "job": int(job),
            "graceSeconds": int() | float() as grace,
            "exitPath": str(exit_path),
            "ends": [int(source), int(target), int(stop_read), int(stop_write)],
            "relay": None | [str(), int(), int()] as relay,
            "orphanSeconds": None | int() | float() as orphan,
        }:
            return WatchPlan(
                job=job,
                grace_seconds=float(grace),
                exit_path=exit_path,
                ends=WriterEnds(source=source, target=target, stop_read=stop_read, stop_write=stop_write),
                relay=None if relay is None else NoticeRelay(env_name=relay[0], read_fd=relay[1], write_fd=relay[2]),
                orphan_seconds=None if orphan is None else float(orphan),
            )
        case other:
            raise ValueError(f"見張りの組の形が違う: {sorted(other) if isinstance(other, dict) else type(other).__name__}")


def watch_argv(plan: WatchPlan) -> tuple[str, ...]:
    """A が exec する命令を組むため: 待ちの子と同じ interpreter を -I(と、待ちの子が -B なら -B)で起こし、待ちの子の探し道と見張りの組を
    渡す。"""
    flags = ("-I", *(("-B",) if sys.flags.dont_write_bytecode else ()))
    return (sys.executable, *flags, "-c", BOOT, json.dumps(sys.path), plan_text(plan))


def write_replacing(path: str, text: str) -> None:
    """別名に書いてから置き換える(書きかけを読ませない)。"""
    temporary = f"{path}.tmp-{os.getpid()}"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def watched(plan: WatchPlan) -> NoReturn:
    """A の見張り: 刻の書き手(送り先は頼みの env — この process の環境)を作り、shim と同じ見張りで B の終わりか止めまで待って片づけ、
    job の終了コードを exit の file に書いてから、shim と同じ値で終わる(exit の file を書く時には、書き手が log を書き終えている)。
    exec した見張りと、exec に失敗した A が同じ所を通る。"""
    lines = StampedLines(plan.ends, shipping_of(job_log_plan(named_environ(JOB_LOG_ENV_NAMES))))
    code = shim_code(
        plan.grace_seconds,
        Adopted(plan.job),
        lines,
        become_subreaper,
        relay=plan.relay,
        orphan_seconds=plan.orphan_seconds,
    )
    write_replacing(plan.exit_path, str(code))
    sys.stderr.flush()
    os._exit(exit_status(code))


def main(text: str) -> NoReturn:
    """exec した見張りの入口(BOOT が呼ぶ): 見張りの組を読んで見張る。"""
    watched(plan_of(text))
