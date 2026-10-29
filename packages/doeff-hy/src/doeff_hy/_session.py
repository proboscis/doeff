"""defhandler の session val / session var の保存先のキー(ADR-DOE-HY-006)。

session val / session var の値は Python の隠れた場所(module の変数・handler の object・閉包)には
持たず、doeff の状態の効果(Get / Put — state monad)を通して読み書きする。キーはこの関数 1 つで決まる。
外側の handler と検査は同じ関数でキーを引き、そのキーの Get に値を答えれば(初期化の式を走らせずに)
値を差し替えられ、Put を受ければ書き込みを観測できる。

キーの形は旧い lazy / lazy-val / lazy-var と同じ `<module の __name__>/<handler の名>/<変数の名>`
(名は Hy の綴りのまま・mangle しない)。旧い形から session val / session var へ書き換えても、
同じセッションに入っている値はそのまま引き継がれる。

⚠ 引数を取る handler(`(defhandler h [cfg] …)`)でもキーに引数は入らない: 同じ handler を違う引数で
2 つ入れると、同じセッションの中で値を共有する(旧い lazy-val からの性質)。
"""




def session_key(module: str, handler: str, name: str) -> str:
    """session val / session var の値を Get / Put するキー。外の handler と検査もこれでキーを引く。"""
    return f"{module}/{handler}/{name}"
