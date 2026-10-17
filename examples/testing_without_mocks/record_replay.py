# record_replay.py
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Complete(EffectBase):
    prompt: str


@do
def summarize(doc: str):
    title = yield Complete(f"Title for: {doc}")
    tags = yield Complete(f"Three tags for: {doc}")
    return {"title": title, "tags": tags}


def recording(tape: dict):
    @do
    def record(effect: Complete, k):
        tape[effect.prompt] = yield effect  # the real model answers
        return (yield Resume(k, tape[effect.prompt]))

    return handler(record)


def replaying(tape: dict):
    def replay(effect: Complete, k):
        return Resume(k, tape[effect.prompt])  # no model call at all

    return handler(replay)


model_calls = []


def fake_model(effect: Complete, k):  # stand-in for your real model client
    model_calls.append(effect.prompt)
    return Resume(k, f"<answer {len(model_calls)}>")


if __name__ == "__main__":
    tape = {}
    first = run(handler(fake_model)(recording(tape)(summarize("release notes"))))
    calls_while_recording = len(model_calls)
    again = run(replaying(tape)(summarize("release notes")))
    print(first == again, calls_while_recording, len(model_calls) - calls_while_recording)
