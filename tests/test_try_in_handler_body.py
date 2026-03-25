"""Test: effect performed inside a handler body must reach outer handlers.

When a handler's @do body yields an effect (e.g., Try), that effect must
propagate to outer handlers in the chain.

Bug: openai_production_handler yields Try(try_ask_client()) inside its
handler body, but Try is not caught by try_handler (which is outer).
"""
from doeff import do, run, WithHandler, Resume, Pass
from doeff_core_effects import Ask, Try
from doeff_core_effects.handlers import reader, try_handler
from doeff_vm import EffectBase, Ok, Err


class LLMQuery(EffectBase):
    def __init__(self, prompt):
        self.prompt = prompt

    def __repr__(self):
        return f"LLMQuery({self.prompt!r})"


@do
def llm_handler_that_uses_try(effect, k):
    """Handler that internally uses Try — mimics openai_production_handler."""
    if isinstance(effect, LLMQuery):
        @do
        def attempt_llm():
            api_key = yield Ask("api_key")
            return f"response from {api_key}: {effect.prompt}"

        # This Try must be caught by an outer try_handler
        safe_result = yield Try(attempt_llm())
        if safe_result.is_ok():
            result = yield Resume(k, safe_result.value)
            return result
        else:
            result = yield Resume(k, f"error: {safe_result.error}")
            return result
    yield Pass(effect, k)


def test_try_in_handler_body_reaches_outer_try_handler():
    """Try yielded from handler body must be caught by outer try_handler."""

    @do
    def prog():
        result = yield LLMQuery("hello")
        return result

    env = {"api_key": "sk-test"}
    # reader (outer) → try_handler → llm_handler (inner)
    wrapped = prog()
    for h in reversed([reader(env=env), try_handler, llm_handler_that_uses_try]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result == "response from sk-test: hello"


def test_try_in_handler_body_catches_error():
    """When the Try'd program fails, handler body gets Err."""

    @do
    def llm_handler_error(effect, k):
        if isinstance(effect, LLMQuery):
            @do
            def attempt():
                yield Ask("missing_key")  # will fail

            safe_result = yield Try(attempt())
            result = yield Resume(k, f"caught: {safe_result.is_err()}")
            return result
        yield Pass(effect, k)

    @do
    def prog():
        return (yield LLMQuery("hello"))

    wrapped = prog()
    for h in reversed([reader(env={}), try_handler, llm_handler_error]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result == "caught: True"
