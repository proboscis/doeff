"""Test: Try used DEEP inside handler body (via nested @do function calls).

Reproduces the exact pattern from doeff_openai's production handler:
  handler body → yield sub_handler_func() → yield helper() → yield Try(...)

The existing test_try_in_handler_body.py tests Try yielded DIRECTLY from
handler body.  This test covers the case where Try is inside a @do function
that is called from another @do function, all within the handler body.

This mirrors:
  openai_production_handler(effect, k)
    → yield _handle_structured_output(effect, k)   # @do
      → yield chat_completion(...)                  # @do
        → yield get_openai_client()                 # @do
          → yield Try(try_ask_client())             # Try 3 levels deep
"""
from doeff import do, run, WithHandler, Resume, Pass
from doeff_core_effects import Ask, Try, Get, Put, Tell
from doeff_core_effects.handlers import reader, try_handler, state, writer
from doeff_vm import EffectBase, Ok, Err


class CustomQuery(EffectBase):
    """Mimics LLMStructuredQuery."""
    def __init__(self, prompt, model="test"):
        self.prompt = prompt
        self.model = model


# --- Level 3: deepest helper that uses Try (mimics get_openai_client) ---

@do
def _get_state_or_none(key):
    """Mimics doeff_openai.client._get_state_or_none."""
    @do
    def _read():
        return (yield Get(key))

    safe = yield Try(_read())
    return safe.value if safe.is_ok() else None


@do
def get_client():
    """Mimics get_openai_client — uses Try to probe Ask, then State."""
    @do
    def try_ask():
        return (yield Ask("client"))

    safe = yield Try(try_ask())
    if safe.is_ok() and safe.value:
        return safe.value

    # Fallback: get api_key from Ask
    @do
    def try_ask_key():
        return (yield Ask("api_key"))

    safe_key = yield Try(try_ask_key())
    key = safe_key.value if safe_key.is_ok() else "default-key"
    return f"client({key})"


# --- Level 2: helper that calls level-3 (mimics chat_completion) ---

@do
def do_query(prompt, model):
    """Mimics chat_completion — gets client via get_client, then does work."""
    yield Tell(f"do_query: prompt={prompt}, model={model}")
    client = yield get_client()

    # Mimic Try-based retry logic
    @do
    def make_call():
        yield Tell(f"make_call with {client}")
        return f"response({client}, {prompt})"

    safe = yield Try(make_call())
    if safe.is_err():
        raise safe.error
    return safe.value


# --- Level 1: sub-handler func (mimics _handle_structured_output) ---

@do
def _handle_custom_query(effect, k):
    """Mimics _handle_structured_output — calls do_query, then resumes."""
    response = yield do_query(effect.prompt, effect.model)
    return (yield Resume(k, response))


# --- Level 0: the handler (mimics openai_production_handler) ---

@do
def custom_query_handler(effect, k):
    """Mimics openai_production_handler — dispatches to sub-handler func."""
    if isinstance(effect, CustomQuery):
        return (yield _handle_custom_query(effect, k))
    yield Pass(effect, k)


# =====================================================================
# Tests
# =====================================================================

def test_try_deep_in_handler_body_happy_path():
    """Try nested 3 levels deep inside handler body — all effects reachable."""
    @do
    def prog():
        result = yield CustomQuery("hello", model="gpt-5")
        return result

    env = {"api_key": "sk-test", "client": "prebuilt-client"}
    wrapped = prog()
    # reader(outer) → state → writer → try_handler → custom_query_handler(inner)
    for h in reversed([reader(env=env), state(), writer(), try_handler, custom_query_handler]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result == "response(prebuilt-client, hello)"


def test_try_deep_in_handler_body_no_client_in_env():
    """Try probes Ask('client') which fails, falls back to Ask('api_key')."""
    @do
    def prog():
        result = yield CustomQuery("hello")
        return result

    env = {"api_key": "sk-fallback"}  # no 'client' key
    wrapped = prog()
    for h in reversed([reader(env=env), state(), writer(), try_handler, custom_query_handler]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result == "response(client(sk-fallback), hello)"


def test_try_deep_in_handler_body_no_keys():
    """Try probes fail for both client and api_key, uses default."""
    @do
    def prog():
        result = yield CustomQuery("hello")
        return result

    env = {}  # no client, no api_key
    wrapped = prog()
    for h in reversed([reader(env=env), state(), writer(), try_handler, custom_query_handler]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result == "response(client(default-key), hello)"


def test_try_deep_wrapped_in_outer_try():
    """Program wraps the CustomQuery call in Try — double nesting of Try."""
    @do
    def prog():
        safe = yield Try(CustomQuery("hello"))
        # Note: CustomQuery inside Try means try_handler handles Try,
        # runs the inner program which yields CustomQuery,
        # then custom_query_handler handles it and internally uses more Try.
        return safe

    env = {"api_key": "sk-test"}
    wrapped = prog()
    for h in reversed([reader(env=env), state(), writer(), try_handler, custom_query_handler]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert isinstance(result, Ok)
    assert "response(" in result.value


def test_try_deep_in_handler_body_with_retry_loop():
    """Mimic the retry loop pattern from chat.py: for + Try in handler body."""
    call_count = 0

    @do
    def flaky_handler(effect, k):
        """Handler that retries via Try loop (like chat_completion retry)."""
        nonlocal call_count
        if isinstance(effect, CustomQuery):
            @do
            def attempt():
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise ConnectionError(f"attempt {call_count}")
                return f"success on attempt {call_count}"

            last_error = None
            for i in range(5):
                safe = yield Try(attempt())
                if safe.is_ok():
                    return (yield Resume(k, safe.value))
                last_error = safe.error
            raise last_error
        yield Pass(effect, k)

    @do
    def prog():
        return (yield CustomQuery("retry-test"))

    wrapped = prog()
    for h in reversed([try_handler, flaky_handler]):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped)
    assert result == "success on attempt 3"
    assert call_count == 3
