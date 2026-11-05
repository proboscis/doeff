"""Types for the public surface of record.hy (declarations for type checking — at runtime record.hy is read).

record.hy is a Hy module, so pyright cannot read inside it, and `doeff_hy.record.DoExpr` and
`doeff_hy.record.EffectBase`, named by the :check expansion of defrecord, become Unknown
(the using side's defrecord line gets a reportUnknownMemberType the author cannot fix). The types are declared here.

- DoExpr and EffectBase are re-exports of names record.hy imports from doeff (used by the :check expression to stop, via
  isinstance, a form that returned a Program / effect).
- require_check, which rejects a false answer, lives in the inner doeff_hy._record_check (a Python module — pyright reads it directly).
  doeff_hy.record is a boundary that exposes only macros, so no name is placed there (ADR-DOE-API-001 R14).
- defrecord, defwire and defenum are macros, not Python names, so they are absent here (they are brought in with require).
"""

from doeff import DoExpr as DoExpr
from doeff import EffectBase as EffectBase
