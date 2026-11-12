"""A sequence that carries the body left by defk / defhandler / defp (``__doeff_body__``) as a string and restores it to Hy models only when first read.

Why: embedding the body with a ``'~body`` quote makes Hy compile that quote into "Python code that builds a tree of models one clause at a time
on every import". In a measurement on 2026-09-28, exactly half (22 seconds) of the compile of the import tree of the agora-controllers turn simulation (44 seconds without .pyc)
was the expansion of this quote. If the body is made one ``hy.repr`` string, compile needs only one string
constant, and it is read back with ``hy.read`` only when a tool that reads bodies (doeff_hy._sexpr, doeff_domain._introspect) touches it.

Packed text: the string is placed in the code as ``zlib.compress`` of its UTF-8 (:func:`packed_text` — made at macro expansion time) and is unpacked only
when read. Every process that imports the module holds the text for its whole life, and a body that contains one non-ASCII character (Japanese
comments in strings and docstrings) is stored by CPython at 2 bytes per character for the whole text: in a measurement on 2026-11-12, the 33,253
distinct body texts held by a doeff-cluster warm child that preloads every agora-controllers entry took 28.6 MB as str and 11.5 MB packed. A
``str`` text (code compiled before the text was packed) is still accepted and read as is.

Round-trip fidelity: ``hy.read(hy.repr(forms))`` returns ``(quote forms)``, whose contents equal the original models (measured the same day —
0 mismatches over 6,090 bodies of defk / defhandler / defp in agora-controllers and doeff). Positions (line and column) are not carried.
"""


import zlib
from collections.abc import Iterator, Sequence

import hy
import hy.models


def packed_text(text: str) -> bytes:
    """The packed form of a body text placed in the code (the macro expansion's side — the reader is :attr:`QuotedForms.text`)."""
    return zlib.compress(text.encode("utf-8"), 9)


class QuotedForms(Sequence):
    """A read-only sequence that holds the packed ``hy.repr`` string of the body and, when first read, restores it to a sequence of models and remembers it."""

    __slots__ = ("_mut_forms", "packed")

    def __init__(self, packed: bytes | str) -> None:
        """packed = :func:`packed_text` of the ``hy.repr`` body (``hy.models.List``) made at macro expansion time, or the string itself (code compiled
        before the text was packed)."""
        self.packed = packed
        self._mut_forms: hy.models.Sequence | None = None

    @property
    def text(self) -> str:
        """The ``hy.repr`` string of the body (unpacked on every read — it is not kept, so reading it does not grow the process)."""
        packed = self.packed
        return packed if isinstance(packed, str) else zlib.decompress(packed).decode("utf-8")

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
            if type(self.packed) is type(other.packed):
                return self.packed == other.packed
            return self.text == other.text
        return self.forms() == other

    def __hash__(self) -> int:
        """To make the same body under the same key as a string."""
        return hash(self.text)

    def __repr__(self) -> str:
        """To show readers the literal text of the body."""
        return f"QuotedForms({self.text!r})"
