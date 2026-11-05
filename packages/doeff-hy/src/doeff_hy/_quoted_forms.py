"""A sequence that carries the body left by defk / defhandler / defp (``__doeff_body__``) as a string and restores it to Hy models only when first read.

Why: embedding the body with a ``'~body`` quote makes Hy compile that quote into "Python code that builds a tree of models one clause at a time
on every import". In a measurement on 2026-09-28, exactly half (22 seconds) of the compile of the import tree of the agora-controllers turn simulation (44 seconds without .pyc)
was the expansion of this quote. If the body is made one ``hy.repr`` string, compile needs only one string
constant, and it is read back with ``hy.read`` only when a tool that reads bodies (doeff_hy._sexpr, doeff_domain._introspect) touches it.

Round-trip fidelity: ``hy.read(hy.repr(forms))`` returns ``(quote forms)``, whose contents equal the original models (measured the same day —
0 mismatches over 6,090 bodies of defk / defhandler / defp in agora-controllers and doeff). Positions (line and column) are not carried.
"""


from collections.abc import Iterator, Sequence

import hy
import hy.models


class QuotedForms(Sequence):
    """A read-only sequence that holds the ``hy.repr`` string of the body and, when first read, restores it to a sequence of models and remembers it."""

    __slots__ = ("_mut_forms", "text")

    def __init__(self, text: str) -> None:
        """text = the string of the ``hy.repr`` body (``hy.models.List``) (made at macro expansion time)."""
        self.text = text
        self._mut_forms: hy.models.Sequence | None = None

    def forms(self) -> hy.models.Sequence:
        """The sequence of body models (read only the first time — because tools that read bodies walk it as models)."""
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
        """To look up a clause by index (the Sequence contract — an int gives one clause, a slice gives a sequence of models)."""
        return self.forms()[index]

    def __len__(self) -> int:
        """To answer the number of clauses (the Sequence contract)."""
        return len(self.forms())

    def __iter__(self) -> Iterator[object]:
        """To walk the clauses in order (the Sequence contract)."""
        return iter(self.forms())

    def __eq__(self, other: object) -> bool:
        """To tell whether it is the same body (it can also be compared with a sequence of models)."""
        if isinstance(other, QuotedForms):
            return self.text == other.text
        return self.forms() == other

    def __hash__(self) -> int:
        """To make the same body under the same key as a string."""
        return hash(self.text)

    def __repr__(self) -> str:
        """To show readers the literal text of the body."""
        return f"QuotedForms({self.text!r})"
