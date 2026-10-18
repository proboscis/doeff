# 実行すると ImportError になる取り方(handler は doeff に無い)— 型の check も import の行で誤りにする(抜け 3 — 直った)
from doeff import state  # expect-error
from doeff import no_such_name  # expect-error

__all__ = ["no_such_name", "state"]  # noqa: DOEFF021 — 型の見本の file が __all__ の誤りを意図して持つ
