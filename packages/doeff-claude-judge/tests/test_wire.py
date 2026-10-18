"""Judge ⇄ Claude の構造化の答えの写し(純粋)。問いの部品の形と、答えの読みと、形の違う答え。"""

import ast
import json
import math
from pathlib import Path

import pytest
from doeff_claude_judge import ClaudeQuery, MalformedAnswer, parse, prepare, state_text
from doeff_system_one import Answer, Image, Judge, JudgeError, Verdict, choice, noul, score

QUESTIONS = {
    "s-d": noul("Does the opening say what the operator must decide?"),
    "s-d_where": choice("Which place most clearly fails s-d?",
                        {"p0": "First line of the opening", "p1": "Second line", "none": "None of these places"}),
}
JUDGE = Judge(state={"document": "依頼書の本文\n2 行目"}, questions=QUESTIONS)

# Messages API の構造化の答えが受けない Schema の語(claude-api の文書 tool-use-concepts の JSON Schema Limitations)。
UNSUPPORTED_SCHEMA_WORDS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                            "minLength", "maxLength", "minItems", "maxItems"}


def _objects(node: object) -> list[dict]:
    """Schema の中の全部の object の節を集めるため(語の禁止と additionalProperties を全部の節で確かめる)。"""
    if isinstance(node, dict):
        return [node, *(found for value in node.values() for found in _objects(value))]
    if isinstance(node, list):
        return [found for value in node for found in _objects(value)]
    return []


def _answer(outcome: Verdict | MalformedAnswer, name: str) -> Answer:
    """答えの読みが Verdict を返し、問い name の答えを持つ事を確かめてから、その答えを取るため。"""
    assert isinstance(outcome, Verdict)
    answer = outcome.answer(name)
    assert answer is not None
    return answer


