"""defk / defhandler / defp が残す本体(``__doeff_body__``)を、文字列で運んで読まれた時に初めて Hy の model へ戻す列。

なぜ: 本体を ``'~body`` の quote で埋め込むと、Hy はその quote を「import のたびに model の木を 1 節ずつ組み立てる
Python code」へ compile する。2026-09-28 の実測では、agora-controllers の手番の模擬の import の木(.pyc が無い状態で 44 秒)の
compile のちょうど半分(22 秒)がこの quote の展開だった。本体を ``hy.repr`` した 1 本の文字列にすれば、compile は文字列の
定数 1 つで済み、本体を読む道具(doeff_hy.sexpr・doeff_domain.introspect)が触った時だけ ``hy.read`` で読み戻す。

往復の忠実さ: ``hy.read(hy.repr(forms))`` は ``(quote forms)`` を返し、その中身は元の model と等しい(同日の実測 —
agora-controllers と doeff の defk / defhandler / defp の本体 6,090 個で不一致 0)。位置(行と桁)は運ばない。
"""


from collections.abc import Iterator, Sequence

import hy
import hy.models


class QuotedForms(Sequence):
    """``hy.repr`` した本体の文字列を持ち、最初に読まれた時に model の列へ戻して覚える、読み取り専用の列。"""

    __slots__ = ("_mut_forms", "text")

    def __init__(self, text: str) -> None:
        """text = ``hy.repr`` した本体(``hy.models.List``)の文字列(macro の展開の時に作る)。"""
        self.text = text
        self._mut_forms: hy.models.Sequence | None = None

    def forms(self) -> hy.models.Sequence:
        """本体の model の列(読むのは最初の 1 回だけ — 本体を読む道具が model として歩くため)。"""
        if self._mut_forms is None:
            read = hy.read(self.text)
            quoted = (
                isinstance(read, hy.models.Expression)
                and len(read) == 2
                and read[0] == hy.models.Symbol("quote")
            )
            self._mut_forms = read[1] if quoted else read
        return self._mut_forms

    def __getitem__(self, index: int | slice) -> object:
        """添字で節を引くため(Sequence の約束 — int は節 1 つ・slice は model の列)。"""
        return self.forms()[index]

    def __len__(self) -> int:
        """節の数を答えるため(Sequence の約束)。"""
        return len(self.forms())

    def __iter__(self) -> Iterator[object]:
        """節を順に歩くため(Sequence の約束)。"""
        return iter(self.forms())

    def __eq__(self, other: object) -> bool:
        """同じ本体か(model の列とも比べられる)を答えるため。"""
        if isinstance(other, QuotedForms):
            return self.text == other.text
        return self.forms() == other

    def __hash__(self) -> int:
        """文字列で同じ本体を同じ鍵にするため。"""
        return hash(self.text)

    def __repr__(self) -> str:
        """読み手に本体の字面を見せるため。"""
        return f"QuotedForms({self.text!r})"
