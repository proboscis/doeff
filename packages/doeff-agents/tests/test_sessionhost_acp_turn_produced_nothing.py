"""出力 0 件で終わった温かい手番は completed を名乗らない(依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB・D1 / D2 / D3 / D5-1)。

実弾(2026-09-19 07:28 JST・pod agentd-pool-7949bb78df-j4bz4・aj-9AHT1RWPYNTTWEZBWRNN0R34T6): ``--resume`` で起きた CLI が
孤児の task の報せを自分の手番として走らせ、器がその result で本文の手番を切った。agentd の turn-end の腕は結末を無条件に
completed と貼ったので、出力 0 件の手番と働いた手番が結末の型で区別できず、ACP は郵便を「読まれた」「組み直さない」で
消費した(24 時間で 42 件)。根(CLI 自身の手番の result の取り違え)は headless_protocol の CLI_OWN_TURN_ORIGINS で直し、
この検は**同じ形が別の経路で起きた時に黙って completed を名乗らない**網を押さえる:

- 判断の 1 点(judgment.turn-output-condition-of / outcome-with-output-condition)の表。
- fake の World で同じ program(agentd.hy)を一周させ、温かい手番の結末を読む。

card acp:kanban-issue:ki-ef537db05f7f(2026-09-19 実弾 aj-6EKERTYDCD4MC666PGPVA9R9HA): 再起動の後に行から拾い直した手番は、
材料の読み始めが**手番の始まりではなく拾い直した拍の file の大きさ**なので、再起動の前に書かれた出力を読めない。その 0 を
「出さなかった」と読むと、答え終えた(`ai tell` を 2 回撃って result success で終えた)手番が failed / TurnProducedNothing に
なり、ACP の配達が同じ郵便でもう一度手番を作る = 二重回答。判断を 3 値(出した / 出さなかった / 測れていない)にして、
測れていない手番は cause を変えず条件 TurnOutputUnmeasured だけ残す。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import hy  # noqa: F401  # registers the .hy importer
from doeff_agents.sessionhost.acp import judgment
from doeff_agents.sessionhost.acp.effects import (
    CONDITION_TURN_OUTPUT_UNMEASURED,
    CONDITION_TURN_PRODUCED_NOTHING,
    JOB_STEP_RECORD_END,
    JOB_STEP_TURN_END,
    PHASE_ENDED,
    DeltaBatch,
    EntryKind,
    JobOutcome,
    JSONObject,
    TurnEntryHeadline,
)
from test_sessionhost_acp import (
    HOMES,
    World,
    bound_job,
    mailed,
    message,
    transcript_line,
)

from doeff import run

SOURCE = "transcript"
PATH = "/h/claude/acct/projects/-work/s.jsonl"
USAGE: JSONObject = {"input": 3, "output": 7}


def _headline(kind: EntryKind, seq: int) -> TurnEntryHeadline:
    return TurnEntryHeadline(seq=seq, at=1_000, kind=kind, bytes=10, sha256="0" * 64)


def _batch(kinds: tuple[EntryKind, ...] = (), usage: JSONObject | None = None) -> DeltaBatch:
    entries = tuple(_headline(kind, seq) for seq, kind in enumerate(kinds, start=1))
    return DeltaBatch(frames=(), entries=entries, usage=usage, next_seq=len(kinds) + 1, model=None)


def _nothing(
    batch: DeltaBatch,
    step: str = JOB_STEP_TURN_END,
    source: str | None = SOURCE,
    path: str | None = PATH,
    turn_error: str | None = None,
    covers: bool = True,
) -> JSONObject | None:
    return run(
        judgment.turn_output_condition_of(step, source, path, batch, turn_error, covers)
    )


def test_a_warm_turn_end_with_no_output_and_no_usage_is_the_nothing_condition() -> None:
    """実弾の形(system の見出しが 2 本・0 本・1 本 — どれも出力 0 件・usage 無し)は条件を 1 項返す。根拠(見出しの数・
    system の数・usage の無さ)を文に残す(Hard rule 5 — 理由を黙って落とさない)。"""
    shapes: tuple[tuple[EntryKind, ...], ...] = (("system", "system"), (), ("system",))
    for kinds in shapes:
        condition = _nothing(_batch(kinds))
        assert condition is not None, kinds
        assert condition["type"] == CONDITION_TURN_PRODUCED_NOTHING
        assert condition["status"] == "True"
        reason = str(condition["reason"])
        assert f"({len(kinds)} headlines, {kinds.count('system')} system" in reason
        assert "no usage" in reason
        assert "the model was never called" in reason
        assert "ended without an error" in reason


def test_the_runners_failure_words_reach_the_condition() -> None:
    """D2: 器が名乗った手番の失敗の文(turn_error)は条件の文に運ばれる(失敗の旗を落とさない)。"""
    condition = _nothing(_batch(("system",)), turn_error="No conversation found with session ID: s")
    assert condition is not None
    assert "the runner said the turn failed: No conversation found with session ID: s" in str(
        condition["reason"]
    )


def test_any_output_or_usage_or_an_unreadable_source_is_not_nothing() -> None:
    """何か出した手番(text / tool_use / tool_result のどれか・usage だけでも)は条件を返さない。材料の path が無い器は
    「読めない」であって「何も出していない」ではない。温かい手番の終わり以外の step は判じない。"""
    assert _nothing(_batch(("system", "text"))) is None
    assert _nothing(_batch(("tool_use",))) is None
    assert _nothing(_batch(("tool_result",))) is None
    assert _nothing(_batch(("system",), usage=USAGE)) is None
    assert _nothing(_batch(("system", "error"), usage=USAGE)) is None
    assert _nothing(_batch(()), source=None) is None
    assert _nothing(_batch(()), path=None) is None
    assert _nothing(_batch(()), step=JOB_STEP_RECORD_END) is None


def test_only_a_completed_cause_is_replaced_by_turn_produced_nothing() -> None:
    """D3: completed だけを failed / TurnProducedNothing に置き換える。取り消し・停止・限度・器の失敗の cause は上書きしない
    (決定的な理由や合図を「手番が走らなかった」側へ畳まない — 畳むと ACP が組み直してしまう)。"""
    condition = _nothing(_batch(("system", "system")))
    assert condition is not None
    done = JobOutcome(ended=True, result=None, cause={"category": "completed"}, conditions=())
    replaced = run(judgment.outcome_with_output_condition(done, condition))
    assert replaced.cause == {"category": "failed", "reason": CONDITION_TURN_PRODUCED_NOTHING}
    assert replaced.conditions == (condition,)
    assert run(judgment.outcome_with_output_condition(done, None)) == done
    kept: tuple[JSONObject, ...] = (
        {"category": "cancelled", "stage": "graceful", "reason": "operator"},
        {"category": "failed", "reason": "ProviderLimit"},
        {"category": "failed", "reason": "SessionFailed"},
        {"category": "agentd-stopped", "reason": "drain-deadline"},
        {"category": "interrupted", "reason": "withdrawn"},
    )
    for cause in kept:
        other = JobOutcome(ended=True, result=None, cause=cause, conditions=())
        assert run(judgment.outcome_with_output_condition(other, condition)) == other, cause


# ---------------------------------------------------------------- 一周(fake の World・同じ agentd.hy)


@dataclass(frozen=True)
class WarmSession:
    """手番 1 を終えた温かい session(器の id と transcript の path)。"""

    sid: str
    path: str


def _first_warm_turn(world: World) -> WarmSession:
    """手番 1: launch(multi_turn)→ 本文への応答 → 手番の終わり(completed)。"""
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    sid = world.sid("j-1")
    path = f"{HOMES}/claude/acct/projects/-work/{sid}.jsonl"
    world.local.transcripts[path] = transcript_line("assistant", [{"type": "text", "text": "one"}])
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(sid, world.local.now_ms + 500)
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    return WarmSession(sid=sid, path=path)


def _second_turn_status(
    world: World, warm: WarmSession, answer: str | None, turn_error: str | None = None
) -> JSONObject:
    """手番 2(温かい session への send): answer が None なら、材料は進む(CLI 自身の手番の init の行 — 実弾では孤児の報せ・
    init・result の行が材料を進めた)が本文への応答は 1 行も無いまま手番が終わる(実弾の形)。材料が進まない手番は
    agentd が手番の終わりと読まない(progressed-of)ので、進みは実弾どおりに作る。"""
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"], created_at_ms=world.local.now_ms + 700))
    world.tick(advance_ms=1_000)
    assert world.sessions.sends[-1] == (warm.sid, mailed("m-2", "second"), True)
    if answer is not None:
        world.local.transcripts[warm.path] += transcript_line(
            "assistant", [{"type": "text", "text": answer}]
        )
    else:
        world.local.transcripts[warm.path] += (
            json.dumps({"type": "system", "subtype": "init", "session_id": warm.sid}) + "\n"
        )
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(warm.sid, world.local.now_ms + 200, turn_error=turn_error)
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    job = world.job("j-2")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    return job.status


def _conditions(status: JSONObject) -> list[JSONObject]:
    conditions = status.get("conditions", [])
    assert isinstance(conditions, list)
    return [item for item in conditions if isinstance(item, dict)]


def test_a_warm_turn_that_produced_nothing_ends_failed_not_completed() -> None:
    """Hard rule 10 の検(この欠陥を捕まえたはずの検): 温かい session の手番が本文に 1 行も答えずに終わった → agent-job は
    Ended・cause {failed, TurnProducedNothing}・条件 TurnProducedNothing(旧形は cause completed・条件 0 本)。session は生かす。"""
    world = World()
    warm = _first_warm_turn(world)
    first = world.job("j-1").status
    assert first is not None
    assert first["result"] == {"cause": {"category": "completed"}}
    status = _second_turn_status(world, warm, answer=None)
    assert status["result"] == {
        "cause": {"category": "failed", "reason": CONDITION_TURN_PRODUCED_NOTHING}
    }
    nothing = [
        item for item in _conditions(status) if item["type"] == CONDITION_TURN_PRODUCED_NOTHING
    ]
    assert len(nothing) == 1
    assert "no usage" in str(nothing[0]["reason"])
    assert world.sessions.views[warm.sid].status == "running"
    assert world.sessions.cleanups == []


def test_a_warm_turn_that_failed_before_the_model_carries_the_runners_words() -> None:
    """D2: 器が手番の失敗を名乗った(turn_error)出力 0 件の手番は、その文を条件に運ぶ(旧形は multi_turn の失敗の旗を捨てていた)。"""
    world = World()
    warm = _first_warm_turn(world)
    status = _second_turn_status(world, warm, answer=None, turn_error="error_during_execution")
    assert status["result"] == {
        "cause": {"category": "failed", "reason": CONDITION_TURN_PRODUCED_NOTHING}
    }
    (nothing,) = [
        item for item in _conditions(status) if item["type"] == CONDITION_TURN_PRODUCED_NOTHING
    ]
    assert "the runner said the turn failed: error_during_execution" in str(nothing["reason"])


def test_a_warm_turn_that_answered_stays_completed() -> None:
    """対照: 本文に答えた温かい手番は今日どおり completed(手番自身の結末 — Hard rule 7 は触らない)。"""
    world = World()
    warm = _first_warm_turn(world)
    status = _second_turn_status(world, warm, answer="two")
    assert status["result"] == {"cause": {"category": "completed"}}
    assert not [
        item for item in _conditions(status) if item["type"] == CONDITION_TURN_PRODUCED_NOTHING
    ]


# ---------------------------------------------------------------- card ki-ef537db05f7f: 測れていない ≠ 出していない


def test_material_that_does_not_cover_the_turn_is_unmeasured_not_nothing() -> None:
    """材料がこの手番を覆っていない(covers が False = 再起動の後に行から拾い直し、読み始めが『拾い直した拍の file の
    大きさ』になった手番)なら、出力 0 件は **測れていない** であって『出さなかった』ではない。条件の型が分かれ、
    理由には覆っていない事実が書かれる(根拠を黙って落とさない)。"""
    condition = _nothing(_batch(("system", "system")), covers=False)
    assert condition is not None
    assert condition["type"] == CONDITION_TURN_OUTPUT_UNMEASURED
    assert condition["status"] == "True"
    reason = str(condition["reason"])
    assert "does not cover the turn" in reason
    assert "picked up from its row after a restart" in reason
    assert "unmeasured, not zero" in reason
    assert "(2 headlines, 2 system, 0 text / tool_use / tool_result, no usage)" in reason
    assert "ended without an error" in reason
    # 器が名乗った失敗の文は、覆っていない側にも運ぶ(D2 はこの枝でも生きる)。
    said = _nothing(_batch(()), covers=False, turn_error="error_during_execution")
    assert said is not None
    assert "the runner said the turn failed: error_during_execution" in str(said["reason"])


def test_output_read_from_material_that_does_not_cover_the_turn_is_still_output() -> None:
    """覆いの検は**出力の検の後**: 覆っていない材料でも、その中に見出しか usage が在れば『出した』は確かに読めた
    (None = 何か出した)。覆いを先に見ると、答えを読めた手番まで測れていない側へ落ちる。"""
    assert _nothing(_batch(("text",)), covers=False) is None
    assert _nothing(_batch(("tool_use",)), covers=False) is None
    assert _nothing(_batch(("system",), usage=USAGE), covers=False) is None
    # 温かい手番の終わり以外・材料の在らない器は、覆いに依らず今日どおり何も言わない。
    assert _nothing(_batch(()), covers=False, step=JOB_STEP_RECORD_END) is None
    assert _nothing(_batch(()), covers=False, path=None) is None


def test_the_unmeasured_condition_never_changes_the_cause() -> None:
    """★ この便の本体: TurnOutputUnmeasured は cause を 1 bit も変えず、条件だけ足す。failed へ倒すと ACP の配達が
    一過性として同じ郵便で手番を作り直し、答え終えた手番の答えが 2 度出る(実弾の被害そのもの)。"""
    unmeasured = _nothing(_batch(("system",)), covers=False)
    assert unmeasured is not None
    done = JobOutcome(ended=True, result=None, cause={"category": "completed"}, conditions=())
    carried = run(judgment.outcome_with_output_condition(done, unmeasured))
    assert carried.cause == {"category": "completed"}
    assert carried.conditions == (unmeasured,)
    # 取り消し・限度の cause も変えない(条件だけ足す — 測れなかった事実は残す)。
    limited = JobOutcome(
        ended=True, result=None, cause={"category": "failed", "reason": "ProviderLimit"}, conditions=()
    )
    kept = run(judgment.outcome_with_output_condition(limited, unmeasured))
    assert kept.cause == limited.cause
    assert kept.conditions == (unmeasured,)


def _restarted_turn_status(world: World, warm: WarmSession, answer: str | None) -> JSONObject:
    """手番 2(温かい session への send)の途中で agentd が再起動し、走っている行から手番を拾い直して閉じる
    (実弾のログの逐語: ``agentd: recovered running job … from its row (turn-end)``)。answer は再起動の**前**に
    材料へ書く — 拾い直しの読み始めはその後ろなので、agentd はもうその出力を読めない。"""
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"], created_at_ms=world.local.now_ms + 700))
    world.tick(advance_ms=1_000)
    assert world.sessions.sends[-1] == (warm.sid, mailed("m-2", "second"), True)
    if answer is not None:
        world.local.transcripts[warm.path] += transcript_line(
            "assistant", [{"type": "text", "text": answer}]
        )
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(warm.sid, world.local.now_ms + 200)
    world.restart()
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    job = world.job("j-2")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    return job.status


def test_a_turn_recovered_after_a_restart_does_not_claim_the_output_it_cannot_read() -> None:
    """★ 一周の検(実弾 aj-6EKERTYDCD4MC666PGPVA9R9HA の形): 温かい session の手番が答えを書いた**後**に agentd が
    再起動して行から拾い直すと、拾い直しの読み始めはその答えの後ろなので材料は空に見える。その手番は
    failed / TurnProducedNothing にしない(= ACP は同じ郵便で手番を作り直さない)。結末は completed のまま、
    測れなかった事実は条件 TurnOutputUnmeasured で行に残る。"""
    world = World()
    warm = _first_warm_turn(world)
    status = _restarted_turn_status(world, warm, answer="answered before the restart")
    assert status["result"] == {"cause": {"category": "completed"}}
    assert not [
        item for item in _conditions(status) if item["type"] == CONDITION_TURN_PRODUCED_NOTHING
    ]
    (unmeasured,) = [
        item for item in _conditions(status) if item["type"] == CONDITION_TURN_OUTPUT_UNMEASURED
    ]
    assert "does not cover the turn" in str(unmeasured["reason"])
    # 答えを 1 行も書かないまま再起動した手番も同じ扱い(材料からはどちらか分からない — 測れていない)。
    world2 = World()
    warm2 = _first_warm_turn(world2)
    silent = _restarted_turn_status(world2, warm2, answer=None)
    assert silent["result"] == {"cause": {"category": "completed"}}
    assert [
        item for item in _conditions(silent) if item["type"] == CONDITION_TURN_OUTPUT_UNMEASURED
    ]


def test_a_recovered_turn_that_reads_from_the_head_of_the_file_is_still_measured() -> None:
    """対照: 拾い直しでも **file の頭から読む腕**(launch — その手番自身が session を起こした)は材料がこの手番を
    覆うので、出力 0 件は今日どおり failed / TurnProducedNothing(測れているものを測れていない側へ逃がさない)。"""
    world = World()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    sid = world.sid("j-1")
    path = f"{HOMES}/claude/acct/projects/-work/{sid}.jsonl"
    world.local.transcripts[path] = (
        json.dumps({"type": "system", "subtype": "init", "session_id": sid}) + "\n"
    )
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(sid, world.local.now_ms + 200)
    world.restart()
    world.tick(advance_ms=1_000)
    status = world.job("j-1").status
    assert status is not None
    assert status["phase"] == PHASE_ENDED
    assert status["result"] == {
        "cause": {"category": "failed", "reason": CONDITION_TURN_PRODUCED_NOTHING}
    }
    assert [
        item for item in _conditions(status) if item["type"] == CONDITION_TURN_PRODUCED_NOTHING
    ]
    assert not [
        item for item in _conditions(status) if item["type"] == CONDITION_TURN_OUTPUT_UNMEASURED
    ]
