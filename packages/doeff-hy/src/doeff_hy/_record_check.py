"""defrecord の :check の展開が __post_init__ から呼ぶ検め(実行時と型検査の両方の展開が読む)。

前は doeff_hy.record の deff だったが、doeff_hy.record は Hy の require が path で読む macro の境界で、見せる名は macro だけ
なので、内側のこの module に置く(ADR-DOE-API-001 R14)。Python の module なので、型検査のための展開の
呼びを pyright が stub 無しで読む。標準の library も import しない葉の module — doeff_hy の中の module も defrecord を使うので、
展開が読む物に doeff_hy の import を持たせない(doeff_hy._record_runtime と同じ訳)。
"""


def require_check(
    record: str, fields: tuple[str, ...], check: str, passed: bool, values: dict[str, object]
) -> None:
    """defrecord の :check の 1 式の結果を検める。偽なら ValueError で「どの型のどの欄がどの検めで落ちたか」と欄の値を名指す
    (Program を返した式は展開の中で先に TypeError にしてある)。dataclass の __post_init__ から呼ぶので、Program を返す defk には
    しない。"""
    if not passed:
        shown = " ".join(f"{k}={v!r}" for k, v in values.items())
        raise ValueError(f"{record} の欄 {'・'.join(fields)} が検め {check} で落ちた: {shown}")
