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
    @handler
    @do
    def record(effect: Complete, k):
        tape[effect.prompt] = yield effect  # the real model answers
        return (yield Resume(k, tape[effect.prompt]))

    return record


def replaying(tape: dict):
    @handler
    def replay(effect: Complete, k):
        return Resume(k, tape[effect.prompt])  # no model call at all

    return replay


model_calls = []


@handler
def fake_model(effect: Complete, k):  # stand-in for your real model client
    model_calls.append(effect.prompt)
    return Resume(k, f"<answer {len(model_calls)}>")


if __name__ == "__main__":
    tape = {}
    first = run(fake_model(recording(tape)(summarize("release notes"))))
    calls_while_recording = len(model_calls)
    again = run(replaying(tape)(summarize("release notes")))
    print(first == again, calls_while_recording, len(model_calls) - calls_while_recording)
