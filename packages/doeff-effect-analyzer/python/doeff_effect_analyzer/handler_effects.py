"""移し替えの層: doeff の main を読む外の repo(merge-queue の検)が、まだこの古い path から import する
check_coverage だけを出す。呼び手を effect CheckCoverage へ書き換えたら、この module ごと消す(ADR-DOE-API-001 R24)。
解析の本体は内側の ``_handler_effects``。
"""

from doeff_effect_analyzer._handler_effects import check_coverage as check_coverage
