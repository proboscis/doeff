"""組み立て点の道具 — handler の積み方は 1 か所に書く。

外側から順に: scheduled → try → slog の出口(stderr / 捨てる)→ print の実体(本番 = doeff_claude_code の claude-print-handler・
検 = scripted_printer)→ 貸し手(BorrowJudgeCredential の答え手 — 使い手が渡す)→ claude-judge(Judge → print)→ program。
効果は内側から外へ流れる: program の Judge は claude-judge が受け、BorrowJudgeCredential を貸し手へ、ClaudePrintOnce を print の実体へ
出す。貸し手を渡さなければ、借りる effect はこの積みの外側の答え手へ抜ける(使い手の組み立てが自分の貸し手を外に積む時)。

使い手(Judge を Claude に答えさせる組み立て)は、この module と target の型だけを読み、doeff_claude_code の型を名指さない。
"""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

import hy  # noqa: F401  # .hy import hook — print の handler と fake は Hy の module doeff_claude_code に住む
from doeff_claude_code import ClaudePrintOnce, PrintAnswered, PrintLimited
from doeff_claude_code.fake import fake_claude_print_handler
from doeff_claude_code import AccountLimitHit, Usage
from doeff_claude_code.print_once import claude_print_handler
from doeff_core_effects import slog_discard_handler, slog_handler, try_handler
from doeff_core_effects.scheduler import scheduled
from doeff_hy.frozen import freeze_json

from doeff import Pass, Resume, do, run, with_handlers
from doeff import handler as _program_handler
from doeff.program import ProgramHandler
from doeff_claude_judge.handler import claude_judge_handler, query_user
from doeff_claude_judge.target import BorrowJudgeCredential, ClaudeJudgeTarget, JudgeCredential
from doeff_claude_judge.wire import ClaudeQuery

if TYPE_CHECKING:
    from doeff_hy.frozen import JsonIn  # 型の宣言(frozen.pyi)だけに在る JSON の値の別名

#: 起こす CLI(実行ファイルと前置きの引数)の既定 — PATH の claude。
DEFAULT_COMMAND: tuple[str, ...] = ("claude",)


def production_printer(command: Sequence[str] = DEFAULT_COMMAND) -> ProgramHandler:
    """本番の print の実体(CLI の process を 1 回起こす handler)を作るため。command = 実行ファイルと前置きの引数。"""
    return claude_print_handler(tuple(command))


def scripted_printer(answer: Callable[[ClaudeQuery, str], "JsonIn"],
                     limited: Callable[[ClaudeQuery, str], AccountLimitHit | None] | None = None) -> ProgramHandler:
    """使い手の検の print の実体: CLI を起こさず、answer(問いの部品, model) が返す構造化の答えで答える(fake)。

    limited(問いの部品, model) が尽きの事実(AccountLimitHit — 使い手は doeff_claude_judge から読む)を返す print は、answer を呼ばずに
    CLI が口座の利用枠の尽きを名乗って答えずに降りた答え(PrintLimited — 判定は JudgeUsageLimited)で答える(尽きた口座の筋書き)。
    None を返す print と、limited を渡さない時は answer で答える。
    """

    @do
    def respond(request: ClaudePrintOnce) -> Any:
        query = ClaudeQuery(system=request.system_prompt, user=query_user(request.input), schema_json=request.json_schema)
        hit = None if limited is None else limited(query, request.model)
        if hit is not None:
            return PrintLimited(limit=hit, exit_code=1, stderr_tail="")
        return PrintAnswered(structured_output=freeze_json(answer(query, request.model)), usage=Usage(), cost_usd=None, models=(),
                             elapsed_ms=0, api_ms=None)

    return fake_claude_print_handler(respond)


def fixed_credential_lender(credential: JudgeCredential) -> Callable[[Any], Any]:
    """どの BorrowJudgeCredential にも同じ資格で答える貸し手(手元の機体と本物に出す検 — 資格を 1 つ持つ時)。"""

    @do
    def lender(effect: Any, k: Any) -> Any:
        if not isinstance(effect, BorrowJudgeCredential):
            return (yield Pass(effect, k))
        return (yield Resume(k, credential))

    return _program_handler(lender)


def claude_judge_stack(
    target: ClaudeJudgeTarget, *,
    printer: Callable[[Any], Any] | None = None,
    lender: Callable[[Any], Any] | None = None,
    command: Sequence[str] = DEFAULT_COMMAND,
    log_stderr: bool = False,
) -> list[ProgramHandler]:
    """本番の handler の列(外側が先)。`printer` を差し替えると検の実体、`lender` は BorrowJudgeCredential の答え手。"""
    sink = slog_handler if log_stderr else slog_discard_handler
    return [try_handler, sink, printer if printer is not None else production_printer(command),
            *([] if lender is None else [lender]), claude_judge_handler(target)]


def run_judgment(program: Any, *, target: ClaudeJudgeTarget, layers: Sequence[Callable[[Any], Any]] | None = None,
                 **stack_kwargs: Any) -> Any:
    """program を本番の積みで走らせて結果を返す(手元の入口・本物に出す検)。`layers` を渡すとその積みをそのまま使う。"""
    stack = list(layers) if layers is not None else claude_judge_stack(target, **stack_kwargs)
    return run(scheduled(with_handlers(stack, program)))