def test_prepare_builds_system_user_and_schema() -> None:
    query = prepare(JUDGE)
    assert isinstance(query, ClaudeQuery)
    assert "probability" in query.system
    assert query.user.startswith("<document>\n依頼書の本文\n2 行目\n</document>\n\n")
    assert "- s-d: Does the opening say what the operator must decide?" in query.user
    assert "- s-d_where: Which place most clearly fails s-d?" in query.user
    assert "  - p1: Second line" in query.user
    assert "  - none: None of these places" in query.user
    schema = json.loads(query.schema_json)
    assert schema["type"] == "object"
    assert schema["required"] == ["s-d", "s-d_where"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["s-d"]["type"] == "number"
    where = schema["properties"]["s-d_where"]
    assert where["type"] == "object"
    assert list(where["properties"]) == ["p0", "p1", "none"]
    assert where["required"] == ["p0", "p1", "none"]
    assert where["additionalProperties"] is False
    assert all(field["type"] == "number" for field in where["properties"].values())


def test_schema_stays_inside_what_the_messages_api_accepts() -> None:
    nodes = _objects(json.loads(prepare(JUDGE).schema_json))
    used = {word for node in nodes for word in node} & UNSUPPORTED_SCHEMA_WORDS
    assert used == set()
    assert all(node["additionalProperties"] is False for node in nodes if node.get("type") == "object")


def test_state_text_tags_each_field_or_wraps_the_whole_state() -> None:
    assert state_text({"document": "本文", "meta": {"n": 1}}) == '<document>\n本文\n</document>\n\n<meta>\n{\n  "n": 1\n}\n</meta>'
    assert state_text("素の文") == "<state>\n素の文\n</state>"
    assert state_text({"two words": "x"}) == '<state>\n{\n  "two words": "x"\n}\n</state>'


def test_parse_carries_the_sample_probabilities_unrounded() -> None:
    sample = {"s-d": 0.596, "s-d_where": {"p0": 0.6049999, "p1": 0.3, "none": 0.0950001}}
    verdict = parse(JUDGE, json.dumps(sample), model="claude-sonnet-5-5", input_tokens=812, elapsed_ms=7031)
    assert isinstance(verdict, Verdict)
    assert (verdict.model, verdict.input_tokens, verdict.elapsed_ms) == ("claude-sonnet-5-5", 812, 7031)
    noul_answer = _answer(verdict, "s-d")
    assert noul_answer.kind == "noul"
    assert noul_answer.value == 0.596
    assert noul_answer.confidence == pytest.approx(abs(0.596 - 0.5) * 2)
    where = _answer(verdict, "s-d_where")
    assert where.kind == "choice"
    assert where.probabilities == {"p0": 0.6049999, "p1": 0.3, "none": 0.0950001}
    assert where.value == "p0"
    assert where.confidence == 0.6049999
    assert parse(JUDGE, sample, model="claude-sonnet-5-5", input_tokens=812, elapsed_ms=7031) == verdict


def test_parse_takes_both_ends_of_the_range_and_integers() -> None:
    verdict = parse(JUDGE, {"s-d": 1, "s-d_where": {"p0": 0, "p1": 0.0, "none": 1.0}})
    assert _answer(verdict, "s-d").value == 1.0
    assert _answer(verdict, "s-d_where").value == "none"


def test_choice_ties_go_to_the_option_listed_first() -> None:
    verdict = parse(JUDGE, {"s-d": 0.5, "s-d_where": {"p0": 0.2, "p1": 0.4, "none": 0.4}})
    assert _answer(verdict, "s-d_where").value == "p1"


@pytest.mark.parametrize(("answer", "question", "said"), [
    ({"s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.8}}, "s-d", "s-d"),
    ({"s-d": 0.7}, "s-d_where", "s-d_where"),
    ({"s-d": 0.7, "s-d_where": {"p0": 0.1, "none": 0.9}}, "s-d_where", "p1"),
    ({"s-d": 0.7, "s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.7, "p9": 0.1}}, "s-d_where", "p9"),
    ({"s-d": 0.7, "s-d_where": [0.1, 0.1, 0.8]}, "s-d_where", "s-d_where"),
    ({"s-d": "0.7", "s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.8}}, "s-d", "s-d"),
    ({"s-d": True, "s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.8}}, "s-d", "s-d"),
    ({"s-d": None, "s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.8}}, "s-d", "s-d"),
    ({"s-d": 0.7, "s-d_where": {"p0": "high", "p1": 0.1, "none": 0.8}}, "s-d_where", "p0"),
])
def test_missing_and_non_number_fields_are_malformed(answer: dict, question: str, said: str) -> None:
    outcome = parse(JUDGE, answer)
    assert isinstance(outcome, MalformedAnswer)
    assert outcome.question == question
    assert said in outcome.detail


@pytest.mark.parametrize(("answer", "question"), [
    ({"s-d": 1.2, "s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.8}}, "s-d"),
    ({"s-d": -0.01, "s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.8}}, "s-d"),
    ({"s-d": 0.7, "s-d_where": {"p0": 0.1, "p1": 1.5, "none": 0.8}}, "s-d_where"),
    ({"s-d": math.nan, "s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.8}}, "s-d"),
    ({"s-d": math.inf, "s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.8}}, "s-d"),
])
def test_out_of_range_numbers_are_malformed(answer: dict, question: str) -> None:
    outcome = parse(JUDGE, answer)
    assert isinstance(outcome, MalformedAnswer)
    assert outcome.question == question


def test_whole_answer_shape_errors_are_malformed() -> None:
    not_json = parse(JUDGE, "the document is fine")
    assert isinstance(not_json, MalformedAnswer)
    assert not_json.question is None
    assert isinstance(parse(JUDGE, "[0.7]"), MalformedAnswer)
    assert isinstance(parse(JUDGE, 0.7), MalformedAnswer)
    extra = parse(JUDGE, {"s-d": 0.7, "s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.8}, "other": 0.5})
    assert isinstance(extra, MalformedAnswer)
    assert "other" in extra.detail
    assert isinstance(parse(JUDGE, '{"s-d": NaN, "s-d_where": {"p0": 0.1, "p1": 0.1, "none": 0.8}}'), MalformedAnswer)


def test_malformed_answer_becomes_the_same_judge_error_kind_as_jev() -> None:
    outcome = parse(JUDGE, {"s-d": 0.7})
    assert isinstance(outcome, MalformedAnswer)
    error = outcome.as_error()
    assert isinstance(error, JudgeError)
    assert error.kind == "malformed"
    assert error.detail == outcome.detail


def test_score_questions_and_images_are_not_translated() -> None:
    with pytest.raises(JudgeError) as scored:
        prepare(Judge(state="s", questions={"s": score("how plain", ["no", "some", "all"])}))
    assert scored.value.kind == "unsupported"
    pictured = Judge(state={"poster": "p-1"}, questions={"n": noul("Is `poster` text only?")},
                     assets={"p-1": Image(format="png", data_base64="iVBORw0KGgo=")})
    with pytest.raises(JudgeError) as imaged:
        prepare(pictured)
    assert imaged.value.kind == "unsupported"


def test_the_translation_module_imports_no_transport() -> None:
    source = Path(__file__).parents[1] / "src" / "doeff_claude_judge" / "wire.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert imported <= {"json", "math", "re", "collections", "dataclasses", "typing", "doeff_system_one"}
