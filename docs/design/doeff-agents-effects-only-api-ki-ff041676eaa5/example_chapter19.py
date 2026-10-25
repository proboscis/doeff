"""章 19 の手順を、doeff-agents の effect の yield と handler を据える事だけで書いた例(設計の記録 design.md の節 4)。

起こす(Launch)・終わりまで見張って出力を読む(AwaitResult)・状態の変わり目に答える(Events を読み FollowUp で答える)・
止める(Stop)・並べて走らせる(doeff の Spawn と Gather)を、fake の Claude の runtime(process も API も使わない)の上で走らせ、
答えを assert で確かめる。time.sleep・polling の loop・callback・session_scope・launch_session は使わない。
本物の Claude で走らせる時は handlers() の fake_claude_agent_runtime_handlers を claude_agent_runtime_handlers に替えるだけ
(design.md の節 4.3)。

走らせ方(doeff の repo の根で): uv run python docs/design/doeff-agents-effects-only-api-ki-ff041676eaa5/example_chapter19.py
"""

import tempfile
from pathlib import Path

from doeff import Gather, Spawn, do, run, with_handlers
from doeff_core_effects.scheduler import scheduled
from doeff_time import SimClock, sim_time_handler

from doeff_agents import (
    AgentToolUseEvent,
    AgentTurnCompleted,
    AgentTurnFailed,
    AgentType,
    AwaitResult,
    Events,
    FollowUp,
    Launch,
    Stop,
    fake_claude_agent_runtime_handlers,
)

# 設計の後は `from doeff_agents import FakeReply`(fake の handler の引数の値として handlers から出す — names.md の節 7)。
# 今の main では組み立ての module に在るので、ここだけ今の置き場から読む。
from doeff_agents.handlers.headless_compose import FakeReply


@do
def review(name: str, work_dir: Path, diff: str):
    """1 つの diff を agent に review させ、review の文を返すため(章 19 の Step 1・2)。"""
    handle = yield Launch(name, agent_type=AgentType.CLAUDE, work_dir=str(work_dir),
                          prompt=f"Review this diff:\n{diff}")          # 起こす
    try:
        outcome = yield AwaitResult(handle, timeout_seconds=600.0)    # 終わりまで見張る(待つのは handler の中)
        return outcome.result                                         # 出力を読む(AwaitOutcome.result — ADR-DOE-AGENTS-001 R2)
    finally:
        yield Stop(handle)                                            # 止める


@do
def review_with_retry(name: str, work_dir: Path, diff: str):
    """手番の出来事を読み、失敗した手番には 1 度だけ頼み直すため(章 19 の Step 3 — callback の代わり)。"""
    handle = yield Launch(name, agent_type=AgentType.CLAUDE, work_dir=str(work_dir),
                          prompt=f"Review this diff:\n{diff}")
    try:
        seq, tool_calls, retried = -1, 0, False
        while True:
            page = yield Events(handle, after_seq=seq, wait_seconds=30.0)  # 次の出来事か手番の終わりまで handler の中で待つ
            seq = page.next_seq
            tool_calls += sum(len(e.tool_calls) for e in page.events if isinstance(e, AgentToolUseEvent))
            match page.end:
                case None:
                    continue                                              # 手番がまだ走っている
                case AgentTurnFailed() if not retried:
                    retried = True
                    yield FollowUp(handle, "The previous turn failed. Please review again.")  # 状態の変わり目に答える
                case AgentTurnCompleted(result_text=text):
                    return (text, tool_calls, retried)
                case other:
                    raise RuntimeError(f"review {name} ended without a review: {other!r}")
    finally:
        yield Stop(handle)


@do
def review_all(work_dir: Path, diffs: tuple[tuple[str, str], ...]):
    """diff ごとの review を並べて走らせ、全部の答えを入力の順で返すため(章 19 の Step 4・5 — asyncio.gather の代わり)。"""
    tasks = ()
    for name, diff in diffs:
        tasks += ((yield Spawn(review(name, work_dir, diff))),)
    return (yield Gather(*tasks))


def responder(text: str, memory: tuple[str, ...]) -> FakeReply:
    """fake の agent の返事を決めるため: 最初の頼みが fail-once を含めば失敗の手番、ほかは頼みの最後の行への review の文。"""
    if "fail-once" in text and not memory:
        return FakeReply("", fail="the fake agent failed")
    return FakeReply(f"reviewed: {text.splitlines()[-1]}")


def handlers() -> list:
    """Program の外に据える handler の組を 1 か所で作るため(時計 → fake の Claude の runtime の順 — 外側が先)。"""
    return [sim_time_handler(clock=SimClock()),
            *fake_claude_agent_runtime_handlers(responder=responder, env={}, settings={})]


def main() -> None:
    """2 つの Program を fake の runtime の上で走らせ、答えを確かめるため。"""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        parallel = run(scheduled(with_handlers(handlers(), review_all(work, (("a", "diff A"), ("b", "diff B"))))))
        print("parallel:", parallel)
        assert list(parallel) == ["reviewed: diff A", "reviewed: diff B"], parallel
        retried = run(scheduled(with_handlers(handlers(), review_with_retry("c", work, "fail-once diff C"))))
        print("retry:", retried)
        assert retried[0].startswith("reviewed: "), retried
        assert retried[2] is True, retried


if __name__ == "__main__":
    main()
