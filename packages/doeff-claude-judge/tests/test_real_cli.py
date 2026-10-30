"""本物の claude に 1 回出す判定(印 e2e・slow)— 読む形 5 万字の見本 1 通の判定が答えを返すまでの秒を測る。

本番と同じ積み(claude_judge_stack の本番の print の handler)で、変えていない CLI の print mode(`--json-schema`)を 1 回走らせる。資格は借りた
資格の形で渡す: env DOEFF_CLAUDE_JUDGE_E2E_CREDENTIALS(CLI の .credentials.json の path)から access token と期限だけを読み、
その process だけの設定 dir(検の tmp の下)の .credentials.json に置く(refreshToken は運ばない)。走るのはその env と PATH の claude が
在る時だけ — 日次と着地の門の -m "not e2e" では走らない(本物の利用枠を使う)。

- 本文: env DOEFF_CLAUDE_JUDGE_E2E_DOCUMENT(読む形の本文の file)が在ればその本文、無ければ検が組む 5 万字の見本(架空の設計の文書)。
- model: env DOEFF_CLAUDE_JUDGE_E2E_MODEL(既定 claude-sonnet-5-5)・thinking の token 数: env DOEFF_CLAUDE_JUDGE_E2E_THINKING_TOKENS
  (既定は CLI の既定 — Haiku 4.5 は 0 で考えを外す)。
- 秒は pytest の記録の欄 seconds(junit の property)と標準出力の 1 行に出す。

走らせ方(doeff の repo の根で):
  DOEFF_CLAUDE_JUDGE_E2E_CREDENTIALS=$CLAUDE_CONFIG_DIR/.credentials.json \\
    uv run --no-sync pytest packages/doeff-claude-judge/tests/test_real_cli.py -m e2e -s
"""

import json
import math
import os
import shutil
import time
from pathlib import Path

import pytest
from doeff_claude_judge import (
    ClaudeJudgeTarget,
    JudgeCredential,
    claude_judge_stack,
    fixed_credential_lender,
)
from doeff_core_effects import scheduled
from doeff_system_one import Judge, Question, Verdict

from doeff import do, run, with_handlers

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

CREDENTIALS_ENV = "DOEFF_CLAUDE_JUDGE_E2E_CREDENTIALS"
DOCUMENT_ENV = "DOEFF_CLAUDE_JUDGE_E2E_DOCUMENT"
MODEL_ENV = "DOEFF_CLAUDE_JUDGE_E2E_MODEL"
THINKING_ENV = "DOEFF_CLAUDE_JUDGE_E2E_THINKING_TOKENS"
REAL_CLI = shutil.which("claude")
FIFTY_THOUSAND = 50_000
#: 1 回の判定の待ちの上限(CLI の起動と終わりの 4〜6 秒を含む — 素の CLI の測りで 5 万字の 1 判定は Sonnet 5.5 で約 7 秒)。
WAIT_SECONDS = 180.0

QUESTIONS = {
    "decision": Question(kind="noul", instructions="Does the document state, near its start, the one decision the reader is asked to make?"),
    "options": Question(kind="noul", instructions="Does the document give each option the reader can choose together with what follows from it?"),
    "undo": Question(kind="noul", instructions="Does the document say how the chosen option could be undone later?"),
    "terms": Question(kind="noul", instructions="Can a reader who has never seen the project follow the document without terms it never explains?"),
    "evidence": Question(kind="noul", instructions="Does the document back its claims with measured facts rather than impressions?"),
    "decision_where": Question(kind="choice", instructions="Which part of the document most clearly states the decision?", options=(("p0", "The first section"), ("p1", "A middle section"), ("p2", "The last section"), ("none", "No part states it"))),
}


def _lent_credential(path: str) -> JudgeCredential:
    """CLI の .credentials.json から access token と期限だけを読む(refreshToken は運ばない)。"""
    oauth = json.loads(Path(path).read_text(encoding="utf-8"))["claudeAiOauth"]
    expires_ms = oauth.get("expiresAt")
    return JudgeCredential(access_token=oauth["accessToken"],
                           expires_at=None if expires_ms is None else expires_ms / 1000,
                           subscription_type=oauth.get("subscriptionType"), rate_limit_tier=oauth.get("rateLimitTier"))


