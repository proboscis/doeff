"""連載の各回を、公開計画の表に書いた刻にZennの予約公開で出す仕組みの確かめ。

公開計画(publications/zenn-use-cases-v0/publication-plan.md)の公開状態に刻を書いた回は、記事のbranch
zenn-contentにpublished: trueとpublished_atの組で置き、Zennが刻に公開する。確かめは偽の時刻(nowを
引数で渡す)と偽のrepo(一時のbare repoをremoteに見立てる)で行い、本物のZennとGitHubには触れない。
訳は docs/design/decisions/2026-10-21-zenn-reserved-publish.md。

予約と公開は、公開状態に「許可 lt-…」(その1本の公開の許可を問うた依頼書のid)を書いた回だけにする。
確かめは末尾の公開の許可の節。
訳は docs/design/decisions/2026-10-21-zenn-publish-permit.md。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "publications/zenn-use-cases-v0"
sys.path.insert(0, str(TOOLS))

import export_zenn  # noqa: E402 — 道具は兄弟のmoduleを名で読むので、道具のdirを先に足す
import push_zenn_branch as push  # noqa: E402
import record_published  # noqa: E402
import render_release as release  # noqa: E402

JST = release.JST
URL = "https://zenn.dev/proboscis/articles/"


def jst(text: str) -> datetime:
    """日本時間の「YYYY-MM-DD hh:mm」を刻にする(偽の今と公開の刻)。"""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=JST)


def plan_text(*cells: tuple[str, str]) -> str:
    """slugと公開状態の欄の組から、公開順の表を1つ持つ公開計画の文を作る。"""
    rows = "".join(
        f"| {week} | [題{week}](doeff-{name}.md) | `{slug}` | 入口 | {cell} |\n"
        for week, (name, (slug, cell)) in enumerate(
            zip(("aaa", "bbb", "ccc", "ddd"), cells, strict=False), start=1
        )
    )
    return f"### doeffの連載\n\n| 週 | 原稿 | Zennのslug | 読者への入口 | 公開状態 |\n| --- | --- | --- | --- | --- |\n{rows}"


def item(
    slug: str, published: bool, published_at: str | None = None, permit: str | None = None
) -> export_zenn.Prepared:
    """書き出す記事1本の偽物(本文は公開設定だけのfront matter・permitは表に書いた公開の許可)。"""
    at = f"published_at: {published_at}\n" if published_at else ""
    article = f'---\ntitle: "{slug}"\npublished: {str(published).lower()}\n{at}---\n本文\n'
    return export_zenn.Prepared(
        f"doeff-{slug}.md", slug, published, article, (), (), published_at, permit
    )


# 偽の公開の許可の依頼書のid(lt-とCrockfordのbase32の26字 — 本物の依頼書のidではない)。
PERMIT = "lt-" + "1" * 26
OTHER_PERMIT = "lt-" + "2" * 26


# ---- 表の読み ----


def test_plan_cells_carry_reserved_times() -> None:
    rows = release.read_rows(
        plan_text(
            ("doeff-introduction", f"公開済み {URL}doeff-introduction"),
            ("doeff-ai-agents", "公開予定 2026-10-22 08:00 JST"),
            ("doeff-virtual-time", "公開予定"),
            (
                "doeff-dependency-injection",
                f"公開済み {URL}doeff-dependency-injection 2026-10-21 08:00 JST",
            ),
        )
    )
    assert [row.state for row in rows] == [
        release.Published(f"{URL}doeff-introduction"),  # 手で出した回は刻を持たない
        release.Scheduled(jst("2026-10-22 08:00")),  # 予約公開の回
        release.Scheduled(),  # 刻の無い公開予定は予約しない
        release.Published(f"{URL}doeff-dependency-injection", jst("2026-10-21 08:00")),
    ]


@pytest.mark.parametrize(
    "cell", ["公開予定 10/22 8時", "公開予定 2026-10-22 08:00", "公開予定 2026-10-22T08:00 JST"]
)
def test_misspelled_reserved_time_is_refused(cell: str) -> None:
    # 書き損じを「刻なし」と読むと、予約したつもりの回が出ないので止める。
    with pytest.raises(ValueError, match="YYYY-MM-DD hh:mm JST"):
        release.read_rows(plan_text(("doeff-ai-agents", cell)))


# ---- 書き出し ----


def test_export_reserves_only_rows_with_a_time() -> None:
    reserved = release.PlanRow(
        "doeff-fleet.md", "doeff-fifty-ai-conversations", release.Scheduled(jst("2026-10-21 08:00"))
    )
    unreserved = release.PlanRow(
        "doeff-fleet.md", "doeff-fifty-ai-conversations", release.Scheduled()
    )
    rows = (reserved,)
    out = export_zenn.prepare(reserved, rows)
    assert out.problems == ()
    assert (out.published, out.published_at) == (True, "2026-10-21 08:00")
    assert "\npublished: true\npublished_at: 2026-10-21 08:00\n---\n" in out.article
    plain = export_zenn.prepare(unreserved, (unreserved,))
    assert (plain.published, plain.published_at) == (False, None)
    assert "published_at" not in plain.article.split("---\n")[1]


# ---- 予約と公開の守り(偽の時刻) ----

BEFORE = jst("2026-10-21 07:59")
AFTER = jst("2026-10-21 08:00")


@pytest.mark.parametrize(
    ("before", "after", "now"),
    [
        (
            push.Placed(False, None),
            item("doeff-x-article", True, "2026-10-21 08:00", PERMIT),
            BEFORE,
        ),  # 予約を置く
        (
            push.Placed(True, "2026-10-21 08:00"),
            item("doeff-x-article", True, "2026-10-22 08:00", PERMIT),
            BEFORE,
        ),  # 刻を変える
        (
            push.Placed(True, "2026-10-21 08:00"),
            item("doeff-x-article", False),
            BEFORE,
        ),  # 予約を解く
        (
            push.Placed(True, "2026-10-21 08:00"),
            item("doeff-x-article", True, "2026-10-21 08:00"),
            AFTER,
        ),  # 出た回をそのまま
        (push.Placed(True, None), item("doeff-x-article", True), AFTER),  # 手で出した回をそのまま
        (
            None,
            item("doeff-x-article", True, "2026-10-21 08:00", PERMIT),
            BEFORE,
        ),  # branchに無い記事を予約で置く
        (None, item("doeff-x-article", False), AFTER),  # branchに無い記事を下書きで置く
    ],
)
def test_reservations_before_the_time_pass(
    before: push.Placed | None, after: export_zenn.Prepared, now: datetime
) -> None:
    branch = {} if before is None else {after.slug: before}
    assert push.published_problems((after,), branch, now) == ()


@pytest.mark.parametrize(
    ("before", "after", "now", "says"),
    [
        (
            push.Placed(True, "2026-10-21 08:00"),
            item("doeff-x-article", False),
            AFTER,
            "非公開に戻さず",
        ),
        (
            push.Placed(True, "2026-10-21 08:00"),
            item("doeff-x-article", True, "2026-10-22 08:00"),
            AFTER,
            "公開日時も変えません",
        ),
        (
            push.Placed(True, None),
            item("doeff-x-article", True, "2026-10-22 08:00"),
            AFTER,
            "公開日時も変えません",
        ),
        (push.Placed(False, None), item("doeff-x-article", True), BEFORE, "その場で公開しません"),
        (
            push.Placed(False, None),
            item("doeff-x-article", True, "2026-10-21 08:00"),
            AFTER,
            "その場で公開しません",
        ),
        (None, item("doeff-x-article", True), BEFORE, "その場で公開しません"),
        (push.Placed(None, None), item("doeff-x-article", False), BEFORE, "publishedが読めません"),
        (push.Placed(False, None), item("doeff-x-article", True, "10/21 8時"), BEFORE, "Zennの形"),
    ],
)
def test_visible_articles_and_immediate_publishing_are_refused(
    before: push.Placed | None, after: export_zenn.Prepared, now: datetime, says: str
) -> None:
    branch = {} if before is None else {after.slug: before}
    (problem,) = push.published_problems((after,), branch, now)
    assert says in problem


def test_reserved_article_cannot_leave_the_plan() -> None:
    # branchから外してもZennの予約は残り、刻に出てしまう。
    branch = {
        "doeff-reserved-one": push.Placed(True, "2026-10-21 08:00"),
        "doeff-draft-one": push.Placed(False, None),
    }
    (problem,) = push.published_problems((), branch, BEFORE)
    assert problem.startswith("articles/doeff-reserved-one.md:")


# ---- 偽のrepoへのpush ----


def run_git(repo: Path, *args: str) -> str:
    """偽のrepoでgitを走らせて標準出力を返す。"""
    return subprocess.run(
        ("git", *args), cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@dataclass(frozen=True)
class FakeRepo:
    """偽のrepoの組(remote = remoteに見立てたbare repo・work = 書き出しの木を作る作業のrepo)。"""

    remote: Path
    work: Path


@pytest.fixture
def fake_repo(tmp_path: Path) -> FakeRepo:
    """偽のrepoの組を作る(remoteにzenn-contentはまだ無い)。"""
    repo = FakeRepo(tmp_path / "remote.git", tmp_path / "work")
    run_git(tmp_path, "init", "--quiet", "--bare", str(repo.remote))
    run_git(tmp_path, "init", "--quiet", str(repo.work))
    run_git(repo.work, "config", "user.name", "test")
    run_git(repo.work, "config", "user.email", "test@example.com")
    return repo


def push_export(
    items: tuple[export_zenn.Prepared, ...], repo: FakeRepo, out: Path, now: datetime
) -> tuple[str, ...]:
    """push_zenn_branch.pyのmainと同じ順で、書き出しを偽のremoteのzenn-contentへ送る。止まる所があれば返す。"""
    branch = push.read_branch(str(repo.remote), "zenn-content", repo.work)
    problems = push.published_problems(items, branch.placed if branch else {}, now)
    if problems:
        return problems
    out.mkdir()
    for prepared in items:
        export_zenn.write(prepared, out)
    for sent in push.plan(push.tree_of(out, repo.work), branch, "test", False, repo.work):
        push.send(sent, str(repo.remote), "zenn-content", repo.work)
    return ()


def front_of(remote: Path, slug: str) -> push.Placed:
    """偽のremoteのzenn-contentの記事の公開設定を読む。"""
    return push.placed_of(run_git(remote, "show", f"zenn-content:articles/{slug}.md"))


def test_reservation_reaches_the_fake_remote_and_holds_after_the_time(
    fake_repo: FakeRepo, tmp_path: Path
) -> None:
    remote = fake_repo.remote
    drafts = (item("doeff-first-article", False), item("doeff-second-article", False))
    assert push_export(drafts, fake_repo, tmp_path / "out0", jst("2026-10-20 12:00")) == ()

    # 刻の前: 1本目を予約し、2本目は下書きのまま。
    reserved = (
        item("doeff-first-article", True, "2026-10-21 08:00", PERMIT),
        item("doeff-second-article", False),
    )
    assert push_export(reserved, fake_repo, tmp_path / "out1", BEFORE) == ()
    assert front_of(remote, "doeff-first-article") == push.Placed(True, "2026-10-21 08:00")
    assert front_of(remote, "doeff-second-article") == push.Placed(False, None)
    tip = run_git(remote, "rev-parse", "zenn-content")

    # 刻の後: 同じ書き出しは変わらず(pushしない)、刻を変える書き出しは止まる。
    assert push_export(reserved, fake_repo, tmp_path / "out2", AFTER) == ()
    assert run_git(remote, "rev-parse", "zenn-content") == tip
    moved = (
        item("doeff-first-article", True, "2026-10-22 08:00", PERMIT),
        item("doeff-second-article", False),
    )
    (problem,) = push_export(moved, fake_repo, tmp_path / "out3", AFTER)
    assert "Zennで公開済みです" in problem
    assert run_git(remote, "rev-parse", "zenn-content") == tip


# ---- 公開済みへの直し ----


def test_record_marks_only_due_and_reachable_rows() -> None:
    plan = plan_text(
        ("doeff-due-reachable", "公開予定 2026-10-21 08:00 JST"),
        ("doeff-due-unreachable", "公開予定 2026-10-21 08:00 JST"),
        ("doeff-not-yet-due", "公開予定 2026-10-22 08:00 JST"),
        ("doeff-no-time-row", "公開予定"),
    )

    def reachable(url: str) -> bool:
        """偽のZenn: 刻を過ぎて開ける回は1本だけ。刻の前の回と刻の無い回は開きに来てはいけない。"""
        assert url.removeprefix(URL) in {"doeff-due-reachable", "doeff-due-unreachable"}, url
        return url.endswith("doeff-due-reachable")

    def never(url: str) -> bool:
        raise AssertionError(f"刻の前に {url} を開きに来た")

    early = record_published.record(plan, BEFORE, never)
    assert (early.plan, early.recorded, early.unreachable) == (plan, (), ())

    done = record_published.record(plan, jst("2026-10-21 09:00"), reachable)
    assert [row.slug for row in done.recorded] == ["doeff-due-reachable"]
    assert [row.url for row in done.unreachable] == [f"{URL}doeff-due-unreachable"]
    states = [row.state for row in release.read_rows(done.plan)]
    assert states == [
        release.Published(f"{URL}doeff-due-reachable", jst("2026-10-21 08:00")),
        release.Scheduled(jst("2026-10-21 08:00")),
        release.Scheduled(jst("2026-10-22 08:00")),
        release.Scheduled(),
    ]


def test_recorded_row_keeps_its_published_at() -> None:
    # 公開済みに直しても、書き出しのpublished_atは変わらない(Zennの公開日時を変えない)。
    scheduled = release.Scheduled(jst("2026-10-21 08:00"))
    recorded = release.Published(f"{URL}doeff-fifty-ai-conversations", jst("2026-10-21 08:00"))
    assert (
        export_zenn.published_at_of(scheduled)
        == export_zenn.published_at_of(recorded)
        == "2026-10-21 08:00"
    )


# ---- 今の公開計画 ----


def test_current_plan_reserves_each_series_in_table_order_one_at_a_time() -> None:
    rows = release.read_rows((TOOLS / "publication-plan.md").read_text())
    times = [
        row.state.at for row in rows if isinstance(row.state, release.Scheduled) and row.state.at
    ]
    assert len(times) == len(set(times)), "同じ刻に2本を予約しない"
    for series in {row.series for row in rows}:
        ordered = [row.state.at for row in rows if row.series == series and row.state.at]
        assert ordered == sorted(ordered), f"{series}の表の順と公開の刻の順が違う"
    # 始め方の答え(2026-10-20 14:06 JST)の前の刻で予約した回は無い。
    assert all(at > jst("2026-10-20 14:06") for at in times)
    # 公開の刻を書いた公開予定の回は、どれもその1本の公開の許可を持つ。
    unpermitted = [
        row.slug
        for row in rows
        if isinstance(row.state, release.Scheduled) and row.state.at and row.permit is None
    ]
    assert unpermitted == [], f"許可の無い回に公開の刻がある: {unpermitted}"


# ---- 公開の許可 ----


def test_plan_cells_carry_permits_after_the_time() -> None:
    rows = release.read_rows(
        plan_text(
            ("doeff-introduction", f"公開済み {URL}doeff-introduction"),
            ("doeff-ai-agents", f"公開予定 2026-10-22 08:00 JST 許可 {PERMIT}"),
            ("doeff-virtual-time", f"公開予定 許可 {OTHER_PERMIT}"),
            ("doeff-dependency-injection", "公開予定 2026-10-23 08:00 JST"),
        )
    )
    assert [(row.state, row.permit) for row in rows] == [
        (release.Published(f"{URL}doeff-introduction"), None),  # 決まりの前に手で出した回
        (release.Scheduled(jst("2026-10-22 08:00")), PERMIT),  # 許可の在る予約
        (release.Scheduled(), OTHER_PERMIT),  # 許可が出て、刻はまだ決めていない回
        (
            release.Scheduled(jst("2026-10-23 08:00")),
            None,
        ),  # 刻だけで許可の無い回(書き出すと止まる)
    ]


@pytest.mark.parametrize(
    "cell",
    [
        "公開予定 2026-10-22 08:00 JST 許可 lt-abc",  # idの形でない
        f"公開予定 2026-10-22 08:00 JST {PERMIT}",  # 「許可」の語が無い
        f"公開予定 許可 {PERMIT.removeprefix('lt-')}",  # lt-が無い
        f"公開予定 許可 {PERMIT} 2026-10-22 08:00 JST",  # 刻と許可の順が逆
        f"公開予定 2026-10-22 08:00 JST 許可 {PERMIT.replace('1', 'I')}",  # Crockfordの外の字
    ],
)
def test_misspelled_permit_is_refused(cell: str) -> None:
    # 許可の書き損じを「許可なし」と黙って読むと、許可の在る回が止まる訳が見えないので止める。
    with pytest.raises(ValueError, match="許可 lt-"):
        release.read_rows(plan_text(("doeff-ai-agents", cell)))


def test_one_permit_cannot_cover_two_articles() -> None:
    # 依頼書1通は記事1本の許可。別の記事の許可を流用した表は読まない。
    plan = plan_text(
        ("doeff-ai-agents", f"公開予定 2026-10-22 08:00 JST 許可 {PERMIT}"),
        ("doeff-virtual-time", f"公開予定 2026-10-24 08:00 JST 許可 {PERMIT}"),
    )
    with pytest.raises(ValueError, match="依頼書1通は記事1本の許可"):
        release.read_rows(plan)


def test_export_carries_the_permit_of_its_row() -> None:
    permitted = release.PlanRow(
        "doeff-fleet.md",
        "doeff-fifty-ai-conversations",
        release.Scheduled(jst("2026-10-22 08:00")),
        permit=PERMIT,
    )
    out = export_zenn.prepare(permitted, (permitted,))
    assert (out.published, out.published_at, out.permit) == (True, "2026-10-22 08:00", PERMIT)
    assert f"許可 {PERMIT}" in export_zenn.summary(out)
    # 公開の許可は書き出しの記事に入れない(Zennのfront matterの項目でない)。
    assert PERMIT not in out.article


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (push.Placed(False, None), item("doeff-x-article", True, "2026-10-22 08:00")),  # 予約を置く
        (None, item("doeff-x-article", True, "2026-10-22 08:00")),  # branchに無い記事を予約で置く
        (
            push.Placed(True, "2026-10-21 09:00"),
            item("doeff-x-article", True, "2026-10-21 09:00"),
        ),  # 許可の無いまま、branchに残る予約を保つ
        (
            push.Placed(True, "2026-10-21 09:00"),
            item("doeff-x-article", True, "2026-10-22 08:00"),
        ),  # 許可の無いまま、予約の刻を変える
    ],
)
def test_unpermitted_reservation_is_refused(
    before: push.Placed | None, after: export_zenn.Prepared
) -> None:
    branch = {} if before is None else {after.slug: before}
    (problem,) = push.published_problems((after,), branch, BEFORE)
    assert "公開の許可がありません" in problem
    # 同じ書き出しに、その1本の許可を足すと通る。
    permitted = item(after.slug, True, after.published_at, PERMIT)
    assert push.published_problems((permitted,), branch, BEFORE) == ()


@pytest.mark.parametrize(
    ("before", "after", "now"),
    [
        (
            push.Placed(True, "2026-10-21 09:00"),
            item("doeff-x-article", False),
            BEFORE,
        ),  # 許可の無い予約を刻の前に解く
        (None, item("doeff-x-article", False), BEFORE),  # 下書きで置く
        (
            push.Placed(True, None),
            item("doeff-x-article", True),
            AFTER,
        ),  # 許可の決まりの前に読者に見えた回(doeffの連載の1本目の形)は、今の決まりのまま保つ
        (
            push.Placed(True, "2026-10-21 08:00"),
            item("doeff-x-article", True, "2026-10-21 08:00"),
            AFTER,
        ),  # 刻を過ぎて読者に見えている回を保つ
    ],
)
def test_drafts_unreserving_and_visible_articles_need_no_permit(
    before: push.Placed | None, after: export_zenn.Prepared, now: datetime
) -> None:
    branch = {} if before is None else {after.slug: before}
    assert push.published_problems((after,), branch, now) == ()


def test_unpermitted_reservation_never_reaches_the_fake_remote(
    fake_repo: FakeRepo, tmp_path: Path
) -> None:
    remote = fake_repo.remote
    drafts = (item("doeff-first-article", False), item("doeff-second-article", False))
    assert push_export(drafts, fake_repo, tmp_path / "out0", jst("2026-10-20 12:00")) == ()
    tip = run_git(remote, "rev-parse", "zenn-content")

    # 許可の無い2本目を予約しようとすると、1本目の許可が在っても何もpushしない。
    mixed = (
        item("doeff-first-article", True, "2026-10-21 08:00", PERMIT),
        item("doeff-second-article", True, "2026-10-22 08:00"),
    )
    (problem,) = push_export(mixed, fake_repo, tmp_path / "out1", BEFORE)
    assert problem.startswith("articles/doeff-second-article.md: 公開の許可がありません")
    assert run_git(remote, "rev-parse", "zenn-content") == tip
    assert front_of(remote, "doeff-first-article") == push.Placed(False, None)
    assert front_of(remote, "doeff-second-article") == push.Placed(False, None)

    # 許可の在る1本だけを予約すると、その1本だけが予約になる。
    permitted = (
        item("doeff-first-article", True, "2026-10-21 08:00", PERMIT),
        item("doeff-second-article", False),
    )
    assert push_export(permitted, fake_repo, tmp_path / "out2", BEFORE) == ()
    assert front_of(remote, "doeff-first-article") == push.Placed(True, "2026-10-21 08:00")
    assert front_of(remote, "doeff-second-article") == push.Placed(False, None)


def test_record_keeps_the_permit_when_marking_published() -> None:
    plan = plan_text(("doeff-due-reachable", f"公開予定 2026-10-21 08:00 JST 許可 {PERMIT}"))
    done = record_published.record(plan, jst("2026-10-21 09:00"), lambda _url: True)
    (row,) = release.read_rows(done.plan)
    assert row.state == release.Published(f"{URL}doeff-due-reachable", jst("2026-10-21 08:00"))
    assert row.permit == PERMIT
    assert f"| 公開済み {URL}doeff-due-reachable 2026-10-21 08:00 JST 許可 {PERMIT} |" in done.plan
