# Domain Angle: Card Game Simulation & UI Handler Switching

Games are the purest demonstration of handler switching. The game logic is a Program. The UI, AI, and simulation are all handlers. Same rules, completely different execution modes.

## Real-World Evidence: card_game_2026

This isn't hypothetical. **[card_game_2026](https://github.com/...)** is a working Slay the Spire-style card game built on a CESK machine with algebraic effects. Written in Hy (Lisp on Python), it demonstrates every pattern described here — with real code running real games.

The architecture:
- **Game logic** (`combat.hy`) — pure effects. Knows nothing about CLI, TUI, or Pygame.
- **Three runtime packages** — `runtime-cli`, `runtime-textual` (TUI), `runtime-pygame` — each wiring different IO handlers to the same game logic.
- **Test harness** — `TestIOState` + `make-test-io-handlers` replace human input with scripted responses. `time_scale=0` makes all delays instant.

## Why This Is NOT Just DI

DI can swap a `UIService` for a `MockUIService`. What DI **cannot** do:

1. Run 10,000 games in milliseconds with simulated time (no `sleep`, no frame delays)
2. Pause mid-game at any effect and let a human take over from an AI
3. Replay a recorded game move-by-move with a different UI
4. Fork a game state at turn 5 and explore two different strategies

These require **controlling execution flow and time**, not just swapping service implementations.

## The Effect Hierarchy (from card_game_2026)

The game defines a clean effect hierarchy. Every game action is an effect — not a function call.

```hy
;; effects/base.hy — the hierarchy
(defclass [(dataclass :frozen True)] Effect []
  (setv effect_level 0))

(defclass [(dataclass :frozen True)] SchedulingEffect [Effect]
  (setv effect_level 0))

(defclass [(dataclass :frozen True)] StateEffect [Effect]
  (setv effect_level 1))

(defclass [(dataclass :frozen True)] UiEffect [Effect]
  (setv effect_level 2)
  (setv needs_input False))
```

IO effects — the things handlers swap:

```hy
;; effects/io.hy — platform-specific primitives
(defclass [(dataclass :frozen True)] Print [Effect]
  "Runtime handler: display text to user (print, log widget, etc.)"
  #^ str text
  #^ object category)

(defclass [(dataclass :frozen True)] ReadChoice [Effect]
  "Runtime handler: display options and get selection"
  #^ str prompt
  #^ tuple options          ; list of (value, label) tuples
  #^ bool allow_cancel)

(defclass [(dataclass :frozen True)] Delay [Effect]
  "Runtime handler: wait for specified time. Test handlers can no-op this."
  #^ float ms)
```

The game logic yields these effects. It never calls `print()`, `input()`, or `time.sleep()` directly.

## The Game Logic: Pure Effects (from combat.hy)

Combat is a program that yields effects. It spawns tasks, emits events, and waits for responses — all through effects.

```hy
;; game/combat.hy — the full combat orchestrator (unchanged across all runtimes)

(defn-domain "game-logic" combat-main [#** kwargs]
  "Main combat orchestrator. Runs the full combat loop via effects."
  (let [max-turns (.get kwargs "max_turns" 20)]
    ;; Spawn background tasks
    (yield (make-spawn state-manager-task))
    (yield (make-spawn effect-scheduler-task))

    (setv turn 1)
    (setv result None)

    (while (and (is result None) (<= turn max-turns))
      ;; Player turn — spawns a task, awaits it
      (let [player-ref (yield (make-spawn (fn [] (player-turn-task turn))))]
        (yield (Await player-ref)))

      ;; Check win condition
      (let [player-hp (yield (Get #("player" "hp")))
            enemies (yield (Get #("enemies")))
            all-dead (all (gfor #(_ e) (.items enemies) (<= (get e "hp") 0)))]
        (cond
          (<= player-hp 0) (setv result :defeat)
          all-dead (setv result :victory)))

      ;; Enemy turn (if combat still ongoing)
      (when (is result None)
        (let [enemy-ref (yield (make-spawn (fn [] (enemy-turn-task turn))))]
          (yield (Await enemy-ref)))
        ;; ... check again ...)

      (+= turn 1))

    ;; Emit combat end event
    (yield (Emit (CombatEndEvent result)))
    result))
```

Player input comes via Futures — the combat logic emits a request event, the UI task resolves the future:

```hy
;; game/combat.hy — requesting player choices
(defn request-card-choice [hand energy]
  "Request a card choice from the player via Future."
  (let [future (Future)]
    (yield (Emit (CardChoiceRequestEvent hand energy future)))
    (yield (AwaitFuture future))))
```

