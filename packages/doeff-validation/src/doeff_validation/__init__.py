"""doeff-validation — 独立した検査の項目を全部走らせ、落ちた項目の失敗を全部集めて
``ValidationException`` で返す(doeff-traverse の上の薄い層)。

Python:

    yield validate(
        check(operator.is_not, perform(LookupConversation(request.conversation)), None,
              reason=PlacementMismatch.CONVERSATION),
        check(operator.eq, job.phase, Phase.PENDING, reason=PlacementMismatch.PHASE),
    )   # 失敗が 1 つでもあれば ValidationException(全部の失敗を持つ)

    run(sequential()(program))   # 逐次。並行は parallel(n)、fail-fast は parallel_fail_fast(n)

Hy: ``(require doeff-hy.macros [defk validate check])`` → ``(! (validate (check = (! (Eff)) b :reason R) …))``。
設計の記録: ``docs/design/doeff-validation/design.md``。
"""

from doeff_validation.api import (
    check as check,
)
from doeff_validation.api import (
    perform as perform,
)
from doeff_validation.api import (
    validate as validate,
)
from doeff_validation.failures import (
    CheckArgument as CheckArgument,
)
from doeff_validation.failures import (
    CheckError as CheckError,
)
from doeff_validation.failures import (
    CheckFailure as CheckFailure,
)
from doeff_validation.failures import (
    CheckSpec as CheckSpec,
)
from doeff_validation.failures import (
    EvaluatedArgument as EvaluatedArgument,
)
from doeff_validation.failures import (
    Performed as Performed,
)
from doeff_validation.failures import (
    ValidationException as ValidationException,
)
