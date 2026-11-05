"""doeff-validation — run every independent check item, collect the failures of all failed items, and
return them as a ``ValidationException`` (a thin layer on top of doeff-traverse).

Python:

    yield validate(
        Check(operator.is_not, Perform(LookupConversation(request.conversation)), None,
              reason=PlacementMismatch.CONVERSATION),
        Check(operator.eq, job.phase, Phase.PENDING, reason=PlacementMismatch.PHASE),
    )   # ValidationException (holding all failures) if there is even one failure

    run(sequential(program))   # sequential. Concurrent is parallel(n); fail-fast is parallel_fail_fast(n)

Hy: ``(require doeff-hy.macros [defk validate check])`` → ``(! (validate (check = (! (Eff)) b :reason R) …))``。
Design record: ``docs/design/doeff-validation/design.md``.
"""

from doeff_validation._api import (
    validate as validate,
)
from doeff_validation._failures import (
    Check as Check,
)
from doeff_validation._failures import (
    Perform as Perform,
)
from doeff_validation._failures import (
    ValidationException as ValidationException,
)
