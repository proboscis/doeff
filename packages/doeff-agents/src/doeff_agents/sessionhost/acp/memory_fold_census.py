"""計器 ``agent-memory-folded`` から「書けなかった記憶の冊」を数える読み手。

card acp:kanban-issue:ki-554e364641e8 の望む状態 3。畳み戻しが記録の service へ書けなかった
拍は、手番の行(agent-job)の条件 ``AgentMemoryUnwritable`` を立てるだけで手番を落とさない。
その条件は落ちた**冊の数**を運ばない(型ごとに 1 つ・最初の理由だけ —
``judgment.memory-unwritable-noted``)うえ、手番の行は終わってから 300 秒で刈られる
(ACP の ``AgentJob`` の回収)。⇒ 条件は「落ちた手番」の短命な印で、落ちた**冊の数**は
**この計器の勘定の破れだけ**が証拠になる。その破れを数えるのがこの module。

## 恒等式

置き場の 1 冊は、``agentd.fold-memories`` の中でちょうど 1 つの結末に落ちる:

===============  ==========================================================
``written``      記録の service へ本文を書けた
``unchanged``    本文が動いていないので書かない(基準は据わっている)
``unbased``      基準が無いので触らない
===============  ==========================================================

⇒ **``books == written + unchanged + unbased``** ⇒ **残差 = 書こうとして書けなかった冊。**

次の 3 つは上の**部分集合**なので、和に足すと二重に数えて落ちを見逃す:

===============  ==============  ==========================================
``conflicted``   ⊂ ``written``   行が手番の下で動いた拍。頭の上に重ねて書けている
``revived``      ⊂ ``written``   退役した行に別の本文が現れた拍。本文を重ねる
``founded``      ⊂ ``unchanged`` 規則 1 の拍で基準を据えた冊。撃ってはいない
===============  ==============  ==========================================

``books`` の**外**(和にも残差にも関わらない): ``unreadable`` は冊として読めなかった file の数
(``MEMORY.md`` を含む)、``retired`` / ``vanished`` は母集団が別(基準に在って置き場に無い名)で
本文を 1 byte も書かない。

## 世代で式を分けない

欄は 3 世代ある(``books``/``written``/``unreadable`` → + ``unchanged``/``founded``/``unbased``/
``conflicted`` → + ``retired``/``revived``/``vanished``)。⚠ **欠けた欄を 0 と読めば、旧世代では
式が自動的に ``books == written`` へ縮む** ⇒ 世代の判定は報告の内訳のためだけで、勘定には要らない。
旧世代は「毎回すべての冊を書き直す版」なのでこれが正しい式になる(会社 Mac の実測 2026-09-22:
旧 398 拍のうち 380 拍が ``written == books``・比の中央値 1.000)。

## 欄が増えた日に気づく

``written`` / ``unchanged`` の部分集合でない新しい「成功」の欄が増えると、この式は落ちを
**過大に**数える。⇒ :attr:`Census.unknown_fields` が知らない欄を名乗り、和が ``books`` を
超えた拍は :attr:`Census.overcounted_beats` に分けて落ちには数えない。

## 入力

入力は「行の列」ちょうど。log でも、記録の side へ計器が出るようになった後でも同じ読み手が
使える(card acp:kanban-issue:ki-718f00f87d5c が観測の側を持つ)。

⚠ **数えた値は必ず母集団と一緒に読む**: 計器は機体ごとの log にしか出ず、pod では再起動で
log が丸ごと消える(実測 2026-09-22: agentd-pool-0 が 90 分の観測中に 1 度再起動し、
前の container の 1,532 行は ``--previous`` からも消えた)。:attr:`Census.source` を必ず名乗ること。
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

#: 計器の名(``MetricLine`` の metric — stdout の JSON 行)。
METRIC = "agent-memory-folded"

#: 和に入る 3 つ。``books`` を分割する(排他)。
PARTITION: tuple[str, ...] = ("written", "unchanged", "unbased")
#: 上の部分集合。和に足さない。
SUBSETS: tuple[str, ...] = ("conflicted", "revived", "founded")
#: ``books`` の外。和にも残差にも関わらない。
OUTSIDE: tuple[str, ...] = ("unreadable", "retired", "vanished")
#: 冊の勘定でない欄。
LABELS: tuple[str, ...] = ("metric", "agentJobId", "conversationId", "books")

#: この module が意味を知っている欄。これ以外が出たら名乗る。
KNOWN: frozenset[str] = frozenset(PARTITION + SUBSETS + OUTSIDE + LABELS)


class Generation(StrEnum):
    """計器が名乗る欄の組。勘定には使わず、報告の内訳にだけ使う。"""

    #: ``books`` / ``written`` / ``unreadable`` だけ。毎手番すべての冊を書き直す版。
    WRITTEN_ONLY = "1 (books/written/unreadable)"
    #: + ``unchanged`` / ``founded`` / ``unbased`` / ``conflicted``。
    WITH_BASELINE = "2 (+unchanged/founded/unbased/conflicted)"
    #: + ``retired`` / ``revived`` / ``vanished``。置き場を端として読む版。
    WITH_RETIREMENT = "3 (+retired/revived/vanished)"


@dataclass(frozen=True)
class Beat:
    """計器 1 行 = 畳み戻し 1 拍。欠けた欄は 0(= その世代に無い欄)。"""

    conversation_id: str
    job_id: str
    books: int
    written: int
    unchanged: int
    unbased: int
    generation: Generation
    #: この module が意味を知らない欄の名(和に入るか確かめる合図)。
    unknown_fields: tuple[str, ...]

    @property
    def residual(self) -> int:
        """恒等式の残差。正なら書けなかった冊の数、負なら式が壊れている。"""
        return self.books - (self.written + self.unchanged + self.unbased)

    @property
    def lost(self) -> int:
        """この拍で書こうとして書けなかった冊の数(負は 0 に倒す)。"""
        return max(0, self.residual)


@dataclass(frozen=True)
class MalformedLine:
    """計器の行に見えて読めなかった行。落ちと混ぜず、別に数える。"""

    reason: str


#: 1 行の読みの結末。
LineReading = Beat | MalformedLine


@dataclass(frozen=True)
class ConversationLoss:
    """1 つの会話が失った冊と、それが起きた拍の数。"""

    beats: int
    books: int


@dataclass(frozen=True)
class Census:
    """1 つの母集団(1 機体・1 つの log)の勘定。"""

    #: どの機体・どの log を数えたか。値だけを引用させないための名乗り。
    source: str
    beats: int
    by_generation: Mapping[Generation, int]
    #: 恒等式が破れた拍の数。
    broken_beats: int
    #: 失われた冊の合計。
    lost_books: int
    #: 和が ``books`` を超えた拍(式の見直しが要る合図)。落ちには数えない。
    overcounted_beats: int
    by_conversation: Mapping[str, ConversationLoss]
    unknown_fields: Mapping[str, int]
    #: 計器の行に見えて読めなかった行。
    malformed_lines: int


def _int_field(fields: Mapping[str, object], key: str) -> int:
    value = fields.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def generation_of(fields: Mapping[str, object]) -> Generation:
    """欄の顔ぶれから世代を読む。"""
    if "retired" in fields:
        return Generation.WITH_RETIREMENT
    if "unchanged" in fields:
        return Generation.WITH_BASELINE
    return Generation.WRITTEN_ONLY


def beat_of(fields: Mapping[str, object]) -> Beat:
    """計器 1 行(dict)を拍に読む。純関数。"""
    conversation = fields.get("conversationId")
    job = fields.get("agentJobId")
    return Beat(
        conversation_id=conversation if isinstance(conversation, str) and conversation else "(名乗りなし)",
        job_id=job if isinstance(job, str) else "",
        books=_int_field(fields, "books"),
        written=_int_field(fields, "written"),
        unchanged=_int_field(fields, "unchanged"),
        unbased=_int_field(fields, "unbased"),
        generation=generation_of(fields),
        unknown_fields=tuple(sorted(set(fields) - KNOWN)),
    )


def reading_of_line(line: str) -> LineReading | None:
    """log の 1 行を読む。計器の行でなければ ``None``。純関数。"""
    if METRIC not in line:
        return None
    start = line.find('{"metric"')
    if start < 0:
        start = line.find("{")
    if start < 0:
        return MalformedLine(reason="JSON の始まりが無い")
    try:
        fields = json.loads(line[start:])
    except json.JSONDecodeError as exc:
        return MalformedLine(reason=f"JSON として読めない: {exc.msg}")
    if not isinstance(fields, dict):
        return MalformedLine(reason="JSON object でない")
    if fields.get("metric") != METRIC:
        return None
    return beat_of(fields)


def readings_of_lines(lines: Iterable[str]) -> tuple[LineReading, ...]:
    """行の列 → 読みの列。計器でない行は落とす。"""
    out: list[LineReading] = []
    for line in lines:
        reading = reading_of_line(line)
        if reading is not None:
            out.append(reading)
    return tuple(out)


def census_of(readings: Iterable[LineReading], source: str) -> Census:
    """読みの列 → 落ちの勘定。純関数(入出力なし)。"""
    beats = 0
    malformed = 0
    broken = 0
    lost = 0
    overcounted = 0
    by_generation: dict[Generation, int] = {}
    by_conversation: dict[str, ConversationLoss] = {}
    unknown: dict[str, int] = {}

    for reading in readings:
        match reading:
            case MalformedLine():
                malformed += 1
            case Beat() as beat:
                beats += 1
                by_generation[beat.generation] = by_generation.get(beat.generation, 0) + 1
                for name in beat.unknown_fields:
                    unknown[name] = unknown.get(name, 0) + 1
                if beat.residual < 0:
                    overcounted += 1
                    continue
                if beat.residual == 0:
                    continue
                broken += 1
                lost += beat.lost
                prior = by_conversation.get(beat.conversation_id)
                by_conversation[beat.conversation_id] = ConversationLoss(
                    beats=(prior.beats if prior else 0) + 1,
                    books=(prior.books if prior else 0) + beat.lost,
                )

    return Census(
        source=source,
        beats=beats,
        by_generation=dict(sorted(by_generation.items())),
        broken_beats=broken,
        lost_books=lost,
        overcounted_beats=overcounted,
        by_conversation=dict(sorted(by_conversation.items(), key=lambda kv: (-kv[1].books, kv[0]))),
        unknown_fields=dict(sorted(unknown.items())),
        malformed_lines=malformed,
    )


def report(censuses: Sequence[Census]) -> str:
    """人が読む報告。母集団を必ず名乗る。純関数。"""
    lines: list[str] = []
    total_beats = 0
    total_broken = 0
    total_lost = 0
    conversations: dict[str, ConversationLoss] = {}

    for census in censuses:
        lines.append(f"=== {census.source} ===")
        lines.append(f"  保存の回数 = {census.beats}")
        for generation, count in census.by_generation.items():
            lines.append(f"    計器の世代 {generation.value}: {count} 回")
        lines.append(
            f"  恒等式の破れ = {census.broken_beats} 回   失われた記憶 = {census.lost_books}   "
            f"会話 = {len(census.by_conversation)}"
        )
        if census.overcounted_beats:
            lines.append(
                f"  ⚠ 内訳の和が全体を超えた回 = {census.overcounted_beats}"
                "(新しい欄が増えた可能性 — 式の見直しが要ります)"
            )
        if census.unknown_fields:
            lines.append(f"  ⚠ 知らない欄 = {census.unknown_fields}(和に入るか確かめてください)")
        if census.malformed_lines:
            lines.append(f"  ⚠ 読めなかった計器の行 = {census.malformed_lines}")
        for conversation, loss in census.by_conversation.items():
            lines.append(f"    {conversation}  失われた記憶 {loss.books} / {loss.beats} 回")
            prior = conversations.get(conversation)
            conversations[conversation] = ConversationLoss(
                beats=(prior.beats if prior else 0) + loss.beats,
                books=(prior.books if prior else 0) + loss.books,
            )
        total_beats += census.beats
        total_broken += census.broken_beats
        total_lost += census.lost_books
        lines.append("")

    if len(censuses) > 1:
        lines.append("=== 合計 ⚠ 上に挙げた母集団の和ちょうどです(読めなかった機械は入っていません)===")
        lines.append(
            f"  保存の回数 {total_beats}   破れ {total_broken} 回   "
            f"失われた記憶 {total_lost}   会話 {len(conversations)}"
        )
    return "\n".join(lines)


def _main(paths: Sequence[str]) -> int:
    import sys

    if not paths:
        print(report([census_of(readings_of_lines(sys.stdin), "(標準入力)")]))
        return 0
    censuses: list[Census] = []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as handle:
            censuses.append(census_of(readings_of_lines(handle), path))
    print(report(censuses))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
