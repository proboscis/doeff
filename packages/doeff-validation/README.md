# doeff-validation

Run every independent check, collect the failures of all failed checks, and report them as a `ValidationException`.
A thin layer on top of doeff-traverse, where one check becomes one element of a traverse. Whether the checks run sequentially, concurrently,
or stop at the first failure is chosen by the traverse handler placed at the root of the assembly.

The Hy `validate` / `check` macros are provided by doeff-hy (`(require doeff-hy.macros [defk validate check])`).
This package provides the runtime part (`validate`, the check-specification type `Check`, the execution-mark type `Perform`, and the failure types).

## Rewrite example

Before the rewrite (it exits at the first `raise`, so the second and later mismatches are invisible; the reason is a single sentence):

```hy
(defk bind-pending-job-to-chosen-node [job spec placement now]
  {:pre [(: job AgentJob) (: spec AgentJobSpec) (: placement AgentPlacement) (: now int)]
   :post [(: % WriteStatus)]}
  (when (or (!= job.key placement.request.key)
            (!= spec.subject placement.request.conversation)
            (not-in job.kind #(JobKind.TURN JobKind.SUMMARIZE))
            (!= job.phase Phase.PENDING)
            (is-not job.cancel None))
    (raise (PlacementError "job does not match the placement request")))
  (write-of-agent-job-status job (bound-status placement job now)))
```

After the rewrite. Matches checked at the entrance of the function are written as `check` in the contract `:pre`. Every check is evaluated,
and all that fail go into the `ValidationException`:

```hy
(require doeff-hy.macros [defk check])

(defk bind-pending-job-to-chosen-node [job spec placement now]
  "Build the state write (CAS) that binds a Pending job to the node and profile chosen by placement."
  {:pre [(: job AgentJob) (: spec AgentJobSpec) (: placement AgentPlacement) (: now int)
         (check = job.key placement.request.key :reason PlacementMismatch.KEY)
         (check = spec.subject placement.request.conversation :reason PlacementMismatch.CONVERSATION)
         (check in job.kind #(JobKind.TURN JobKind.SUMMARIZE) :reason PlacementMismatch.KIND)
         (check = job.phase Phase.PENDING :reason PlacementMismatch.PHASE)
         (check is job.cancel None :reason PlacementMismatch.CANCELLED)]
   :post [(: % WriteStatus)]}
  (write-of-agent-job-status job (bound-status placement job now)))
```

Message when it fails (the expression, the evaluated value of each argument, and the reason are listed):

```
bind-pending-job-to-chosen-node pre-condition: 2 checks failed:
  (= job.key placement.request.key)  job.key = 'k-1'  placement.request.key = 'k-2'  [PlacementMismatch.KEY]
  (= job.phase Phase.PENDING)  job.phase = <Phase.RUNNING: 'running'>  Phase.PENDING = <Phase.PENDING: 'pending'>  [PlacementMismatch.PHASE]
```

Only the arguments are visible from `:pre`, so reading from the raw dict is finished at the caller's boundary, and typed values
(`job`, `spec`) are accepted as arguments.

To check in the middle of a function body, use `validate`. For a check that uses an effect, mark the argument with `(! …)`:

```hy
(require doeff-hy.macros [defk validate check])

(defk ensure-conversation-bindable [job request]
  "Check that the conversation of the placement request still exists and the job is still Pending."
  {:pre [(: job AgentJob) (: request PlacementRequest)] :post [(: % NoneType)]}
  (! (validate
       (check is-not (! (LookupConversation request.conversation)) None
              :reason PlacementMismatch.CONVERSATION)
       (check = job.phase Phase.PENDING :reason PlacementMismatch.PHASE))))
```

## How to write

### `validate`

- Only **independent items** are lined up directly under it, of two kinds: `check` and a Program (a defk call).
    Items do not depend on each other's results (a unit that may run concurrently).
- Run every item and collect the failures of all failed items. If there is even one, `ValidationException` is raised;
    otherwise `None` is returned.
- `validate` is an expression that builds a Program, so in the body of a defk run it with `(! (validate …))` or `(<- _ (validate …))`.
- Writing a bare `(<- …)` or `(! …)` directly under it is an error at expansion time. Processing that depends on a previous value is
    finished before `validate`, or the helper defk is given its own `validate`.

### `check`

- `(check operator arg … :reason reason)` or `(check expression :reason reason)`. `(check (= x 0))` is
    decomposed the same as `(check = x 0)`, recording the expression and the value of each argument. Short-circuit and control forms such as `and` / `or` are
    not decomposed; only the expression and its truth value are recorded (the flat form `(check and a b)` is an error at expansion time).
