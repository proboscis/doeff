"""判断の道具(純粋・通信を知らない)。

答えをどう読むかは呼び手の仕事だが、繰り返し踏む形はここに 1 度だけ書く。
"""

from collections.abc import Callable

from doeff_system_one.types import Answer

#: state に載せる文の目安(字)。超えると確信度が落ちる(jevadr の実測 2026-09-19: 2,000 字を
#: 越えた窓は点数が下がるのではなく確信度が 0 になる = 判定器が答えを出せない)。
STATE_CHARS_ADVISED = 2_000


def answered(answer: Answer | None, floor: float) -> bool:
    """答えが出ているか。**確信度が無い / 床の下 = 判定できない**(合格でも違反でもない)。"""
    return (answer is not None and answer.confidence is not None
            and answer.confidence >= floor)


def crosses(answer: Answer | None, *, value_floor: float, confidence_floor: float) -> bool:
    """値と確信度の両方が床を越えたか(score / noul 用)。答えが出ていなければ False。"""
    if not answered(answer, confidence_floor):
        return False
    assert answer is not None
    try:
        return float(answer.value) >= value_floor
    except (TypeError, ValueError):
        return False


def chosen(answer: Answer | None, *, floor: float, none_label: str | None = None) -> str | None:
    """choice の答えを「採る / 採らない」に畳む。確信度が床の下、または「どれでもない」なら None。"""
    if not answered(answer, floor):
        return None
    assert answer is not None
    value = answer.value
    if value is None or (none_label is not None and value == none_label):
        return None
    return str(value)


def calibration_ok(bad: Answer | None, good: Answer | None,
                   crosses_fn: Callable[[Answer], bool], floor: float) -> bool:
    """使う前に判定器を測る。違反する例(bad)が越え、守る例(good)が越えず、**両方が答えを
    出せている**こと。「正例が床を越えなければ合格」だけだと、何を渡しても『判定できない』を
    返す判定器が校正を通る(jevadr の実弾 2026-09-19)。"""
    return (answered(bad, floor) and answered(good, floor)
            and bool(crosses_fn(bad)) and not bool(crosses_fn(good)))  # type: ignore[arg-type]


def clip(text: object, limit: int = STATE_CHARS_ADVISED) -> str:
    """state に載せる文を上限で切る(先頭を残す)。"""
    if text is None:
        return ""
    value = str(text)
    return value if len(value) <= limit else value[:limit]
