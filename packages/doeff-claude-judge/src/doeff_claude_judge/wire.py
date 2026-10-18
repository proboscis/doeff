"""Judge ⇄ Claude の構造化の答えの写し(純粋)。通信はしない。

Claude への 1 回の問い = system の文・利用者の文・答えの JSON Schema の 3 つ(`ClaudeQuery`)。運ぶ手段(Messages API の
output_config.format か、Claude Code の CLI の 1 回きりの実行の --system-prompt・標準入力・--json-schema)はこの module の外の handler が持ち、
ここはどちらでも同じ写しだけを持つ。

綴り:
- 利用者の文 = state を名の札で囲んだ本文(state が札の名にできる鍵の表なら欄ごとに <欄の名>…</欄の名>、それ以外は
  <state>…</state>。文字列はそのまま、他は JSON)と、名つきの問いの並び。
- noul の問い → 答えの欄 <問いの名> = number(はいの確率)。
- choice の問い → 答えの欄 <問いの名> = object(候補の id → number の確率・全部の候補が要る)。
- Schema は Messages API の構造化の答えが受ける部分だけを使う(minimum / maximum と配列の長さの制約は受けない)。範囲 0〜1 は
  説明の文で伝え、答えの読みで確かめる。choice を候補の数と同じ長さの配列でなく id の object にするのも同じ訳(配列の長さを
  Schema で縛れない・位置で候補を当てさせない)で、Verdict の probabilities(id → 確率)とも同じ形になる。
- score の問いと画像(Judge の assets)は写さない(JudgeError の unsupported)。

答えの読み: 答えが JSON でない・object でない・欄が欠ける・余る・数でない(真偽値も数と見ない)・有限でない・0〜1 の外の答えは、
Verdict を作らずに `MalformedAnswer` で返す。確率は答えの値のまま運ぶ(丸めない・足して 1 に直さない — 床と比べるのは呼び手で、
判定器の値そのもの)。
"""

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from doeff_system_one import Answer, Judge, JudgeError, Question, Verdict

SUPPORTED_KINDS: tuple[str, ...] = ("noul", "choice")

#: 欄の名をそのまま札の名にできる形(それ以外の鍵を持つ state は丸ごと <state> の JSON にする)。
TAG_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
STATE_TAG = "state"

SYSTEM = (
    "You are a careful judge. The user message holds the material to judge between tags, then a list of named questions. "
    "Judge only from that material.\n"
    "For a yes/no question, give the probability (a number from 0 to 1) that the correct answer is yes. Use the whole range: "
    "close to 0 when the material clearly fails the question, close to 1 when it clearly satisfies it, and near 0.5 only when "
    "it gives no clear evidence either way.\n"
    "For a choice question, give every listed option, by its exact id, the probability (a number from 0 to 1) that it is the "
    "right answer; the probabilities over the options of one question should add up to 1.\n"
    "Answer every question by its exact name."
)

NOUL_HEAD = "Yes/no questions (answer each with the probability that the answer is yes):"
CHOICE_HEAD = "Choice questions (answer each with a probability for every option id):"


@dataclass(frozen=True)
class ClaudeQuery:
    """Claude への 1 回の問いの部品。schema_json = 答えの JSON Schema の文(問いの名 → 欄)。"""

    system: str
    user: str
    schema_json: str


@dataclass(frozen=True)
class MalformedAnswer:
    """形の違う答え(Verdict を作らない)。question = 当たった問いの名(答え全体の形が違う時は None)。"""

    detail: str
    question: str | None = None

    def as_error(self) -> JudgeError:
        """handler が Judge の呼び手へ投げる形(doeff-jev の形の違う答えと同じ kind)。"""
        return JudgeError("malformed", self.detail)


def _refuse_unsupported(judge: Judge) -> None:
    """Claude の問いに写せない Judge(score の問い・画像)を、問いを作る前に断るため。"""
    if judge.assets:
        raise JudgeError("unsupported", "画像(assets)は Claude の問いの部品に写さない")
    unsupported = [name for name, question in judge.questions.items() if question.kind not in SUPPORTED_KINDS]
    if unsupported:
        raise JudgeError("unsupported", f"問い {unsupported} の種類は写さない(noul と choice だけ)")


def _block(tag: str, value: Any) -> str:
    """state の欄 1 つを、問いの文が名で指せる札(<tag>…</tag>)に囲むため。"""
    body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return f"<{tag}>\n{body}\n</{tag}>"


def state_text(state: Any) -> str:
    """state を名の札で囲んだ本文にする(札の名にできる鍵の表なら欄ごとの札、それ以外は <state> 1 つ)。"""
    if isinstance(state, Mapping) and state and all(
            isinstance(key, str) and TAG_NAME.match(key) for key in state):
        return "\n\n".join(_block(key, value) for key, value in state.items())
    return _block(STATE_TAG, state)


def _question_lines(name: str, question: Question) -> tuple[str, ...]:
    """問い 1 つを、答えの欄の名と同じ名で指す行(choice は候補の id の行つき)にするため。"""
    head = f"- {name}: {question.instructions}"
    return (head, *(f"  - {option_id}: {text}" for option_id, text in question.options))


def _questions_text(judge: Judge) -> str:
    """問いの束を、種類ごとの見出しの下に並べた利用者の文の後ろ半分にするため。"""
    sections = (
        (head, tuple(line for name, question in judge.questions.items() if question.kind == kind
                     for line in _question_lines(name, question)))
        for head, kind in ((NOUL_HEAD, "noul"), (CHOICE_HEAD, "choice"))
    )
    return "\n\n".join("\n".join((head, *lines)) for head, lines in sections if lines)


