"""http-production-handler が呼ぶたびに作る installer の宣言の失敗ケース。

http-production-handler は scope ごとに client を作るため、節を静的に読めない関数(installer)を返す。答える効果と出す効果は
__doeff_handles__ / __doeff_effects__ で宣言しているが、宣言は関数そのものにしか無く、installer には写っていなかった。
そのため stacked_handlers の束(中の handler の宣言の和を付ける)に installer を入れると、束が「読めない handler」のままになった。
"""

from doeff import stacked_handlers
from doeff_core_effects.http_effects import HttpRequest
from doeff_core_effects.http_handlers import http_production_handler


def test_the_installer_declares_what_the_function_declares() -> None:
    installer = http_production_handler()
    assert installer.__doeff_handles__ == http_production_handler.__doeff_handles__ == (HttpRequest,)
    assert installer.__doeff_effects__ == http_production_handler.__doeff_effects__


def test_a_bundle_with_the_installer_declares_the_request_effect() -> None:
    bundle = stacked_handlers(http_production_handler())
    assert HttpRequest in bundle.__doeff_handles__
