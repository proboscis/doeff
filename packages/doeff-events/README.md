# doeff-events

Generic publish/subscribe effects for doeff.

## API

- `Publish(event)`
- `WaitForEvent(*event_types)`
- `event_handler()`
- `subscribed_event_handler(bus, subscriber, event_types=())`, `EventBus()`

## Per-subscriber queues

`event_handler()` hands a signal to everyone waiting at that moment and discards the signal if nobody is waiting. To avoid losing signals emitted between
reading the state and entering `WaitForEvent`, use `subscribed_event_handler()`. The subscription starts when the handler is
built, and signals emitted while no waiter exists are also queued in the per-subscriber queue. The subscriber name, the queue and the `EventBus` appear only in the arguments of the handler's
construction; the Program (`Publish`, `WaitForEvent`) is unchanged.

| Entry point | Behavior |
|---|---|
| `subscribed_event_handler(bus, subscriber, event_types=())` | When built, starts the subscription on `bus` and queues only signals of `event_types` that come after that point. Rebuilding with the same name discards the previous queue. `event_types=()` only emits |
| `Publish(event)` | Hands the event to the queues of all subscribers on `bus` (including itself). Waiting subscribers wake; for subscribers not waiting, it stays in their queue |
| `WaitForEvent(*types)` | Takes the first matching signal from the head of its own queue. If none, waits until a signal arrives. Waiting for a type outside the subscription types raises `ValueError` |
| `EventBus.subscribe(subscriber, event_types)` | Creates and returns one subscriber's queue (the in-handler part `doeff_events._handlers.memory.SubscriberQueue` — not a public name). A handler with another signal source can use the same way of waiting by handing received signals to the queue's `offer(event)` |

Among signals of the same type piled up in the queue while no waiter exists, those that are frozen dataclass signals with a field `keys` (a tuple of changed places) are merged into one
at the position of the earlier one (`keys` are combined in arrival order with duplicates removed; signals whose fields other than `keys` differ are not merged).
The receiver re-reads the records by place, so nothing is lost. Signals without `keys` (such as `TimerFired`) are not merged.

## The event-waiting loop macro `event-loop`

A Hy macro that writes a worker's loop in one shape: "read the state -> wait for an event or the stop signal -> run the clause of whatever arrived".

```hy
(require doeff-events.macros [event-loop])

(event-loop [board Board (! (read-board))]    ; state name, type and initial value (the initial value is inside do! — write reads as (! ...))
  (:stop reason)          board                ; stop clause (exactly one required) — this value is the value of event-loop
  (BoardMoved :keys keys) (! (read-board-at board keys))   ; the body's value is the next state
  (TimerFired :tag tag)   (match tag "end" (stop board) _ board))   ; (stop value) exits
```