def _section(number: int) -> str:
    """架空の設計の文書の節 1 つ(節ごとに数と名が違う — 同じ文の繰り返しにしない)。"""
    region = ("north", "south", "east", "west", "central")[number % 5]
    rows = 1200 + number * 37
    minutes = 14 + number % 9
    return (
        f"## Section {number}: the {region} report batch\n\n"
        f"The nightly report batch for the {region} region reads {rows} rows from the order table and writes one summary "
        f"file per store. Last week the batch took {minutes} minutes on average, and on two nights it ran past the "
        f"morning cut-off, so the stores opened without their summary. The batch runs on the shared queue, where it "
        f"waits behind the billing jobs; the measured wait before it starts was between 3 and {minutes - 5} minutes.\n\n"
        f"Moving this batch to the new queue removes the wait, because the new queue runs only report jobs. The cost is "
        f"one more worker at about {40 + number % 7} dollars a month. If the move goes wrong, the batch can be moved "
        f"back by changing one line of the schedule file; the summaries already written stay where they are.\n\n"
        f"The owner of the {region} stores asked for the summaries by 6:00 local time. With the new queue, the "
        f"measured finish time over five test nights was between 4:{10 + number % 40:02d} and 4:{20 + number % 35:02d}.\n"
    )


def _synthetic_document() -> str:
    """5 万字を越える架空の設計の文書(冒頭で決める事を名指し、節ごとに測った数を持つ)。"""
    head = ("# Moving the nightly report batches to a queue of their own\n\n"
            "Decision asked of the reader: approve moving every regional report batch from the shared queue to a new "
            "report-only queue, at the cost of one more worker per region.\n\n")
    sections = []
    number = 0
    while len(head) + sum(len(section) for section in sections) < FIFTY_THOUSAND:
        sections = [*sections, _section(number)]
        number += 1
    return head + "\n".join(sections)


def _runner_environment() -> dict[str, str]:
    """検を走らせた人の env(e2e の入力の env を読む入口はここ 1 か所)。"""
    return dict(os.environ)  # noqa: DOEFF004 — 検が走らせた人の env から e2e の入力を読む 1 か所


def _document() -> str:
    given = _runner_environment().get(DOCUMENT_ENV)
    return Path(given).read_text(encoding="utf-8") if given else _synthetic_document()


def _child_env(home: Path) -> dict[str, str]:
    """子の CLI の env: PATH と検の HOME と、更新の確かめを止める値だけ(この process の資格・hook の印を継がせない)。"""
    return {"PATH": _runner_environment().get("PATH", "/usr/bin:/bin"), "HOME": str(home), "DISABLE_AUTOUPDATER": "1",
            "LANG": "C.UTF-8"}


@pytest.mark.skipif(REAL_CLI is None or not _runner_environment().get(CREDENTIALS_ENV),
                    reason=f"本物の claude と env {CREDENTIALS_ENV}(CLI の .credentials.json の path)が在る時だけ走る")
def test_a_fifty_thousand_character_document_is_judged_by_the_real_cli(tmp_path: Path, record_property) -> None:
    document = _document()
    assert len(document) >= FIFTY_THOUSAND
    environment = _runner_environment()
    thinking = environment.get(THINKING_ENV)
    target = ClaudeJudgeTarget(model=environment.get(MODEL_ENV, "claude-sonnet-5-5"), config_dir=str(tmp_path / "home"),
                               env=_child_env(tmp_path), thinking_tokens=None if thinking is None else int(thinking))
    judge = Judge(state={"document": document}, questions=QUESTIONS, timeout_seconds=WAIT_SECONDS, cacheable=False)

    @do
    def flow():
        return (yield judge)

    started = time.monotonic()
    lender = fixed_credential_lender(_lent_credential(environment[CREDENTIALS_ENV]))
    verdict = run(scheduled(with_handlers(claude_judge_stack(target, lender=lender), flow())))
    seconds = time.monotonic() - started
    record_property("seconds", round(seconds, 2))
    record_property("chars", len(document))
    print(f"claude-judge e2e: {len(document)} 字・model {verdict.model}・{seconds:.2f} 秒(CLI の process {verdict.elapsed_ms} ms)"
          f"・input {verdict.input_tokens} token・答え {[(name, answer.value) for name, answer in verdict.answers.items()]}")
    assert isinstance(verdict, Verdict)
    assert set(verdict.answers) == set(QUESTIONS)
    for name, question in QUESTIONS.items():
        answer = verdict.answers[name]
        if question.kind == "noul":
            assert math.isfinite(answer.value), (name, answer)
            assert 0.0 <= answer.value <= 1.0, (name, answer)
        else:
            assert set(answer.probabilities or {}) == set(question.option_ids), (name, answer)
    assert seconds < WAIT_SECONDS
    # 借りた資格の家は残らない。
    homes = tmp_path / "home" / ".credential-homes"
    assert not homes.exists() or list(homes.iterdir()) == []