def _field_schema(name: str, question: Question) -> dict[str, Any]:
    """問い 1 つの答えの欄の Schema を、Messages API の構造化の答えが受ける部分だけで書くため。"""
    if question.kind == "noul":
        return {"type": "number",
                "description": f"Probability from 0 to 1 that the answer to question {name} is yes."}
    return {
        "type": "object",
        "description": f"For question {name}: the probability from 0 to 1 that each option is the right answer.",
        "properties": {option_id: {"type": "number", "description": f"Probability from 0 to 1 for option {option_id}."}
                       for option_id in question.option_ids},
        "required": question.option_ids,
        "additionalProperties": False,
    }


def _answer_schema(judge: Judge) -> dict[str, Any]:
    """答え全体の Schema(問いの名ごとの欄・全部が要る・余りを許さない)を作るため。"""
    return {
        "type": "object",
        "properties": {name: _field_schema(name, question) for name, question in judge.questions.items()},
        "required": list(judge.questions),
        "additionalProperties": False,
    }


def prepare(judge: Judge) -> ClaudeQuery:
    """Judge を Claude への 1 回の問いの部品(system の文・利用者の文・答えの JSON Schema)に写す。"""
    _refuse_unsupported(judge)
    return ClaudeQuery(system=SYSTEM,
                       user=state_text(judge.state) + "\n\n" + _questions_text(judge),
                       schema_json=json.dumps(_answer_schema(judge), ensure_ascii=False))


def _probability(raw: object) -> float | None:
    """0〜1 の有限の数なら float、それ以外は None(真偽値は数と見ない)。値は丸めない。"""
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    value = float(raw)
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        return None
    return value


def _unexpected_keys(got: Mapping[str, object], expected: list[str]) -> list[str]:
    """答えに在る、問いの外の鍵(Schema が許さない余り)を見つけるため。"""
    return sorted(str(key) for key in got if key not in expected)


def _read_noul(name: str, raw: object) -> Answer | MalformedAnswer:
    """noul の欄の答えを、確率を値のまま持つ Answer にするため(確信度は doeff-jev の noul と同じ 0.5 からの距離 × 2)。"""
    value = _probability(raw)
    if value is None:
        return MalformedAnswer(f"問い {name} の答えが 0〜1 の数でない: {raw!r}", name)
    return Answer(kind="noul", value=value, confidence=abs(value - 0.5) * 2, raw={"answer": raw})


def _read_choice(name: str, option_ids: list[str], raw: object) -> Answer | MalformedAnswer:
    """choice の欄の答え(候補の id → 確率)を、候補ごとの確率を値のまま持つ Answer にするため。"""
    if not isinstance(raw, Mapping):
        return MalformedAnswer(f"問い {name} の答えが候補の表でない: {raw!r}", name)
    missing = [option_id for option_id in option_ids if option_id not in raw]
    if missing:
        return MalformedAnswer(f"問い {name} の答えに候補 {missing} が無い", name)
    extra = _unexpected_keys(raw, option_ids)
    if extra:
        return MalformedAnswer(f"問い {name} の答えに問いの外の候補 {extra} が在る", name)
    read = {option_id: _probability(raw[option_id]) for option_id in option_ids}
    bad = [option_id for option_id in option_ids if read[option_id] is None]
    if bad:
        return MalformedAnswer(
            f"問い {name} の候補 {bad} の答えが 0〜1 の数でない: {[raw[option_id] for option_id in bad]!r}", name)
    probabilities = {option_id: value for option_id, value in read.items() if value is not None}
    # 選んだ候補 = 確率の最も高い候補(同じ確率なら並べた順で先の候補)。確信度はその確率。
    chosen = max(option_ids, key=lambda option_id: probabilities[option_id])
    return Answer(kind="choice", value=chosen, confidence=probabilities[chosen],
                  probabilities=probabilities, raw={"answer": dict(raw)})


def _read(name: str, question: Question, raw: object) -> Answer | MalformedAnswer:
    """欄 1 つの答えを、問いの種類に合う読みへ渡すため。"""
    if question.kind == "noul":
        return _read_noul(name, raw)
    return _read_choice(name, question.option_ids, raw)


def parse(judge: Judge, answer: object, *, model: str = "", input_tokens: int = 0,
          elapsed_ms: int = 0) -> Verdict | MalformedAnswer:
    """構造化の答え(JSON の文か、読んだ値)を Verdict に写す。形が違えば MalformedAnswer(問いの順で最初の違い)。

    model・input_tokens・elapsed_ms は運ぶ handler が知る値で、Verdict にそのまま載せる。
    """
    _refuse_unsupported(judge)
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except ValueError:
            return MalformedAnswer(f"答えが JSON でない: {answer[:120]!r}")
    if not isinstance(answer, Mapping):
        return MalformedAnswer(f"答えが問いの名の表でない: {str(answer)[:120]!r}")
    names = list(judge.questions)
    extra = _unexpected_keys(answer, names)
    if extra:
        return MalformedAnswer(f"答えに問いの外の欄 {extra} が在る")
    missing = [name for name in names if name not in answer]
    if missing:
        return MalformedAnswer(f"答えに問い {missing[0]} の欄が無い", missing[0])
    reads = {name: _read(name, question, answer[name]) for name, question in judge.questions.items()}
    failed = [read for read in reads.values() if isinstance(read, MalformedAnswer)]
    if failed:
        return failed[0]
    return Verdict(answers={name: read for name, read in reads.items() if isinstance(read, Answer)},
                   model=model, input_tokens=int(input_tokens), elapsed_ms=int(elapsed_ms))