- Writing the state type (`[name type init]`) checks the initial value, each clause's value and the exited value against it, and the type checker also sees the loop's value type.
  The untyped `[name init]` can also be written (the type checker sees the loop's value as Unknown).

- The tuple of types in each clause head is passed as-is to `WaitForEvent`, so the awaited types and the handled types cannot drift apart. Events of types not handled do not reach a clause.
- The stop signal races with `AwaitStop` (only one is awaited during the loop's lifetime). A stop that arrives while a clause is being processed takes effect before the next event left in the queue.
  A stop that arrived before the loop runs the stop clause without running any event.
- A clause body is inside `do!` (`<-` and `!` are available; the contents of `(do ...)` line up as statements). The form without state lists only clauses, without writing `[name init]`.
- For each event run, `slog("event-loop", event=<type name>)` is recorded (an outer slog handler is required).
- `(stop value)` is written at the last-value position of a body (a branch of `do`, `if`, `cond`, `match`, `try`).
- Type clauses match in the order written (if a parent-type clause is written first, the parent clause also receives events of the child type).
- When exiting on the stop signal, one event taken from the queue in the middle of a wait may be discarded (a signal only says "where it changed", so
  the next worker to wake catches up by re-reading the records).
- The body is a function nested inside `do!`, so rewriting an outer `var` with `:=` does not reach outside. Pass the next state as the body's value.

Forms rejected at expansion: no stop clause or two of them / no awaited-type clause / a clause whose type cannot be derived (`_`, a bare name, `|`, a value expression) / two clauses of the same type /
a value expression in a type clause binding (do not narrow by value; branch in the body) / a clause without a body / a `stop` outside the last-value position.

## Notices across processes (notice broker)

Business Programs write only `Publish(event)` and `WaitForEvent(*types)` as before. Carrying events to another process is done by
`notice_events_handler`, and how they are carried (channel names, value encoding) is written only in the route table passed as its argument. Channel names do not appear in the Program.
What is carried is unsaved notices (Redis Pub/Sub) and notices kept only when a route declares it (Redis Streams — see "Kept channels" below).

```python
from doeff_events import (
    BrokerPatience, EventBus, MarkGap, MemoryBroker, NoticeRoute, NoticeSent, SourceMissed, SourceResumed, SourceStarted,
    broker_back_by_retry, broker_patience_handler, memory_notice_handler, notice_events_handler, redis_notice_handler,
    subscribed_event_handler,
)

routes = (
    NoticeRoute(
        event_type=OrderState,                             # the type the Program emits and waits for
        wire_name="order-state",                           # name on the wire (several types can ride the same channel)
        channel=lambda event: f"order:{event.order_id}",   # decide the destination channel from the event's value
        encode=lambda event: json.dumps(...),              # type -> string
        decode=lambda body: OrderState(...),               # string -> type
        when_unsent=MarkGap(),                             # when it did not arrive: put a gap mark on the channel (Drop() = keep nothing)
        reads=("order:o1",),                               # channels this process receives (() if it only emits)
    ),
)

# Outside -> inside. The clock handlers (GetTime, WaitWithin) are used only while the broker is unreachable.
program = subscribed_event_handler(EventBus(), "screen", (OrderState, SourceStarted, SourceResumed))(
    broker_patience_handler(BrokerPatience(seconds=900.0))(  # the limit answered to ReadBrokerPatience, asked when the reading wrapper's source stalls (one place in the assembly; no default)
        redis_notice_handler("redis://localhost:6379/0", timeout_seconds=2.0)(  # for local simulation and tests use memory_notice_handler(MemoryBroker())
            broker_back_by_retry(retry_seconds=2.0)(             # learn Redis has come back by trying whether it connects, only while waiting
                notice_events_handler("screen", routes)(body)
            )
        )
    )
)
```

- Emitting side: sends a `Publish` of a type in the route table to the broker (`PUBLISH`), and the answer is `NoticeSent(receivers)` — the number of receivers
  subscribed at that moment (0 = nobody is listening. What to do about 0 is up to the emitter. Even 1 or more is not proof that a receiver read it). Types not in the table go as they are to the outer
  handler (the queue inside the process).
- Failure to emit (handled in this one place — ADR-DOE-EVENTS-002 R5): even if it does not reach the broker, `Publish` does not raise an exception but answers with one of three closed types —
  `NoticeSent`, `NoticeGapMarked` (the route's `when_unsent` is `MarkGap` — a gap mark was put on that channel), `NoticeDropped` (`Drop` — nothing is kept,
  for things that go stale quickly). Only the channel's mark is kept, not the event itself (the amount kept is bounded by the number of channels). A gap is one fixed notice
  (the notice `Announce.gap(channel, source)` builds), and it is emitted when the broker comes back (the answer of `AwaitBrokerBack`), before the next `Publish` that goes through, and when the emitter starts
  (`MarkGap.start_channels`). Tests that read broker effects directly and fake brokers tell a gap notice apart with `Announcement.is_gap` and
  `Announce.is_gap` instead of comparing notice names (the name string stays inside — ADR-DOE-API-001 R1). The receiving wrapper turns it into `SourceMissed(source, channel)`, and the Program catches up from the records once, as with `SourceStarted` / `SourceResumed`
  (it may come any number of times). A `Publish` while another task is emitting a gap waits for that to finish before emitting (the order is not swapped).
  Once the next `Publish` has emitted all gaps, the task waiting for the broker to come back stops. When nobody can answer `AwaitBrokerBack`, that task stops, the marks
  stay until the next `Publish`, and the failure is raised when the body ends. When the body ends the marks are gone (filled by the gap notice at the next start).
- Writes with an unknown outcome: an emitter whose write's answer did not come in time and does not know whether the write arrived passes the event
  it was going to emit to `PublishGap(event)`. The event is not sent; a gap mark is put on that route's channel and emitted at once through the same outlet (answer
  `NoticeGapTold(channel)`). If the broker cannot accept it, the mark stays, as with the failure of `Publish` (`NoticeGapMarked`). A `Drop` route
  keeps nothing (`NoticeDropped`). A `PublishGap` of a type not in the table goes to the outer handler (the wrapper does not know channels).
- Receiving side: starts the subscription before the first effect of the wrapped body, and emits `SourceStarted(source)` once **after the broker has confirmed the subscription**.
  All received notices are `Publish`ed to the subscriber queue inside the process, and the Program receives them one at a time with `WaitForEvent` (what answers `WaitForEvent` is the
  outer `subscribed_event_handler` — `SourceStarted`, `SourceStalled` and `SourceResumed` are received only by a Program that names them in its subscription types).
  A notice that the route table cannot read is not discarded; the source fails with `UnroutedNotice`.
- What does not arrive: a notice reaches only readers that were subscribed and connected when it was emitted. Notices from before the subscription and while disconnected are not
  delivered later. The backend does not fill in what was lost; the premise is that the Program catches up from the records once when it receives `SourceStarted` and `SourceResumed`
  (both are emitted only after the subscription is established, so notices emitted after the catch-up read are not lost).
- Kept channels (`NoticeRoute(kept=True)`): the emitting side adds to the channel's log with `Announce(kept=True)` (for Redis,
  `XADD` of Streams; the length limit `KEPT_LOG_LENGTH` is approximate), and the receiving side reads from the log by reader name (the source's name). After emitting the event
  to the queue inside the process, the source returns `AckAnnouncement`, and the broker's handler hands over again a notice without an ack after `redeliver_seconds` (default
  `KEPT_REDELIVERY_SECONDS` = 2 seconds). After reconnecting, it reads from where the same reader left off. A notice
  lost between the broker and the queue is not undelivered but arrives later. The same event may arrive twice, so the receiver handles duplicates as it does for catch-up.
  The read position and unacked notices are held per subscription that the handler (process) opened. A closed subscription's share is taken over by the next subscription of the same name, and subscriptions of the same name
  opened at the same time all read every notice. A restarted process starts from the end of the log (it catches up from the records for what is before that).
  The routes of one channel, on both the emitting and receiving sides, declare the same `kept` (a mixed read is rejected when the wrapper is built). A subscription that reads a kept channel
  also listens to that channel's Pub/Sub, so a notice emitted without `kept` by a still-old emitter during a redeclaration also arrives (there is no receipt and it is not
  acked). The order of declaring the emitting and receiving sides does not matter.
