# What a handler does with k, as the page's sections 1, 3 and 6 show it, around the running example: resume it at
# once, drop it (a cost cap), perform the effect again and resume once (retries), keep the answers and replay them.
# Prints each run's result and what the handlers saw, in the order it happened, to back the k scenes of the video.
from doeff import Pass, Resume, do, handler, run

from ai_effects import Chat, Draw, canned_chat, canned_images, illustrated_summary

events = []


def providers(program):
    return handler(canned_chat)(handler(canned_images)(program))


@do
def seen(effect: Chat | Draw, k):     # notes each effect on its way out, and passes it on
    events.append(f"{type(effect).__name__}({effect.model!r}, {effect.prompt!r})")
    return (yield Pass(effect, k))


print("resume at once:", run(providers(handler(seen)(illustrated_summary("tides")))))
print("  effects:", events)

# Drop it: a budget across all providers (section 1).
PRICE_CENTS = {"gpt-4o": 1, "imagen-3": 4}


def cost_cap(limit_cents):
    spent = 0

    @do
    def cap(effect: Chat | Draw, k):
        nonlocal spent
        spent += PRICE_CENTS[effect.model]
        events.append(f"{effect.model}: spent {spent} of {limit_cents}")
        if spent > limit_cents:
            return f"stopped before {effect.model}: over {limit_cents} cents"
        return (yield Pass(effect, k))

    return handler(cap)


for limit in (10, 3):
    events.clear()
    print(f"cost_cap({limit}):", run(providers(cost_cap(limit)(illustrated_summary("tides")))))
    print("  cap saw:", events)

# Perform it again, resume once: retries (section 3).
attempts = []


@do
def flaky_chat(effect: Chat, k):        # the provider times out twice, then answers
    attempts.append(effect.model)
    if len(attempts) < 3:
        raise ConnectionError("provider timed out")
    return (yield Resume(k, f"summary by {effect.model}"))


@do
def retry(effect: Chat | Draw, k):      # one retry policy for every model call
    for attempt in range(3):
        try:
            answer = yield effect       # ask the handlers outside this one
        except ConnectionError:
            events.append(f"attempt {attempt + 1}: timed out")
            if attempt == 2:
                raise
            continue
        events.append(f"attempt {attempt + 1}: {answer}")
        return (yield Resume(k, answer))


events.clear()
print("retry:", run(handler(flaky_chat)(handler(canned_images)(handler(retry)(illustrated_summary("tides"))))))
print("  retry saw:", events, "provider calls:", len(attempts))


# Keep the answers: record once, replay (section 6).
def record(tape):
    @do
    def recorder(effect: Chat | Draw, k):
        answer = yield effect
        tape[effect] = answer
        return (yield Resume(k, answer))

    return handler(recorder)


def replay(tape):
    @do
    def player(effect: Chat | Draw, k):
        if effect not in tape:
            raise LookupError(f"not recorded: {effect}")
        return (yield Resume(k, tape[effect]))

    return handler(player)


tape = {}
print("record:", run(providers(record(tape)(illustrated_summary("tides")))))
print("  tape:", [f"{type(e).__name__} {e.model} -> {a}" for e, a in tape.items()])
print("replay:", run(replay(tape)(illustrated_summary("tides"))))
try:
    run(replay(tape)(illustrated_summary("waves")))
except LookupError as error:
    print("replay waves:", error)
