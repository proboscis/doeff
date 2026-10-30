"""問いの型と純粋な判断の道具。"""

import pytest
from doeff_system_one import (
    Judge,
    Question,
    answer_choice,
    answer_noul,
    answer_score,
)
from doeff_system_one._judgment import answered, calibration_ok, chosen, clip, crosses


def test_question_payload() -> None:
    assert Question(kind="choice", instructions="which", options=(("a", "a"), ("b", "b"))).as_payload() == {"type": "choice", "instructions": "which", "criteria": {"a": "a", "b": "b"}}
    described = Question(kind="choice", instructions="which", options=(("zsh", "glob が当たらない"), ("none", "どれでもない")))
    assert described.as_payload()["criteria"] == {"zsh": "glob が当たらない", "none": "どれでもない"}
    assert described.option_ids == ["zsh", "none"]
    assert Question(kind="noul", instructions="is it").as_payload() == {"type": "noul", "instructions": "is it"}
    assert Question(kind="score", instructions="how", levels=("lo", "mid", "hi")).as_payload() == {"type": "score", "instructions": "how", "criteria": ["lo", "mid", "hi"]}


def test_question_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError, match="noul は候補も段階も"):
        Question(kind="noul", instructions="x", levels=("a",))
    with pytest.raises(ValueError, match="choice は候補"):
        Question(kind="choice", instructions="x")
    with pytest.raises(ValueError, match="score は段階"):
        Question(kind="score", instructions="x", options=(("a", "a"),))
    with pytest.raises(ValueError, match="kind は"):
        Question(kind="other", instructions="x")  # type: ignore[arg-type]


def test_judge_requires_questions_of_the_right_type() -> None:
    with pytest.raises(ValueError, match="問いが 1 つ以上"):
        Judge(state="s", questions={})
    with pytest.raises(TypeError):
        Judge(state="s", questions={"q": {"type": "noul"}})  # type: ignore[dict-item]
    effect = Judge(state={"x": 1}, questions={"q": Question(kind="noul", instructions="is"), "c": Question(kind="choice", instructions="w", options=(("a", "a"),))}, cacheable=False)
    assert effect.kinds() == {"q": "noul", "c": "choice"}
    assert effect.cacheable is False


def test_answered_treats_missing_or_low_confidence_as_undecided() -> None:
    assert not answered(None, 0.25)
    assert not answered(answer_score(1.9, confidence=None), 0.25)
    assert not answered(answer_score(1.9, confidence=0.1), 0.25)
    assert answered(answer_score(1.9, confidence=0.6), 0.25)


def test_noul_confidence_is_distance_from_half() -> None:
    assert answer_noul(0.5).confidence == pytest.approx(0.0)
    assert answer_noul(0.97).confidence == pytest.approx(0.94)
    assert answer_noul(0.1).confidence == pytest.approx(0.8)


def test_crosses_needs_both_floors() -> None:
    assert crosses(answer_score(1.9, confidence=0.9), value_floor=1.5, confidence_floor=0.25)
    assert not crosses(answer_score(1.2, confidence=0.9), value_floor=1.5, confidence_floor=0.25)
    assert not crosses(answer_score(1.9, confidence=0.1), value_floor=1.5, confidence_floor=0.25)


def test_chosen_returns_value_only_when_confident_and_not_none_label() -> None:
    assert chosen(answer_choice("zsh-glob", confidence=0.9), floor=0.5, none_label="none") == "zsh-glob"
    assert chosen(answer_choice("none", confidence=0.9), floor=0.5, none_label="none") is None
    assert chosen(answer_choice("zsh-glob", confidence=0.3), floor=0.5) is None


def test_calibration_requires_both_examples_answered_and_separated() -> None:
    hits = lambda a: float(a.value) >= 1.5  # noqa: E731
    assert calibration_ok(answer_score(1.9, 0.9), answer_score(0.1, 0.9), hits, floor=0.25)
    assert not calibration_ok(answer_score(1.9, 0.0), answer_score(0.1, 0.9), hits, floor=0.25)
    assert not calibration_ok(answer_score(1.9, 0.9), answer_score(1.8, 0.9), hits, floor=0.25)


def test_clip_keeps_head() -> None:
    assert clip("abcdef", 3) == "abc"
    assert clip(None) == ""
    assert clip("ab", 3) == "ab"


def test_a_usage_limit_is_a_judge_error_of_its_own_kind() -> None:
    # 判定器の口座の利用枠の尽き(card ki-eb4f8e1db64d): 呼び手は JudgeError のまま受けられ、型で尽きを分けて戻る刻を読める。
    from doeff_system_one import JudgeError, JudgeUsageLimited

    limited = JudgeUsageLimited("You've hit your session limit", window="five_hour", resets_at=1791342000)
    assert isinstance(limited, JudgeError)
    assert (limited.kind, limited.window, limited.resets_at, limited.status) == ("usage_limit", "five_hour", 1791342000, 429)
    unnamed = JudgeUsageLimited("limit", window=None, resets_at=None)
    assert (unnamed.window, unnamed.resets_at) == (None, None)
    for bad in ({"window": "", "resets_at": None}, {"window": None, "resets_at": True}, {"window": None, "resets_at": 1.5}):
        with pytest.raises((TypeError, ValueError)):
            JudgeUsageLimited("limit", **bad)
