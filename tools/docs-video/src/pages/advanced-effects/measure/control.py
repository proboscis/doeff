# Control flow of your own, as a handler for your own effect (the example in "Custom Control Effects with Handlers"
# of docs/09-advanced-effects.md). audit_handler resumes k, so the body goes on; abort_handler does not, so the body
# ends where it yielded Abort and the handler's value is the result. Prints each step the body reached, to back the
# custom-control scene of the Advanced Effects video.
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Abort(EffectBase):
    reason: str


@dataclass(frozen=True)
class Audit(EffectBase):
    message: str


@handler
def abort_handler(effect: Abort, k):
    # Not resuming k ends the body here; this value becomes the result.
    print("  ", f"abort_handler got Abort({effect.reason!r}), does not resume k")
    return f"aborted: {effect.reason}"


@handler
def audit_handler(effect: Audit, k):
    print("  ", f"audit_handler got Audit({effect.message!r}), resumes k")
    return (yield Resume(k, None))


@do
def checkout(stock: int):
    yield Audit(f"stock={stock}")
    print("  ", "checkout goes on after Audit")
    if stock == 0:
        yield Abort("out of stock")
        print("  ", "checkout goes on after Abort")  # never reached: k is not resumed
    print("  ", "checkout returns 'ordered'")
    return "ordered"


def with_policies(program):
    return abort_handler(audit_handler(program))


for i in range(3):
    for stock in (3, 0):
        print(f"run {i + 1} checkout({stock}):")
        print("   ->", repr(run(with_policies(checkout(stock)))))
