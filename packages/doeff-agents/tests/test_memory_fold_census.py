"""``memory_fold_census`` の検(card acp:kanban-issue:ki-554e364641e8 の望む状態 3)。

この読み手が守るのは 1 つ — **落ちを見逃さないこと**。手番の行の条件は落ちた冊の数を運ばず、
行も終わって 300 秒で刈られるので、落ちた冊の数はこの勘定が唯一の証拠になる。
⇒ 検は「和に足してはいけない欄を足すと落ちが消える」を
正面から撃つ。
"""

from __future__ import annotations

from doeff_agents.sessionhost.acp.memory_fold_census import (
    Beat,
    Census,
    Generation,
    MalformedLine,
    beat_of,
    census_of,
    reading_of_line,
    readings_of_lines,
    report,
)

#: 会社 Mac CA-20038667 の log から採った実物(2026-09-22)。この拍は **この検を書いた
#: 会話自身**が記憶を 1 冊 失った拍で、``unreadable`` が同じ行に立っている。
REAL_GENERATION_1 = (
    '{"metric":"agent-memory-folded","agentJobId":"aj-FAEKVPVWRY1G5P00AG3HCZRJCE",'
    '"conversationId":"c-3TFDMGKEY64EFRC9FBBK688SHF","books":9,"written":8,"unreadable":1}'
)
#: 同じ log の世代 2 の破れ。全欄が 0 で ``books`` だけ 1 = 1 冊まるごと書けなかった拍。
REAL_GENERATION_2 = (
    '{"metric":"agent-memory-folded","agentJobId":"aj-VWG7RMCFK48XBQT1DGKENE99XM",'
    '"conversationId":"c-7JDE43NBYXA05BVBN6QFGXSPHP","books":1,"written":0,"unchanged":0,'
    '"unbased":0,"conflicted":0,"founded":0,"unreadable":0}'
)


def _census(*lines: str) -> Census:
    return census_of(readings_of_lines(lines), "(検)")


def test_the_three_way_split_that_adds_up_loses_nothing() -> None:
    """``written + unchanged + unbased == books`` の拍は落ちを 1 つも出さない。"""
    census = _census(
        '{"metric":"agent-memory-folded","conversationId":"c-A","books":5,'
        '"written":1,"unchanged":3,"unbased":1}'
    )
    assert census.lost_books == 0
    assert census.broken_beats == 0
    assert census.overcounted_beats == 0


def test_a_missing_column_reads_as_zero_so_the_old_shape_needs_no_second_formula() -> None:
    """旧世代(``unchanged`` / ``unbased`` の欄が無い)でも式は 1 つ。

    欠けた欄を 0 と読むと ``books == written`` へ縮む ⇒ 世代ごとに式を持たなくてよい。
    """
    beat = beat_of({"metric": "agent-memory-folded", "books": 9, "written": 8, "unreadable": 1})
    assert beat.generation is Generation.WRITTEN_ONLY
    assert beat.unchanged == 0
    assert beat.unbased == 0
    assert beat.lost == 1, "旧世代で books - written が落ちにならなかった"


def test_conflicted_is_not_added_to_the_sum_or_the_loss_disappears() -> None:
    """⭐ ``conflicted`` は ``written`` の**部分集合**なので和に足さない。

    足すと二重に数えて残差が縮み、**現に失われた冊が 0 件に見える**。
    出典 = ``agentd.hy`` の検 ``…-moved-under-the-turn…`` が ``written`` 1 と
    ``conflicted`` 1 を同じ拍で撃つ(衝突しても頭の上に重ねて書けている)。
    """
    line = (
        '{"metric":"agent-memory-folded","conversationId":"c-A","books":2,'
        '"written":1,"unchanged":0,"unbased":0,"conflicted":1}'
    )
    census = _census(line)
    assert census.lost_books == 1, "conflicted を和に足して落ちを見逃した"

    beat = beat_of(
        {"metric": "agent-memory-folded", "books": 2, "written": 1, "unchanged": 0, "unbased": 0, "conflicted": 1}
    )
    assert beat.books - (beat.written + beat.unchanged + beat.unbased + 1) == 0, (
        "この検体は conflicted を足すと残差が 0 になる形であること(検自身の前提)"
    )


def test_revived_and_founded_are_subsets_too() -> None:
    """``revived`` ⊂ ``written`` ・ ``founded`` ⊂ ``unchanged``。どちらも和に足さない。"""
    census = _census(
        '{"metric":"agent-memory-folded","conversationId":"c-A","books":3,"written":1,'
        '"unchanged":1,"unbased":0,"revived":1,"founded":1,"retired":0,"vanished":0}'
    )
    assert census.lost_books == 1, "revived / founded を和に足して落ちを見逃した"


