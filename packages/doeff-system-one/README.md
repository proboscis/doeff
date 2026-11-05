# doeff-system-one

The judgment effect. It sends "how should this question be answered about this state?" to a calibrated judge (TypeSafe
System One = Jev, or another implementation with the same contract) and receives an answer with probabilities.

This is the provider-agnostic layer, split the same way as doeff-llm and doeff-openai.

| package | role |
|---|---|
| `doeff-system-one` (this package) | the effect contract (`Judge`), question and answer types, and a scripted handler for tests |
| `doeff-jev` | handler mapping `Judge` to TypeSafe (direct API and Vercel AI Gateway), target and key resolution, answer cache |
| a self-hosted judge package (future) | handler for a Jev-compatible judge running on our own GPU (if the wire format is the same, just pass the target to the doeff-jev handler) |

## Usage

```python
from doeff import do, run, with_handlers
from doeff_system_one import Judge, Question

@do
def is_vague(prompt: str):
    verdict = yield Judge(
        state={"prompt": prompt},
        questions={
            "vague": Question(kind="noul", instructions="Is this request vague, so that planning is needed before starting?"),
            "kind": Question(kind="choice", instructions="Which kind of request is this?",
                             options=(("investigate", "investigate"), ("implement", "implement"), ("operate", "operate"))),
        },
    )
    vague = verdict.answer("vague")
    # If confidence is below the floor, it means "cannot judge" -- do not read it as "the condition is not met".
    return vague.confidence is not None and vague.confidence >= 0.6 and vague.value >= 0.7
```

The handler is chosen by the composition root. Tests stack `scripted_judge_handler`.

## The 3 kinds of question

A question is built with `Question(kind=..., instructions=...)`. choice holds its options in `options` (a sequence of id and description pairs), and score holds its levels in `levels` (an ordered sequence of descriptions).

| kind | answer | when to use |
|---|---|---|
| `Question(kind="choice", instructions, options)` | `value` = the chosen option, `probabilities`, `confidence` | pick one from fixed options |
| `Question(kind="noul", instructions)` | `value` = probability of yes (0-1), `confidence` = distance from 0.5 x 2 | whether a condition holds |
| `Question(kind="score", instructions, levels)` | `value` = expected value over levels, `confidence` | measure a degree |

## How to read answers

The public API is only the effect, the handler, and the question and answer types (ADR-DOE-API-001 R1). The pure functions that read answers (`answered`, `chosen`,
`crosses`, `calibration_ok`, `clip`) and the advised character count for the state `STATE_CHARS_ADVISED` live inside this package (`_judgment`)
and are not exported from the entry point. Callers write the following reading rules in their own code.

- An answer with no confidence (`confidence`) or confidence below the floor means "cannot judge", not "the condition is not met".
- For choice, adopt the value only when confidence is above the floor and the value is not the "none of them" option.
- Measure the judge before use: the violating example must cross, the compliant example must not cross, and **both must actually give an answer**.
- Cut the text put in the state to about 2,000 chars. Beyond 2,000 chars confidence drops (measured 2026-09-19).
