"""doeff-adr の置き場のうち環境変数が名指す物 — この package で環境を読む module はここだけ(doeff_hy._env_places と同じ形)。

関数は 1 つの変数の素の値を返し、どの置き場を使うかは呼び手が決める。名は同じ repo の DOEFF_HY_CODE_STORE・DOEFF_WHEEL_CACHE と同じ
「DOEFF_<package>_<置き場>」の形 — 日次の全体検証の task は回ごとに HOME を空の dir に替え、回をまたぐ置き場をこの形の環境変数で子へ渡す。
"""

#: Hy の test file の item の記録の置き場の dir(card ki-9338eec1d15e の責務 R2 — 順は item_cache.items_cache_dir_for)。
ITEMS_CACHE_ENV = "DOEFF_ADR_ITEMS_CACHE"
#: installed の defsemgrep の答えを process の外に残す置き場の dir(card ki-f81dde817291 — registry の _installed_verdict)。
SEMGREP_VERDICTS_ENV = "DOEFF_ADR_SEMGREP_VERDICTS"


def environment_value(name: str) -> str | None:
    """環境変数 1 つの素の値(無ければ None)— 設定の Ask を、環境変数から答える foundation の答え手 env_var_ask(接頭辞なし)の
    下で問う(doeff_hy._env_places の読みと同じ形)。os.environ を直に読むと doeff-linter の DOEFF004 に当たる(日次の lint の段の赤
    2026-10-11 08:05 JST)。env_var_ask は `{module.path}` の形の値を import の名として読むので、置き場の path をその形で書かない。"""
    # doeff-core-effects は doeff-hy の macro を読むので、この module を読み込む時点ではなく呼ばれた時に読む。
    from doeff_core_effects import Ask, env_var_ask, reader

    from doeff import run, with_handlers

    value = run(with_handlers([reader({name: None}), env_var_ask(prefix="")], Ask(name)))
    return value if isinstance(value, str) else None
