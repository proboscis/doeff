# The program the CLI runs: it asks for what it needs and leaves the answers to the handlers.
from doeff_core_effects import Ask

from doeff import do


@do
def total():
    rate = yield Ask("tax_rate")
    currency = yield Ask("currency")
    return f"{42 * (1 + rate):.2f} {currency}"


total_program = total()
