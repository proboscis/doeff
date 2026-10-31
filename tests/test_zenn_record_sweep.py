"""公開の刻を過ぎた回を公開済みに直してmainに入れ、リンクを張り直す1回の確かめ。

record_sweep.pyは、agoraの担当の道具の組の同期のjobが毎日8:10 JSTに、doeffのmainの先端の作業木で走らせる。
確かめは偽の時刻(nowを渡す)・偽のrepo(本物の公開計画と原稿と画像を写した一時のrepoと、remoteに見立てたbare repo)・
偽のagora(マージの列の便を、偽のremoteのmainを進めて入れたことにする)・偽のZenn(URLが開けるかの答え)で行い、
本物のZenn・GitHub・マージの列には触れない。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "publications/zenn-use-cases-v0"
IMAGES = Path(__file__).resolve().parents[1] / "images"
sys.path.insert(0, str(TOOLS))

import push_zenn_branch as push  # noqa: E402 — 道具は兄弟のmoduleを名で読むので、道具のdirを先に足す
import record_published  # noqa: E402
import record_sweep  # noqa: E402
import render_release as release  # noqa: E402

JST = release.JST
URL = "https://zenn.dev/proboscis/articles/"
# 偽の公開の許可の依頼書のid(lt-とCrockfordのbase32の26字 — 本物の依頼書のidではない)。
PERMIT = "lt-" + "1" * 26
OTHER_PERMIT = "lt-" + "2" * 26
# 偽の知らせの宛先(連絡のidの形 — 本物の会話ではない)。
NOTIFY = "m-" + "3" * 26
FIRST = "doeff-introduction"
SECOND = "doeff-ai-agents"
RESERVED = "2026-11-02 08:00"


def jst(text: str) -> datetime:
    """日本時間の「YYYY-MM-DD hh:mm」を刻にする(偽の今)。"""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=JST)


def run_git(repo: Path, *args: str) -> str:
    """偽のrepoでgitを走らせて標準出力を返す。"""
    return subprocess.run(
        ("git", *args), cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def set_state(plan: str, slug: str, cell: str) -> str:
    """公開計画の文のうち、slugの行の公開状態の欄をcellに替える。"""
    row = re.compile(rf"^(\|[^|\n]*\|[^|\n]*\| `{slug}` \|[^|\n]*\| )([^|\n]*?)( \|)$", re.M)
    edited, count = row.subn(lambda found: found.group(1) + cell + found.group(3), plan)
    assert count == 1, slug
    return edited


@dataclass(frozen=True)
class World:
    """偽の世界(remote = remoteに見立てたbare repo・work = mainの先端の作業木・agora = 偽のagoraの命令・
    state = 偽のagoraの状態のfile)。"""

    remote: Path
    work: Path
    agora: Path
    state: Path

    def plan(self) -> Path:
        return self.work / record_sweep.PLAN

    def remote_plan(self) -> str:
        return run_git(self.remote, "show", f"main:{record_sweep.PLAN}")

    def zenn_tip(self) -> str:
        return run_git(self.remote, "rev-parse", "zenn-content")

    def zenn_article(self, slug: str) -> str:
        return run_git(self.remote, "show", f"zenn-content:articles/{slug}.md")

    def calls(self) -> list[list[str]]:
        return json.loads(self.state.read_text())["calls"]

    def configure(self, **values: object) -> None:
        state = json.loads(self.state.read_text())
        self.state.write_text(json.dumps({**state, **values}))

    def commit_plan(self, text: str, message: str) -> None:
        """公開計画をtextにしてcommitし、偽のremoteのmainへ入れて、作業木をその先端にする。"""
        self.plan().write_text(text)
        run_git(self.work, "commit", "--quiet", "-am", message)
        run_git(self.work, "push", "--quiet", "origin", "HEAD:refs/heads/main")
        run_git(self.work, "checkout", "--quiet", "--detach", "HEAD")

    def push_zenn(self, now: str, *flags: str) -> subprocess.CompletedProcess[str]:
        """偽のrepoの道具のpush_zenn_branch.pyを、偽のremoteへ走らせる。"""
        script = self.work / record_sweep.TOOLS / "push_zenn_branch.py"
        return subprocess.run(
            (sys.executable, str(script), "--now", jst(now).isoformat(), *flags),
            cwd=self.work,
            capture_output=True,
            text=True,
            check=False,
        )

    def sweep(
        self, now: str, reachable: object = None, land_wait: float = 300.0
    ) -> record_sweep.Sweep:
        def never(url: str) -> bool:
            raise AssertionError(f"開きに来てはいけない時に {url} を開きに来た")

        return record_sweep.Sweep(
            root=self.work,
            now=jst(now),
            notify=NOTIFY,
            agora=(str(self.agora),),
            land_wait=land_wait,
            reachable=reachable if callable(reachable) else never,
            sleep=no_wait,
        )


def no_wait(seconds: float) -> None:
    """偽の待ち: 待たずに戻る(main入りを待つ間隔の秒は読まない)。"""
    assert seconds > 0


FAKE_AGORA = """#!{python}
# 偽のagora: マージの列の便をstateのfileで演じる(landsが真なら、登記の時に偽のremoteのmainを便のbranchの先端へ進める)。
import json, subprocess, sys
from pathlib import Path

