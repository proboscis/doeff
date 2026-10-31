"""Semgrep の検を表に載せる effect RegisterSemgrep と、semgrep を走らせて見本の当たり外れを確かめる effect AssertSemgrep と、
2 つに答える handler を返す工場 semgrep_enforcements。

決め: RegisterSemgrep と AssertSemgrep はどちらも effect にする
(ADR-DOE-API-001 R22)。本体は内側の registry._register_semgrep_enforcement・registry._assert_semgrep_enforcement に
移した。前の関数 register_semgrep_enforcement と assert_semgrep_enforcement は、外の repo の呼び手を effect へ書き換えた後に
(agora-controllers cef8e6e88a)消した。

handler は GetAdr の adr_registry と分ける: AssertSemgrep は外の process(semgrep)を起こすので、ADR を読むだけの program の
handler の組に semgrep の実行を混ぜない。macro defsemgrep の展開は、自分の載せと検をこの handler の下で走らせる(project の
doeff_interpreter fixture に頼らない — 外の repo の defsemgrep は doeff の版を上げるだけで動く)。test の中で別の表や別の
semgrep を使う時は、program の一部に semgrep_enforcements(table=…, semgrep=…) を据える: 内側の handler が先に答える。
"""

from dataclasses import KW_ONLY, dataclass
from os import PathLike, fspath

from doeff import EffectBase, K, Transfer, TransferThrow, handler
from doeff.program import ProgramHandler
from doeff_adr._registry import (
    EnforcementMode,
    EnforcementRef,
    FixtureSpec,
    SemgrepSpec,
    _assert_semgrep_enforcement,
    _register_semgrep_enforcement,
)


@dataclass(frozen=True)
class RegisterSemgrep(EffectBase[SemgrepSpec]):
    """Semgrep の検を表に載せる: yield RegisterSemgrep("id", rule_id=…, hit_fixtures=[…], clean_fixtures=[…]) → SemgrepSpec。

    据え付けの規則の形は rule_id と当たる見本(hit_fixtures)・当たらない見本(clean_fixtures)、その場の規則の形は pattern と
    bad・good。欄は defsemgrep の展開と同じ(declared_in = 宣言した file — 相対の config をその dir から読む)。同じ id が表に
    在る時と、pattern も rule_id も無い時は ValueError。
    """

    enforcement_id: str
    _: KW_ONLY
    pattern: str | None = None
    rule_id: str | None = None
    config: str = ".semgrep.yaml"
    declared_in: str | None = None
    expand_hy: bool = False
    languages: list[str] | tuple[str, ...] | None = None
    message: str = "ADR Semgrep enforcement failed"
    severity: str = "ERROR"
    bad: list[FixtureSpec] | tuple[FixtureSpec, ...] | None = None
    good: list[FixtureSpec] | tuple[FixtureSpec, ...] | None = None
    hit_fixtures: list[dict[str, str]] | tuple[dict[str, str], ...] | None = None
    clean_fixtures: list[dict[str, str]] | tuple[dict[str, str], ...] | None = None
    mode: EnforcementMode = "green"


@dataclass(frozen=True)
class AssertSemgrep(EffectBase[None]):
    """表の Semgrep の検を確かめる: yield AssertSemgrep("id") → None。semgrep を走らせ、規則が当たる見本に 1 つ以上当たり、
    当たらない見本に当たらなければ答える。外れ・semgrep が無い・走らなかった時は AssertionError、表に無い id は KeyError。"""

    enforcement_id: str


def semgrep_enforcements(
    *,
    table: dict[str, EnforcementRef | SemgrepSpec] | None = None,
    semgrep: str | PathLike[str] | None = None,
) -> ProgramHandler:
    """RegisterSemgrep と AssertSemgrep に答える handler。semgrep の process はこの handler が AssertSemgrep に答える時に起こす。

    table が無ければ process の登録簿(defadr・defsemgrep が載せる所)に載せて読み、table を渡せばその辞書に載せて読む
    (渡した辞書に無い id は外の登録簿へ落ちずに KeyError)。semgrep が無ければ答える時に PATH の semgrep を探し、渡せば
    その実行 file を起こす(無い path は AssertionError)。

    使い方: semgrep_enforcements()(program)・semgrep_enforcements(table={}, semgrep="/path/to/semgrep")(program)。
    """
    if table is not None and not isinstance(table, dict):
        raise TypeError(
            f"semgrep_enforcements(table=…) takes a dict from id to enforcement, got {type(table).__name__}"
        )
    executable = None if semgrep is None else fspath(semgrep)

    @handler
    def answer_semgrep(effect: RegisterSemgrep | AssertSemgrep, k: K):
        # 載せと検の決まった失敗は、effect を出した所へ投げ返す(先例 _effects の GetAdr と同じ形・構成のレビューの所見 5)。
        try:
            if isinstance(effect, RegisterSemgrep):
                answer: SemgrepSpec | None = _register_semgrep_enforcement(
                    effect.enforcement_id,
                    table=table,
                    pattern=effect.pattern,
                    rule_id=effect.rule_id,
                    config=effect.config,
                    declared_in=effect.declared_in,
                    expand_hy=effect.expand_hy,
                    languages=effect.languages,
                    message=effect.message,
                    severity=effect.severity,
                    bad=effect.bad,
                    good=effect.good,
                    hit_fixtures=effect.hit_fixtures,
                    clean_fixtures=effect.clean_fixtures,
                    mode=effect.mode,
                )
            else:
                answer = _assert_semgrep_enforcement(
                    effect.enforcement_id, table=table, semgrep=executable
                )
        except (AssertionError, KeyError, ValueError) as failed:
            return (yield TransferThrow(k, failed))
        return (yield Transfer(k, answer))

    return answer_semgrep
