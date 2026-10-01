"""doeff_hy.session の型(defhandler の session val / session var の展開が呼ぶ — agora-redesign #2293)。

本体の session.py は注記つきだが、pyright は stub の無い package の module を strict で「stub が無い」と赤にするので、
ほかの公開の module(wire.pyi・record.pyi など)と同じく stub で型を宣言する。
"""

def session_key(module: str, handler: str, name: str) -> str: ...
