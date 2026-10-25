# The limits scene: continuations are one-shot (docs/positioning/what-doeff-is.md, "Continuations are one-shot").
# A handler that resumes k once per option fails at the second Resume; running the whole program again, once per
# choice, stands in for it. Prints when order() goes on past its yield, what the second Resume raised, and the
# answers of the runs per choice.
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Choose(EffectBase):
    options: tuple


@do
def order():
    drink = yield Choose(("tea", "coffee"))
    print(f"  order goes on with {drink!r}")
    return f"{drink} please"


@do(non_tail=True)
def every_option(effect: Choose, k):
    answers = []
    for option in effect.options:
        print(f"  every_option: Resume(k, {option!r})")
        answers.append((yield Resume(k, option)))
    return answers


print("every_option:")
try:
    run(handler(every_option)(order()))
except RuntimeError as error:
    print(f"  raises RuntimeError: {error}")
    print("  one-shot violation in the message:", "one-shot violation" in str(error))


def choosing(index):
    @do
    def on_choose(effect: Choose, k):
        return (yield Resume(k, effect.options[index]))

    return handler(on_choose)


print("one run per choice:")
print("  ->", [run(choosing(index)(order())) for index in range(2)])
