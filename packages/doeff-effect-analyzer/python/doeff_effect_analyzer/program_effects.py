"""移し替えの層: doeff の main を読む外の repo(agora-controllers・merge-queue の検)が、まだこの古い path から
import する 4 名だけを出す。呼び手を effect AnalyzeProgram・CheckCoverage と値の型 FoldCarried へ書き換えたら、この
module ごと消す(ADR-DOE-API-001 R24)。解析の本体は内側の ``_program_effects``。
"""

from doeff_effect_analyzer._program_effects import ProgramEffects as ProgramEffects
from doeff_effect_analyzer._program_effects import analyze_program as analyze_program
from doeff_effect_analyzer._program_effects import qualified_name as qualified_name
from doeff_effect_analyzer._program_effects import runs_where_performed as runs_where_performed