state_file = Path({state!r})
state = json.loads(state_file.read_text())
args = sys.argv[1:]
state["calls"] = [*state["calls"], args]
verb = args[1]
if verb == "list":
    answer = {{"repo": "doeff", "scope": "branch", "requests": state["live"]}}
elif verb == "request":
    branch = args[args.index("--branch") + 1]
    if state["lands"]:
        subprocess.run(
            ["git", "-C", {remote!r}, "update-ref", "refs/heads/main", "refs/heads/" + branch], check=True
        )
    answer = {{"id": "L1", "status": "pending"}}
else:
    answer = {{"id": args[2], "status": state["show"]}}
state_file.write_text(json.dumps(state))
print(json.dumps(answer))
"""


@pytest.fixture
def world(tmp_path: Path) -> World:
    """本物の公開計画と原稿と画像を写した偽のrepoで、1本目に刻と許可を書いて予約を置いた世界を作る。"""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    run_git(tmp_path, "init", "--quiet", "--bare", str(remote))
    run_git(tmp_path, "init", "--quiet", "--initial-branch=main", str(work))
    run_git(work, "config", "user.name", "test")
    run_git(work, "config", "user.email", "test@example.com")
    run_git(work, "remote", "add", "origin", str(remote))
    shutil.copytree(TOOLS, work / record_sweep.TOOLS, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(IMAGES, work / "images")
    plan = (work / record_sweep.PLAN).read_text()
    (work / record_sweep.PLAN).write_text(
        set_state(plan, FIRST, f"公開予定 {RESERVED} JST 許可 {PERMIT}")
    )
    run_git(work, "add", "--all")
    run_git(work, "commit", "--quiet", "-m", "偽の公開計画")
    run_git(work, "push", "--quiet", "origin", "main")
    run_git(work, "checkout", "--quiet", "--detach", "main")
    state = tmp_path / "agora-state.json"
    state.write_text(json.dumps({"calls": [], "live": [], "lands": True, "show": "landed"}))
    agora = tmp_path / "fake-agora"
    agora.write_text(FAKE_AGORA.format(python=sys.executable, state=str(state), remote=str(remote)))
    agora.chmod(0o755)
    made = World(remote, work, agora, state)
    reserved = made.push_zenn("2026-11-01 12:00")
    assert reserved.returncode == 0, reserved.stdout + reserved.stderr
    assert push.placed_of(made.zenn_article(FIRST)) == push.Placed(True, RESERVED)
    return made


def opens_first(url: str) -> bool:
    """偽のZenn: 刻を過ぎた1本目だけが開ける。他の回を開きに来てはいけない。"""
    assert url == URL + FIRST, url
    return True


def land_verbs(calls: list[list[str]]) -> list[str]:
    return [call[1] for call in calls]


# ---- 予約と公開の設定を変えない書き出し(--keep-schedule) ----


def prepared(slug: str, published: bool, published_at: str | None = None) -> object:
    """書き出す記事1本の偽物(本文と画像は無い)。"""
    return push.export_zenn.Prepared(
        f"doeff-{slug}.md", slug, published, "本文", (), (), published_at, None
    )


def test_keep_schedule_passes_body_only_changes_and_new_drafts() -> None:
    branch = {
        "doeff-public": push.Placed(True, "2026-11-02 08:00"),
        "doeff-draft": push.Placed(False, None),
    }
    items = (
        prepared("doeff-public", True, "2026-11-02 08:00"),
        prepared("doeff-draft", False),
        prepared("doeff-new-draft", False),
    )
    assert push.schedule_changes(items, branch) == ()


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (push.Placed(False, None), prepared("doeff-one", True, "2026-11-04 08:00")),
        (push.Placed(True, "2026-11-04 08:00"), prepared("doeff-one", False)),
        (push.Placed(True, "2026-11-04 08:00"), prepared("doeff-one", True, "2026-11-05 08:00")),
        (None, prepared("doeff-one", True, "2026-11-04 08:00")),
    ],
    ids=["reserve", "unreserve", "move", "new-reserved"],
)
def test_keep_schedule_names_every_schedule_change(
    before: push.Placed | None, after: object
) -> None:
    branch = {"doeff-one": before} if before else {}
    (change,) = push.schedule_changes((after,), branch)
    assert change.startswith("articles/doeff-one.md: ")


# ---- 直しと張り直しの1回 ----


def test_sweep_before_the_time_changes_nothing(world: World) -> None:
    plan, tip = world.remote_plan(), world.zenn_tip()
    assert record_sweep.sweep_once(world.sweep("2026-11-02 07:59")) == 0
    assert land_verbs(world.calls()) == ["list"]
    assert (world.remote_plan(), world.zenn_tip()) == (plan, tip)


def test_sweep_after_the_time_records_lands_and_relinks(world: World) -> None:
    before = world.zenn_article(SECOND)
    assert URL + FIRST not in before

    assert record_sweep.sweep_once(world.sweep("2026-11-02 08:10", opens_first)) == 0

    # 表を直したcommitが、知らせの宛先を名乗って列に載り、mainに入った。
    calls = world.calls()
    assert land_verbs(calls) == ["list", "request", "show"]
    request = calls[1]
    assert request[request.index("--requester-entity") + 1] == NOTIFY
    assert request[request.index("--branch") + 1] == "wt/zenn-record-sweep"
    (row,) = [row for row in release.read_rows(world.remote_plan()) if row.slug == FIRST]
    assert row.state == release.Published(URL + FIRST, jst(RESERVED))
    assert row.permit == PERMIT
    # 2本目の本文の1本目へのリンクがZennのURLになり、どの記事の予約と公開の設定も変わらない。
    after = world.zenn_article(SECOND)
    assert URL + FIRST in after
    assert push.placed_of(after) == push.placed_of(before)
    assert push.placed_of(world.zenn_article(FIRST)) == push.Placed(True, RESERVED)
    # 作業木はmainの先端に居る(書き出しの元はmain)。
    assert run_git(world.work, "rev-parse", "HEAD") == run_git(world.remote, "rev-parse", "main")

    # 2回目は何も変えない(列に載せず、pushもしない)。
    plan, tip = world.remote_plan(), world.zenn_tip()
    assert record_sweep.sweep_once(world.sweep("2026-11-02 08:20")) == 0
    assert land_verbs(world.calls()) == ["list", "request", "show", "list"]
    assert (world.remote_plan(), world.zenn_tip()) == (plan, tip)


def recorded_plan(plan: str) -> str:
    """1本目を公開済みに直した公開計画の文(前の回の直しの便が入れる表)。"""
    cell = record_published.published_cell(URL + FIRST, jst(RESERVED), PERMIT)
    return set_state(plan, FIRST, cell)


def test_sweep_does_not_record_again_while_the_previous_land_stays_in_the_queue(
    world: World,
) -> None:
    world.configure(live=[{"id": "L9", "status": "pending"}], show="pending")
    plan, tip = world.remote_plan(), world.zenn_tip()
    assert record_sweep.sweep_once(world.sweep("2026-11-02 08:10", land_wait=30.0)) == 1
    assert land_verbs(world.calls()) == ["list", "show", "show", "show", "show"]
    assert (world.remote_plan(), world.zenn_tip()) == (plan, tip)


def test_sweep_waits_for_the_previous_land_and_relinks_when_it_lands(world: World) -> None:
    # 前の回の直しの便L9が列に在る間に、この回の取り寄せが走った(作業木はL9の前のmain)。L9は待つ間にmainに入る。
    world.commit_plan(recorded_plan(world.plan().read_text()), "前の回の直し(L9)")
    run_git(world.work, "checkout", "--quiet", "--detach", "HEAD~1")
    world.configure(live=[{"id": "L9", "status": "pending"}], show="landed")
    assert URL + FIRST not in world.zenn_article(SECOND)
    # 1本目は既に公開済み — 開きに来ず、同じ直しを2度載せず、リンクだけを張り直す。
    assert record_sweep.sweep_once(world.sweep("2026-11-02 08:20")) == 0
    assert land_verbs(world.calls()) == ["list", "show"]
    assert run_git(world.work, "rev-parse", "HEAD") == run_git(world.remote, "rev-parse", "main")
    assert URL + FIRST in world.zenn_article(SECOND)
    assert push.placed_of(world.zenn_article(FIRST)) == push.Placed(True, RESERVED)


def test_sweep_relinks_a_record_that_landed_after_an_earlier_run_gave_up(world: World) -> None:
    # 前の回は待ちの秒を越えて諦め(リンクは張っていない)、その後に便L9がmainに入った。この回の作業木はL9の後のmain。
    world.commit_plan(recorded_plan(world.plan().read_text()), "前の回の直し(L9)")
    world.configure(live=[{"id": "L9", "status": "landed"}])
    assert record_sweep.sweep_once(world.sweep("2026-11-02 09:00")) == 0
    assert land_verbs(world.calls()) == ["list"]
    assert URL + FIRST in world.zenn_article(SECOND)


def test_sweep_after_the_relink_never_pushes_other_main_edits(world: World) -> None:
    # 直しとリンクの張り直しの後、2本目の本文の直しがmainに入っても、次の回は送らない(リンクは揃っている)。
    assert record_sweep.sweep_once(world.sweep("2026-11-02 08:10", opens_first)) == 0
    article = world.work / record_sweep.TOOLS / "doeff-agents.md"
    article.write_text(article.read_text() + "\n追記。\n")
    world.commit_plan(world.plan().read_text(), "2本目の本文の直し")
    tip = world.zenn_tip()
    assert record_sweep.sweep_once(world.sweep("2026-11-03 08:10")) == 0
    assert world.zenn_tip() == tip


def test_sweep_without_a_record_never_pushes_other_main_edits(world: World) -> None:
    # 2本目の本文の直しがmainに入っても、直す回の無い日は送らない(読者に見える本文を毎日勝手に替えない)。
    article = world.work / record_sweep.TOOLS / "doeff-agents.md"
    article.write_text(article.read_text() + "\n追記。\n")
    world.commit_plan(world.plan().read_text(), "2本目の本文の直し")
    tip = world.zenn_tip()
    assert record_sweep.sweep_once(world.sweep("2026-11-02 07:00")) == 0
    assert world.zenn_tip() == tip


def opens_nothing(url: str) -> bool:
    """偽のZenn: 刻を過ぎても1本目が開けない(予約が届いていない時)。"""
    assert url == URL + FIRST, url
    return False


def test_sweep_reports_a_due_article_that_zenn_does_not_open(world: World) -> None:
    plan, tip = world.remote_plan(), world.zenn_tip()
    rc = record_sweep.sweep_once(world.sweep("2026-11-02 08:10", opens_nothing))
    assert rc == 1
    assert land_verbs(world.calls()) == ["list"]
    assert (world.remote_plan(), world.zenn_tip()) == (plan, tip)


def test_sweep_returns_to_the_base_when_the_land_does_not_finish(world: World) -> None:
    world.configure(lands=False, show="pending")
    base, plan, tip = (
        run_git(world.work, "rev-parse", "HEAD"),
        world.remote_plan(),
        world.zenn_tip(),
    )
    sweep = world.sweep("2026-11-02 08:10", opens_first, land_wait=30.0)
    assert record_sweep.sweep_once(sweep) == 1
    assert land_verbs(world.calls()) == ["list", "request", "show", "show", "show", "show"]
    assert run_git(world.work, "rev-parse", "HEAD") == base
    assert (world.remote_plan(), world.zenn_tip()) == (plan, tip)


def test_sweep_reports_a_land_the_queue_failed(world: World) -> None:
    world.configure(lands=False, show="failed")
    base = run_git(world.work, "rev-parse", "HEAD")
    assert record_sweep.sweep_once(world.sweep("2026-11-02 08:10", opens_first)) == 1
    assert run_git(world.work, "rev-parse", "HEAD") == base


def test_sweep_never_sends_a_reservation_it_did_not_make(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    # 2本目の許可と刻がmainに入ったが、予約はまだzenn-contentに無い(予約は許可を得た会話が置く)。
    plan = set_state(
        world.plan().read_text(), SECOND, f"公開予定 2026-11-04 08:00 JST 許可 {OTHER_PERMIT}"
    )
    world.commit_plan(plan, "2本目の許可と刻")
    tip = world.zenn_tip()
    # 1本目の直しはmainに入るが、リンクの張り直しは2本目の予約を含むので送らない。
    assert record_sweep.sweep_once(world.sweep("2026-11-02 08:10", opens_first)) == 0
    (row,) = [row for row in release.read_rows(world.remote_plan()) if row.slug == FIRST]
    assert row.state == release.Published(URL + FIRST, jst(RESERVED))
    assert world.zenn_tip() == tip
    assert push.placed_of(world.zenn_article(SECOND)) == push.Placed(False, None)
    assert "送りません(--keep-schedule)" in capsys.readouterr().out