**The combat code has zero knowledge of how input is obtained.** It just yields and waits.

## Runtime Mode 1: Interactive CLI (from runtime-cli)

The CLI runtime uses default IO handlers — `print()` for Print, `input()` for ReadChoice, `time.sleep()` for Delay:

```hy
;; runtime-cli/cli_main.hy
(defn run-combat []
  (let [initial-state (make-initial-state)
        runtime (Runtime)]  ; Default CLI IO handlers, time_scale=1.0
    (.run runtime game-main :store initial-state)))
```

The CLI UI task spawns two concurrent CESK tasks:

```hy
;; game/ui_task.hy — cli-ui-task spawns an animation player
(defn-domain "ui-handler" cli-ui-task []
  "CLI UI task — two-task architecture for events + animations."
  ;; Initialize animation queue
  (let [queue (yield (Get #("ui" "animation-queue")))]
    (when (is queue None)
      (yield (Modify #("ui") (fn [ui] (| (or ui {}) {"animation-queue" []}))))))

  ;; Spawn animation player (consumes queue with timing delays)
  (yield (make-spawn animation-player))

  ;; Run event handler in this task
  (yield :from (ui-event-handler)))
```

The animation player polls a queue and prints with delays — it yields `Delay` effects:

```hy
(defn-domain "ui-handler" animation-player []
  "Consumes animation queue with timing delays. Polls at ~60fps when idle."
  (while True
    (let [queue (yield (Get #("ui" "animation-queue")))]
      (if (and queue (> (len queue) 0))
          (let [item (get queue 0)
                text (.get item "text" "")
                delay (.get item "delay" 0)]
            (yield (Modify #("ui" "animation-queue") (fn [q] (cut q 1 None))))
            (yield (make-print text))
            (when (> delay 0)
              (yield (make-delay delay))))
          ;; Queue empty — short poll delay
          (yield (make-delay 16))))))
```

## Runtime Mode 2: Pygame GUI (from runtime-pygame)

Same `combat-main`. Same `cli-ui-task`. Different IO handlers:

```hy
;; runtime-pygame/main.hy — wires pygame handlers
(defclass PygameRuntime []
  (defn create-io-handlers [self]
    (defn handle-print [effect state]
      "Print → add message to pygame overlay"
      (.add-message (. self renderer) (. effect text))
      (._render-frame self)
      #(None state))

    (defn handle-read-choice [effect state]
      "ReadChoice → pygame click selection on rendered cards"
      (setv result None)
      (while (and (is result None) (. self running))
        (for [event (pygame.event.get)]
          (cond
            (= (. event type) pygame.MOUSEBUTTONDOWN)
              (let [pos (. event pos)
                    clicked-card (.get-card-at-pos (. self renderer) pos)
                    clicked-button (.get-button-at-pos (. self renderer) pos)]
                (when clicked-card
                  (for [#(value label) (. effect options)]
                    (when (= value clicked-card)
                      (setv result value))))
                (when (= clicked-button "end_turn")
                  ;; ... handle end turn ...))))
        (._render-frame self)
        (pygame.time.delay 16))
      #(result state))

    {Print handle-print
     ReadChoice handle-read-choice})

  (defn run [self program store]
    (let [io-handlers (.create-io-handlers self)
          ui-handler (.create-ui-handler self)
          runtime (Runtime :io_handlers io-handlers
                          :ui_handler ui-handler
                          :time_scale 1.0)]
      (.run runtime program :store store))))
```

**The game logic is literally the same code.** Only the handler wiring changes.

## Runtime Mode 3: Tests (from test_io_handlers.hy)

The test runtime replaces IO handlers with scripted responses and captures output:

```hy
;; runtime/test_io_handlers.hy

(defclass [(dataclass)] TestIOState []
  "State for test IO handlers."
  #^ list output         ; Captured Print output: [(text, category), ...]
  #^ list script         ; Scripted responses for ReadChoice
  #^ int script-idx      ; Current position in script

  (defn [staticmethod] create [script]
    (TestIOState [] (list script) 0)))


(defn make-test-io-handlers [state]
  "Create IO handlers that use the given TestIOState."

  (defn handle-print [effect cesk-state]
    "Capture print output to state.output"
    (.append (. state output) #((. effect text) (. effect category)))
    #(None cesk-state))

  (defn handle-readchoice [effect cesk-state]
    "Return next scripted response"
    (when (>= (. state script-idx) (len (. state script)))
      (raise (RuntimeError f"Test script exhausted at index {(. state script-idx)}")))
    (let [response (get (. state script) (. state script-idx))]
      (+= (. state script-idx) 1)
      #(response cesk-state)))

  {Print handle-print
   ReadLine handle-readline
   ReadChoice handle-readchoice})
```

