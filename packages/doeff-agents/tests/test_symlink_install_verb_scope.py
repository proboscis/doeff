"""symlink を据える物理が、許した 2 つの動詞の**中**にしか無いことの構造検査。

card acp:kanban-issue:ki-62aa1f4e9c9c 決定 D8 / 設計
``docs/design/symlink-verbs-fail-vocabulary-ZCN5BD/design.md`` 3.6 (b)。

なぜ semgrep では足りないか
---------------------------
semgrep の規則 ``doeff-agents-symlink-install-has-one-home`` は ``substrate.hy`` を
**file 単位で exclude** して出荷側の据え付けを通している。つまり「この file の中は自由」の
意味で、同じ file に 3 つ目の hand-roll が生えても緑のままになる(この検査を書く発端が
まさにそれだった)。semgrep の generic モードでは Hy の関数の範囲を表現できないので、
Hy 自身の reader で構文木を読み、**動詞の単位**で判定する。
file 単位の exclude はそのまま維持する — あちらは「file の外は 1 つも許さない」という
別の役目を持つ。

なぜ許す綴りが 1 つではなく 2 つなのか
--------------------------------------
**物理が逆だから**、1 つの正しい形には畳めない。

* ``ensure-symlink-outcome`` = **置き換える**据え付け。一意な名の仮 symlink を張り、
  ``rename(2)`` 1 手で被せる。読み手は常に古い先か新しい先のどちらかを見る。
* ``FsLinkArtifact`` = **置き換えない**敷設。``os.symlink`` を撃ち、``FileExistsError`` を
  「見た後に何かが現れた」の合図として ``samefile`` を読み直す。据わっている物には
  絶対に触らない。

どちらも「見てから張る」を判断の座にしないという点では同じで、そこが守るべき 1 点。

⚠ Hy の reader は ``os.symlink`` を ``(. os symlink)`` へ、``(.symlink_to obj target)`` を
``((. None symlink_to) obj target)`` へ割る。綴りの grep でも、頭の Symbol を
``".symlink_to"`` と読む素朴な形でも見つからない(設計の試作はそこを取りこぼしていて、
現物に該当が 0 件だったので黙って緑だった)。**構文木の形**で見分けること。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import hy  # noqa: F401  # registers the Hy import hooks / reader
from hy import models
from hy.reader import read_many

SOURCE = Path(__file__).resolve().parents[1] / "src/doeff_agents/sessionhost/substrate.hy"

# 許す 2 点(head, name)。name=None は「名前は問わない」。
ALLOWED: tuple[tuple[str, str | None], ...] = (
    ("deff", "ensure-symlink-outcome"),  # 置き換える据え付け: 一意な仮 + rename
    ("FsLinkArtifact", None),  # 置き換えない敷設: symlink + EEXIST の読み直し
)
# 据え付けの綴り 2 つを、reader が割った後の形で名指す。
BANNED_ATTR = ("os", "symlink")  # (. os symlink)
BANNED_METHOD = "symlink_to"  # ((. None symlink_to) obj target)


def _walk(form: models.Object) -> Iterator[models.Object]:
    yield form
    if isinstance(form, models.Sequence):
        for child in form:
            yield from _walk(child)


def _head_of(form: models.Object) -> str | None:
    if isinstance(form, models.Sequence) and len(form):
        first = form[0]
        if isinstance(first, models.Symbol):
            return str(first)
    return None


def _name_of(form: models.Object) -> str | None:
    if isinstance(form, models.Sequence) and len(form) > 1:
        second = form[1]
        if isinstance(second, models.Symbol):
            return str(second)
    return None


def _allowed_ranges(forms: list[models.Object]) -> list[tuple[str, int, int]]:
    ranges: list[tuple[str, int, int]] = []
    for top in forms:
        for form in _walk(top):
            head, name = _head_of(form), _name_of(form)
            for want_head, want_name in ALLOWED:
                if head == want_head and (want_name is None or name == want_name):
                    ranges.append((want_name or want_head, form.start_line, form.end_line))
    return ranges


def _install_sites(forms: list[models.Object]) -> list[tuple[str, int]]:
    """据え付けの綴りの出現を、行番号つきで拾う。

    どちらも ``(. …)`` の形に割られているので、属性の並びで見分ける。
    呼び出しでなく参照(関数を手渡す形)でも当たる — 物理は同じだから。
    """
    hits: list[tuple[str, int]] = []
    for top in forms:
        for form in _walk(top):
            if not isinstance(form, models.Expression) or not len(form):
                continue
            head = form[0]
            if not isinstance(head, models.Symbol) or str(head) != ".":
                continue
            parts = [str(x) for x in form[1:]]
            if parts == list(BANNED_ATTR):
                hits.append(("os.symlink", form.start_line))
            elif parts and parts[-1] == BANNED_METHOD:
                hits.append((".symlink_to", form.start_line))
    return hits


def _sites_outside_the_two_verbs(source: str, filename: str) -> list[tuple[str, int]]:
    forms = list(read_many(source, filename=filename))
    ranges = _allowed_ranges(forms)
    return [
        (spelling, line)
        for spelling, line in _install_sites(forms)
        if not any(start <= line <= end for _, start, end in ranges)
    ]


def test_the_two_verbs_are_the_only_place_a_symlink_is_installed() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    forms = list(read_many(source, filename=str(SOURCE)))

    # 対照の前提: 許した 2 つの動詞が現に在り、据え付けの綴りも現に在る
    # (どちらかが消えていれば下の 0 件は無意味になる)。
    names = {name for name, _, _ in _allowed_ranges(forms)}
    assert names == {"ensure-symlink-outcome", "FsLinkArtifact"}, names
    assert _install_sites(forms), "据え付けの綴りが 1 つも無い — 検査が空振りしている"

    outside = _sites_outside_the_two_verbs(source, str(SOURCE))
    assert outside == [], (
        "symlink の据え付けが許した 2 つの動詞の外に在る: "
        f"{outside} — 据え付けは ensure-symlink-outcome(置き換える)か "
        "FsLinkArtifact(置き換えない)のどちらかへ畳むこと。物理が逆なので "
        "綴りは 2 つ在るが、3 つ目は無い。"
    )


def test_a_third_hand_rolled_install_turns_the_check_red() -> None:
    # わざと違反させる: 動詞の外に 3 つ目の据え付けを置く。
    violating = (
        "(deff ensure-symlink-outcome [link target]\n"
        "  (os.symlink target link))\n"
        "(defhandler real-substrate []\n"
        "  (FsLinkArtifact [source-path target-path]\n"
        "    (os.symlink source-path target-path))\n"
        "  (FsSomethingElse [link target]\n"
        "    (os.symlink target link)\n"
        "    (.symlink_to (Path link) target)))\n"
    )
    outside = _sites_outside_the_two_verbs(violating, "<violating>")
    assert [spelling for spelling, _ in outside] == ["os.symlink", ".symlink_to"], outside