def test_columns_outside_books_never_move_the_count() -> None:
    """``unreadable`` / ``retired`` / ``vanished`` は ``books`` の外。

    実物で撃つ: ``books 9 / written 8 / unreadable 1`` は落ち **1**。
    ``unreadable`` を和に入れると 0 になり、現に失われた冊が消える。
    """
    census = _census(REAL_GENERATION_1)
    assert census.lost_books == 1
    assert census.by_conversation["c-3TFDMGKEY64EFRC9FBBK688SHF"].books == 1

    retirement = _census(
        '{"metric":"agent-memory-folded","conversationId":"c-A","books":1,"written":1,'
        '"unchanged":0,"unbased":0,"retired":4,"vanished":4,"revived":0}'
    )
    assert retirement.lost_books == 0, "撤回の冊を books の内と読んだ"


def test_a_whole_book_that_never_landed_is_counted() -> None:
    """全欄 0 で ``books`` だけ立つ拍 = 1 冊まるごと書けなかった(実物)。"""
    census = _census(REAL_GENERATION_2)
    assert census.lost_books == 1
    assert census.broken_beats == 1
    assert census.by_conversation["c-7JDE43NBYXA05BVBN6QFGXSPHP"].beats == 1


def test_an_unknown_column_is_named_so_the_formula_can_be_revisited() -> None:
    """知らない欄が出たら名乗る。

    ``written`` / ``unchanged`` の部分集合でない新しい「成功」の欄が増えると、この式は
    落ちを過大に数える ⇒ 欄が増えた日に気づける必要がある。
    """
    census = _census(
        '{"metric":"agent-memory-folded","conversationId":"c-A","books":2,"written":1,'
        '"unchanged":0,"unbased":0,"deferred":1}'
    )
    assert census.unknown_fields == {"deferred": 1}
    assert census.lost_books == 1, "知らない欄を黙って和に入れた"


def test_a_sum_over_books_is_not_counted_as_loss() -> None:
    """和が ``books`` を超えた拍は式が壊れている ⇒ 落ちに混ぜず別に名乗る。"""
    census = _census(
        '{"metric":"agent-memory-folded","conversationId":"c-A","books":1,"written":2,'
        '"unchanged":1,"unbased":0}'
    )
    assert census.overcounted_beats == 1
    assert census.lost_books == 0
    assert census.broken_beats == 0


def test_losses_are_grouped_by_conversation_worst_first() -> None:
    """会話ごとの内訳が出る(断りの log 行には会話が載らないので、ここが唯一の引き口)。"""
    census = _census(
        '{"metric":"agent-memory-folded","conversationId":"c-A","books":1,"written":0,"unchanged":0,"unbased":0}',
        '{"metric":"agent-memory-folded","conversationId":"c-B","books":5,"written":0,"unchanged":0,"unbased":0}',
        '{"metric":"agent-memory-folded","conversationId":"c-A","books":2,"written":0,"unchanged":0,"unbased":0}',
    )
    assert list(census.by_conversation) == ["c-B", "c-A"], "失った冊の多い順に並んでいない"
    assert census.by_conversation["c-A"].books == 3
    assert census.by_conversation["c-A"].beats == 2
    assert census.lost_books == 8


def test_a_log_line_with_a_prefix_is_read() -> None:
    """log の行は計器の JSON の前に時刻などが付く。"""
    reading = reading_of_line(
        "2026-09-22T00:12:34Z agentd " + REAL_GENERATION_2
    )
    assert isinstance(reading, Beat)
    assert reading.books == 1


def test_a_line_that_is_not_the_metric_is_dropped() -> None:
    assert reading_of_line("agentd: memory foo of conversation c-A was not written back") is None
    assert reading_of_line('{"metric":"agent-turn-ended","books":3}') is None


def test_a_broken_metric_line_is_named_not_silently_dropped() -> None:
    """計器の行に見えて読めない行は、落ちと混ぜず別に数える。"""
    census = _census('{"metric":"agent-memory-folded","books":')
    assert census.malformed_lines == 1
    assert census.beats == 0
    assert census.lost_books == 0


def test_the_report_names_the_population_it_counted() -> None:
    """数えた値を母集団なしで引用させない。

    計器は機体ごとの log にしか出ず、pod では再起動で消える ⇒ どの機体の何日ぶんかを
    言わない数は艦隊の実数と読まれてしまう。
    """
    census = census_of(readings_of_lines([REAL_GENERATION_1]), "CA-20038667 の log(10 日ぶん)")
    text = report([census])
    assert "CA-20038667 の log(10 日ぶん)" in text
    assert "失われた記憶 = 1" in text

    two = report([census, census])
    assert "上に挙げた母集団の和ちょうど" in two, "合計を艦隊の実数と読める形で出した"


def test_a_malformed_line_and_a_beat_are_different_readings() -> None:
    """読みは判別可能な union。網羅して扱う。"""
    readings = readings_of_lines([REAL_GENERATION_1, '{"metric":"agent-memory-folded","books":'])
    assert [type(r) for r in readings] == [Beat, MalformedLine]