- Each argument is evaluated from the left. Only an argument marked with `(! …)` is executed as an effect, and its result value is compared
    (for `(check = (! (CountSeats node)) 0)` the record is "`(! (CountSeats node))` = 3"). An argument without the mark is
    compared as-is, even if it is a Program value.
- If evaluating an argument or the verdict raises an exception, it is collected as the failure (`CheckError`) of that check. Other checks continue.
- It can be written **only directly under `validate` and inside `:pre` / `:post` of defk / do!**. Writing it in the body of a defk, in `fn`, or in
    a comprehension is an error at expansion time. All checks stay visible, lined up under `validate` or in the contract, and
    are not hidden inside a helper.

### Reusing a group of checks

Give the helper defk its own `validate`, and line up its call as an item in the outer `validate`.
A `ValidationException` that failed inside is counted as one failure on the outside (holding the list of failures inside).

```hy
(defk job-is-bindable [job]
  "Check that the kind and state of the job are such that it can be bound to a node."
  {:pre [(: job AgentJob)] :post [(: % NoneType)]}
  (! (validate
       (check in job.kind #(JobKind.TURN JobKind.SUMMARIZE) :reason PlacementMismatch.KIND)
       (check = job.phase Phase.PENDING :reason PlacementMismatch.PHASE))))

(! (validate
     (job-is-bindable job)
     (check is-not (! (LookupConversation request.conversation)) None
            :reason PlacementMismatch.CONVERSATION)))
```

### Python

```python
import operator
from doeff_validation import Check, Perform, validate

yield validate(
    Check(operator.is_not, Perform(LookupConversation(request.conversation)), None,
          reason=PlacementMismatch.CONVERSATION),
    Check(operator.eq, job.phase, Phase.PENDING, reason=PlacementMismatch.PHASE),
    job_is_bindable(job),        # a Program that has its own validate
)
```

In Python, write the types `Check` and `Perform` directly (there are no functions `check` or `perform`). `Check(…)` is a value passed to `validate`
and is not a Program on its own (it cannot be `yield`ed). This expresses "check goes only directly under validate" as a type.
`Perform(…)` corresponds to Hy `(! …)`. If the first argument of `Check` is not callable, or if a value that is neither a
Program nor an effect is passed to `Perform`, a `TypeError` is raised at construction. Python arguments are evaluated when `validate` is called (not deferred into the item as in Hy).

### How to run — sequential, concurrent, fail-fast

```python
from doeff import run
from doeff_core_effects import scheduled
from doeff_traverse import parallel, parallel_fail_fast, sequential

run(sequential(program))                    # run everything sequentially and collect
run(scheduled(parallel(8)(program)))          # run everything concurrently and collect
run(scheduled(parallel_fail_fast(1)(program)))  # stop at the first failure (one failure)
```

The code that writes the checks is the same. The checks in a contract (`:pre` / `:post`) are evaluated in order on the spot inside the function,
so no traverse handler is needed.

## When you want to receive the failure as a value

The `ValidationException` thrown by `validate` can be turned into a value with doeff's `Try`:

```hy
(<- result (Try (validate …)))   ; Ok(None) or Err(ValidationException)
```

`ValidationException.failures` is the sequence of records of failed checks (`CheckFailure` = verdict false, `CheckError` =
evaluation raised an exception, or a nested `ValidationException`); `reasons` is the sequence of reasons.

## Choosing how to use exceptions

- **Foreseeable business failures** (mismatches in input or observation): collect them with `validate` / the contract `check`, or return a value
    (`Ok` / `Err`).
- **Unrecoverable anomalies** (broken invariants, program bugs): keep them as exceptions. Exceptions other than `ValidationException`
    raised inside a Program item of `validate` are thrown as-is without being collected.
- **try-catch only at boundaries**: use `Try` where you want to turn an exception into a value (the caller's entrance, an API boundary).
    Do not catch at every check.

## Types

| Name | What it is |
| --- | --- |
| `Check(predicate, *args, reason=…, expression=…)` | Specification of one check (meaningful only as an item of `validate`) |
| `CheckFailure` | Record of a check whose verdict was false (expression text, evaluated argument values, reason) |
| `CheckError` | Record of a check where evaluating an argument or the verdict raised an exception |
| `ValidationException` | Exception holding all failures (`failures`, `reasons`, `context`) |
| `Perform(program)` | Mark that executes an argument of a Python `Check` as an effect |