And the scripted UI task — same event handling as `cli-ui-task`, but with scripted responses instead of human input:

```hy
;; game/ui_task.hy — scripted-ui-task for testing

(defn-domain "ui-handler" scripted-ui-task [script]
  "Scripted UI task for testing — predetermined responses, no animation delays."
  (setv script-idx 0)

  (while True
    (let [event (yield (WaitEvent [
          CardChoiceRequestEvent TargetChoiceRequestEvent
          ;; ... all the same events as cli-ui-task ...
          ]))]

      (cond
        ;; Input requests — use script instead of human
        (isinstance event CardChoiceRequestEvent)
          (do
            (when (>= script-idx (len script))
              (raise (RuntimeError "Test script exhausted")))
            (let [response (get script script-idx)]
              (+= script-idx 1)
              (.resolve (. event future) response)))

        ;; Other events — ignore in test mode
        True
          None))))
```

## The time_scale Parameter: Controlling Time Itself

The Runtime has a `time_scale` parameter that controls how `Delay` effects are handled:

```hy
;; cesk/runtime.hy — Runtime constructor
(defclass Runtime []
  (defn __init__ [self * [time-scale 1.0] ...]
    "Args:
      time-scale: Time scale for delays (0 = instant, 1 = real time)
    "
    (setv (. self time-scale) time-scale))

  (defn _handle-delay [self state task-id suspended effect]
    "Delay is scaled by time_scale:
     - time_scale=0: instant (for tests)
     - time_scale=1: real time (for CLI)
     - time_scale=0.5: half speed"
    (let [ms (* (. effect ms) (. self time-scale))]
      (when (<= ms 0)
        ;; Resume immediately — no actual waiting
        (let [resumed-state (.resume suspended None (. state store))]
          (return (._merge-task-state self state task-id resumed-state))))
      ;; Otherwise, schedule timer wake-up
      ...)))
```

This is what DI **cannot** do. A DI container can swap `TimeService` for `MockTimeService`, but:
- Every function that calls `time.sleep()` still calls it — you'd have to mock every call site
- The DI container has no concept of "time scale" — it's all-or-nothing
- With effects, `time_scale=0` makes the **entire game** run with zero delays, without changing any game logic

Test configuration:

```hy
;; time_scale=0 — all Delay effects resolve instantly
(let [runtime (Runtime :time_scale 0
                       :io_handlers (make-test-io-handlers test-state)
                       :strict True)]
  (.run runtime game-main :store initial-state))
```

CLI configuration:

```hy
;; time_scale=1 — real-time delays for animations
(let [runtime (Runtime)]  ; defaults to time_scale=1.0
  (.run runtime game-main :store initial-state))
```

Same combat code. Same UI task code. 10,000 games in seconds vs. one playable game with animations.

## What This Proves

```
Same combat-main program, different runtime configurations:

  Runtime()                                    → playable CLI game (real-time)
  Runtime(:io_handlers pygame_handlers)        → playable Pygame game (graphical)
  Runtime(:time_scale 0, :io_handlers test_io) → instant test run (scripted)
  Runtime(:time_scale 0) + scripted-ui-task    → 10k simulations for balance
  Runtime() + replay-ui-task                   → recorded game replay
  Runtime() + llm-ui-task                      → AI plays the game
```

The game logic never changes. Not the combat orchestrator, not the card effects, not the turn structure. Only the handlers change.

### What DI Would Require

To achieve this in a DI-based architecture:
- A `UIService` interface with mock implementations → covers handler swapping, but...
- A `TimeService` interface injected into every function that does timing → misses time scaling
- Custom "simulation mode" booleans threaded through the call stack → ad hoc
- A separate "replay" service that knows the full game loop → doesn't compose
- No way to pause mid-combat and switch from AI to human → impossible without coroutines/effects

With effects, each of these modes is a **different handler stack**. The game is one program. The modes are configuration.

## The Pitch

> "Write your game logic once as effects. Run it as an interactive CLI game, a Pygame GUI, a 10,000-game simulation for balance testing, or an automated test suite — all by swapping handler configurations. The game rules never change. Only how they're executed."

**This isn't a thought experiment. It's shipping code.**