- When the connection drops: the source emits `SourceStalled` and waits for the return with the answer of `AwaitBrokerBack` (one upper limit `WaitWithin` —
  it does not re-read on a timer). The upper limit is not an argument of the wrapper but the answer to `ReadBrokerPatience`, which the source asks once at the start of a stall
  (`BrokerPatience`), and `broker_patience_handler`, placed outside the wrapper, answers it (the same shape as doeff-records' source asking `ReadSourcePatience`).
  There is no default, and an assembly that forgot to place the answerer fails at the first stall by name as an effect with no answerer. A send-only wrapper does not ask. On return it rebuilds the subscription and,
  after the broker has confirmed, emits `SourceResumed` once and continues. Past the limit it fails with
  `NoticeSourceUnreachable` (`SourceFailed` reaches the body's wait). What answers `AwaitBrokerBack` is the handler itself in memory's handler, and in Redis
  `broker_back_by_retry(retry_seconds)` (placed inside `redis_notice_handler` and outside `notice_events_handler`) —
  no notices come from a downed Redis, so only while waiting, it merely tries "does it connect" at an interval the assembly names (no default)
  (`ProbeBroker` = `PING`; it reads and writes no data). Tries while nobody is waiting are 0 (ADR-DOE-EVENTS-002 R6).
- Slow-reading receivers: Redis cuts a subscriber's connection when the unsent notices addressed to it exceed `client-output-buffer-limit pubsub` (default 32mb 8mb 60). The
  receiver sees it as "the connection dropped" (`SourceStalled` -> `SourceResumed`), and no notice silently goes missing
  (confirmed by a test against a real redis-server 7.2.7).
- How it stops: the source's task waits on a blocking take with no time limit, and stops with `Cancel` when the body ends (`await_handler` passes the cancellation on to the
  redis-py coroutine). The body's stop is still received as `StopArrived` with `WaitForEvent`, as before.

The lower layer (the broker operation effects — `doeff_events._effects.notices`): `Announce` (answer = number of receivers), `SubscribeChannels` (answered after the broker has
confirmed), `NextAnnouncement`, `CloseSubscription`, `AwaitBrokerBack`. When the broker is unreachable, `BrokerUnreachable` is answered instead of an exception.
The answering handlers are `memory_notice_handler(MemoryBroker())` (inside the process; tests can make a stall with `cut_broker` / `restore_broker`) and
`redis_notice_handler(url, timeout_seconds)` (needs the extra `doeff-events[redis]`; settings are the URL and the upper limit `timeout_seconds` on waiting for the answer to connect and send — no default. A non-answering server or a silent network does not stall the emitter).

The delivery laws live in one place, the scenarios (Programs) of `doeff_events._notice_laws`, and both memory and a real redis-server go through the same scenarios
(declaration = `docs/adr/defadr_doeff_events_002_notice_delivery_laws.hy`). Tests against the real one start the `redis-server` on `PATH` in a temporary dir.
If the executable is missing, only that test is skipped with a reason (tests against memory always run).

## Waiting for file changes (`WatchFiles` / `NextFileChanges` / `CloseFileWatch`)

Instead of going to read at fixed intervals whether a file has grown, it wakes on the OS's change notifications.

| Entry point | Behavior |
|---|---|
| `WatchFiles(directory)` | Starts watching the entries directly under an absolute-path dir (it does not look inside subdirectories — so the cost to start does not depend on the number of entries). Changes after the answer are received by the next `NextFileChanges` (changes while not waiting are not lost either). If the dir does not exist, `WatchRefused` |
| `NextFileChanges(watch)` | Waits until the next change and answers the changed paths (create, write, delete) as `FilesChanged(paths)`. There is no time limit |
| `CloseFileWatch(watch)` | Stops the watch and discards what the handler holds |
| `os_file_watch_handler()` | Receives the OS's notifications (inotify on Linux, FSEvents on macOS) with `watchfiles`. Needs the extra `doeff-events[files]`. Placed inside `await_handler` (inside `scheduled`) |
| `memory_file_watch_handler(files)` | Handler for simulation and checks. When a writer role fires `announce_file_change(files, path)`, the watch holding that path wakes |
