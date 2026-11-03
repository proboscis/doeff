;;; Executable ADR: launch-stage "ready" is defined by pasteability, and failure is
;;; classified in a closed vocabulary. An undelivered attempt is its own class (prompt_undelivered).
;;;
;;; Root fix for ACP issue sessionhost-launch-ready-gate-vs-timeout-f0ac0f
;;; (adopted fix-launch-ready-gate. Later, the second root fix "unsubmitted-prompt rescue"
;;; from the sessionhost liveness change [issue 55b1bd] was merged
;;; into this one. Decision document decision-acp-sessionhost-liveness-55b1bd-2026-08-12.html,
;;; item second-rootcause, option A).
;;;
;;; Coupling core: the entry-side closure of ACP core-04 (liveness closure) +
;;; the attempt boundary of core-13 (invocation execution model) —
;;; a lane that must ship law, counterexamples, tests, and implementation together (frontier + human review).
;;; Direction declaration launch-ready-paste-consumption-f0ac0f (final).

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-011
  :title "The launch ready gate is defined not as 'the input field was drawn' but as 'this composer consumes our paste and can clear it' (input loop wired ∧ no unsubmitted content ∧ a bounded probe consumed and cleared). Failure self-describes with a closed-vocabulary class and keeps a bounded set of several evidence frames. An attempt whose prompt was never delivered is prompt_undelivered (its own class, retryable=true), not timed_out"
  :status "accepted"
  :scope ["packages/doeff-agents/src/doeff_agents/_sessionhost/launch.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/policy.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/effects.hy"
          "packages/doeff-agents/src/doeff_agents/_sessionhost/impls/markers.hy"
          "packages/doeff-agents/tests/sessionhost_launch_deftests.hy"]
  :problem
    [(fact
       "The launch ready gate terminated the 120s budget exhaustion with a single fixed message and kept no classification of what was visible. The message asserted 'startup is blocked by an unrecognized screen (a dialog outside the R9 fast-path set?)' every time, but classifying the 341 real frames from the 2026-08-11 snapshot gave 0 unrecognized screens (A), 24 with nothing drawn (B), 314 with only shell echo (B'), and 3 whose final frame was already ready (budget race). The assertion was wrong in every case and misled the steward's diagnosis every time."
       :evidence "direct read of agentd.sqlite (classification of the output_snippet of terminal_cause_json like '%launch ready gate%' through the detector functions in impls/markers.hy) + implementation request v1+reqa3c6cd70 for ACP issue sessionhost-launch-ready-gate-vs-timeout-f0ac0f")
     (fact
       "'Nothing drawn' and 'only shell echo' could not be told apart afterwards. The old implementation kept the raw last 15 lines of the capture as evidence; shell echo sits at the top of the screen, so the tail was only blank lines — the evidence of 24 measured cases was '14 newlines', the same shape as an empty screen. Only 1 frame was kept, so the phase (right after launch / just before budget exhaustion) could not be distinguished."
       :evidence "raw bytes of output_snippet in the same snapshot (every len=14 case is newlines only)")
     (fact
       "'timeout' is not one kind but three, and counting by category=timed_out mixes them: launch ready gate (341) / never reached active state within 60s (36) / launch pipeline did not complete within 180s (4). The ordering seat actually produced contaminated numbers (wrongly inflating 07-26 from 2→6 and 07-27 from 16→25). The knowledge distinguishing the 3 kinds was nowhere in the code, only in people's heads."
       :evidence "per-terminal_cause_json.$.reason tally of the same snapshot + acceptance criterion (e) of the implementation request, verbatim")
     (fact
       "Seats exist where a prompt pasted immediately upon seeing the idle prompt sits in the composer as a collapsed chip, the turn never starts, and the seat dies after using up 5 Enter resends (25 cases on 2026-08-11). In all 25 cases the turn-activity marker was absent, and in 12 cases there were 3–10 chips = a single bracketed paste landed fragmented. Extending the resend count offers no rescue (seats rescued within 1–4 resends survived 522/522; of the 38 that used all 5, 25 died = bimodal)."
       :evidence "all 25 rows of agentd.sqlite with last_validation_error like 'unsubmitted-prompt:%' + survival rate by number of session_unsubmitted_paste_resubmitted events (1: 415 / 2: 59 / 3: 32 / 4: 16 all survived; of 38 with 5, 25 terminated as unsubmitted)")
     (fact
       "observed_active_at (the launch watchdog's release signal) is raised from startup-finished, and startup-finished becomes true on the idle prompt being visible alone. So the same single frame — 'the input field was drawn' — passes the ready gate, releases the watchdog, and stamps observed_active_at — all 25 cases had observed_active_at while the turn never started. A column name that reads as 'activity observed' meaning only 'input field observed' made launch-stage failures look the same as seats that did work."
       :evidence "cross-check of observed_active_at of the same 25 cases (all landed at started_at +14–130s) against the absence of the turn-activity marker in the death frames")
     (fact
       "The gate form (ready_physics CODEX-READY-PATTERN) excluded the MCP boot window from ready from the start with a negative lookahead, while the observation form (the sessionhost ready gate) claimed ready on the idle prompt alone. The same physics — 'the composer is drawn while Starting MCP servers (N/M) is still showing, but the input loop is not yet wired; keys sent in that window have their Enter eaten by the loading screen, and the prompt sits in the input box without being submitted' — was in effect in only one of the 2 forms (a hole in the two-form agreement of ADR-DOE-AGENTS-008 R1)."
       :evidence "passing the verbatim capture packages/doeff-agents/tests/data/ready_screens/codex_mcp_boot.txt through classify-output gives has-idle-prompt=True (the old gate lets it through)")]
  :context
    [(interpretation
       "Shape of readiness: readiness is a proposition about 'whether this pane's input reader is consuming our bytes', not 'what was drawn on the screen'. Drawing and wiring the input loop are separate events, and a TUI shows the former first. The only positive evidence is observing that a short probe we sent appears in the composer and disappears with the clear key — no drawing proxy (banner gone, rules drawn, screen settled) proves consumption. The probe runs inside the same budget and does not extend the launch-stage wall clock (keeping ACP ADR 0042 R8's ordering invariant ready gate 120s < orphan grace 150s < scheduler launch deadline 180s).")
     (interpretation
       "The definition of 'composer is empty' is limited to the absence of unsubmitted chips. Do not add an empty prompt-line body as a condition — codex's ready composer draws a rotating placeholder on the prompt line (`› Improve documentation in @filename` etc.), so requiring an empty string makes every codex launch fail structurally (demonstrated with the verbatim capture codex_ready.txt). The only stable physics is the chip vocabulary of ADR-DOE-AGENTS-010 R1.")
     (interpretation
       "Shape of classification: failure always self-describes with a closed-vocabulary class. Asserting a cause with fixed prose claims a state that was not observed (an 'unrecognized screen' measured at 0 cases had been stamped on 341), and systematically misleads downstream diagnosis. Classification is derived from the fact bundle (PaneObservation); marker→fact is owned by impl, ordering and meaning are owned by policy (not crossing the ADR-DOE-AGENTS-004 C1/C2 split). The classification input is the whole capture, not the retained tail — looking only at the tail pushes shell echo out of the window and misclassifies it as 'nothing drawn'.")
     (interpretation
       "The shape of compensation does not change: post-delivery unsubmitted detection and bounded Enter resend remain owned by the level-triggered arm of ADR-DOE-AGENTS-010 R2 (010 deliberately rejected extending the launch-side synchronous confirm, as it widens the blind window of hazard 4). What this ADR adds is a necessary condition *before* delivery, completed within the gate's budget. So its relation to 010 R2 is not competition but successive stages — the stronger the gate becomes, the more R2's arm becomes a backstop.")
     (interpretation
       "Shape of non-delivery: an attempt whose prompt never arrived is a different fact from 'ran but timed out'. While they are mixed into the same category (timed_out), downstream (ACP) cannot express mechanically that an attempt which never ran a single turn is not counted as 'ran', and the tally also splits across 2 paths (launch-stage gate / post-delivery budget overrun). retryable=true is kept — a retry on another machine or under another load is not a double implementation (there are real cases where redeclaring the same issue succeeds). Only the provider-limit notice screen is distilled into rate_limited (the owner of the failure is the provider side; lumping it under undelivered means ACP ADR 0049's failover cannot take it, and retries pile onto the same exhausted quota).")
     (interpretation
       "Home of the distinguishing knowledge: knowledge for counting terminal reason kinds (3 timeouts + undelivered + awaiting) rots when each reader (sensor / tally / post-mortem) rewrites its own regex. A single function on the producing side owns the classification, and the reason carries a closed-vocabulary class token. The verbatim phrases existing observation surfaces depend on (`did not become ready` / `launch ready gate`) are preserved as prefixes — the argus sensor and conformance S18/S22 pick up launch-stage failures by those substrings, so a full rewrite of the prose would silence the instruments.")]
  :decision
    [(rule R-paste-ready-a71c "The ready gate has 3 necessary conditions: (1) input loop wired (idle prompt visible ∧ not during MCP boot — markers.hy is-input-loop-wired), (2) no unsubmitted content in the composer (is-composer-clear = the composer region is drawn ∧ no chips per ADR-DOE-AGENTS-010 R1; an empty prompt-line body is not a condition), (3) pasteability (bracketed-paste READY-PROBE-TEXT and observe that it appears in the composer region [both the literal form and the collapsed chip form count as evidence of consumption], and that sending READY-PROBE-CLEAR-KEY once per probe character returns the composer to empty). The 3 conditions are judged inside the same max-wait budget, without extending the budget.")
     (rule R-frame-class-6f3d "A launch-stage failure self-describes with one of the closed vocabulary LAUNCH-NOT-READY-CLASSES (no-output / conversation-not-found / no-agent-frame / provider-limit-screen / dialog-not-dismissed / unknown-dialog / unrecognized-screen / mcp-boot-window / composer-occupied / paste-not-consumed / composer-not-clearable / deadline-race). On the budget-exhaustion side, the single home of classification is policy's launch-not-ready-class, which derives it from PaneObservation + the whole capture, and the reason is built by launch-not-ready-reason (`launch ready gate [<class>]: <agent> REPL did not become ready within <budget>s; the prompt was never delivered`). Returning fixed prose that asserts a cause ('unrecognized screen' etc.) is forbidden. [Revised 2026-08-18: added conversation-not-found — the screen where the CLI could not resolve the conversation to resume and exited loudly (claude 'No conversation found with session ID' / codex 'no rollout found for thread id', verbatim from resume-physics.md probes (a)(c)). This screen is not a proposition that waiting resolves, so the gate terminates immediately without waiting out the budget, like provider-limit-screen. Measured 2026-08-16–17: all 91 cases of this shape waited 120s and were misclassified as no-agent-frame (layer 2 of ADR-DOE-AGENTS-006 R10). The category stays prompt_undelivered (the fact of non-delivery is unchanged — the root fix is the admission check of 006 R10, and this class is the honest name for TOCTOU and unknown paths).]")
     (rule R-evidence-frames-9c17 "On failure, a bounded set of several evidence frames is kept (READY-GATE-FRAME-RETENTION=3, READY-GATE-FRAME-LINES=15 lines per frame). Retention pins the first frame (right after launch) and rotates the newest side, and each frame carries the seconds elapsed since the gate started. Trailing blank lines are dropped before taking the tail (a raw tail squashes a shell-echo-only screen into 'only newlines', indistinguishable from nothing drawn). Taking an extra capture after budget exhaustion and keeping only that 1 frame is forbidden — keep the screens the gate saw.")
     (rule R-unknown-dialog-loud-4e02 "On observing a dialog shape outside the R9 fast-path (markers.hy is-dialog-shaped: numbered options right after the prompt glyph / confirmation wording), fail loudly at once without guessing a dismissal key (class=unknown-dialog). Do not loosen this judgment by the presence of an idle prompt — codex's trust dialog uses `›` as its selection marker, so it passes the idle prompt check. If a provider-limit notice (api-limit marker) is observed right after launch, it is class=provider-limit-screen and category=rate_limited (policy's launch-not-ready-category is the only home).")
     (rule R-undelivered-first-class-b5e8 "Add prompt_undelivered (retryable=true) to the TerminalCause categories, and use it for terminations where the prompt never arrived: (a) launch-stage gate failure (every class except provider-limit-screen), (b) paste-resend budget overrun of ADR-DOE-AGENTS-010 R2 (that R2's `cause timed_out` is revised here — if the api-limit latch is set, it stays rate_limited). Launch does not write running until delivery is done (an undelivered attempt is not counted as 'ran'). Downstream's default for an unknown category is CommandNonZeroExit + causeRetryable, so this is harmless against old engines too.")
     (rule R-timeout-kind-single-home-3c5f "The single home for classifying the kinds of terminal reasons that sessionhost produces is policy's SESSIONHOST-TIMEOUT-KINDS / sessionhost-timeout-kind (launch-ready-gate / launch-never-active / launch-pipeline-incomplete / unsubmitted-prompt / awaiting-response). Readers do not reimplement this classification. The verbatim phrases existing observation surfaces depend on (`did not become ready` / `launch ready gate` / `unsubmitted-prompt:` / `awaiting-response timeout:`) are preserved as prefixes. When a producing site is added, register it in this table at the same time — out of vocabulary (unclassified) is a gate error.")]
  :laws
    [(law launch-ready-requires-consumed-paste
       :statement "ready(pane) => input_loop_wired(pane) and composer_clear(pane) and probe_consumed(pane) and probe_cleared(pane); drawn_composer_alone_is_not_ready; probe_within_same_budget"
       :counterexamples
         [(counterexample "The 25 seats of 2026-08-11: judged ready on a single idle-prompt frame and pasted a 1238-line prompt — the composer received it as a chip (fragmented into 3–10 pieces on 12 seats), Enter was eaten in the gaps between fragments, the turn never started, and after using up 5 resends they died with unsubmitted-prompt")
          (counterexample "Treating the MCP boot window (`Starting MCP servers (1/2)` + `› `) as ready — the gate form excluded the same physics from the start, but the observation form alone let it through, and Enter was eaten by the loading screen (verbatim capture codex_mcp_boot.txt)")
          (counterexample "Adding 'no characters on the prompt line' as a condition for an empty composer — codex's ready composer draws a rotating placeholder, so every codex launch fails (verbatim capture codex_ready.txt)")
          (counterexample "Putting the wait for probe consumption outside the budget (extending the gate's wall clock) — ACP ADR 0042 R8's ordering invariant (ready gate < orphan grace 150s < launch deadline 180s) breaks, and a normal cold start is misclassified as orphan")])
     (law launch-not-ready-is-classified
       :statement "not_ready(pane) => failure_class in LAUNCH_NOT_READY_CLASSES and evidence_frames(1 < n <= 3) and classification_input_is_full_capture; fixed_prose_diagnosis_is_forbidden"
       :counterexamples
         [(counterexample "The old implementation's fixed message 'startup is blocked by an unrecognized screen (a dialog outside the R9 fast-path set?)' — 0 matching cases out of 341 measured (nothing drawn 24 / only shell echo 314 / budget race 3). It asserted an unobserved state every time")
          (counterexample "Using the retained tail (15 lines) as the classification input — shell echo sits at the top of the screen, so it falls out of the window and is misclassified as 'nothing drawn' (all 314 measured cases go wrong)")
          (counterexample "Keeping only the final frame as evidence — what was visible right after launch is lost, and the single frame just before budget exhaustion is not enough to distinguish (A)/(B) (measured: 24 tails of only blank lines)")])
     (law undelivered-attempt-is-its-own-class
       :statement "prompt_never_delivered(attempt) => category = prompt_undelivered and retryable = true and not counted_as_ran; provider_limit_screen => category = rate_limited"
       :counterexamples
         [(counterexample "Mixing undelivered into timed_out — downstream (ACP) cannot distinguish 'ran but timed out' from 'not a single turn ran', and acceptance criterion (d) 'do not count a session that terminated with the prompt undelivered as having run' cannot be expressed mechanically. The tally also splits across 2 paths: launch-stage gate and post-delivery budget overrun")
          (counterexample "Lumping the provider-limit notice screen under prompt_undelivered — ACP ADR 0049's failover cannot take it, and retries pile onto the same exhausted quota (measured: 16 of 283 screens of running seats had this notice shape)")
          (counterexample "Making undelivered retryable=false (deterministic) — load-induced launch-stage failures succeed at another time slice, so work that could be redeclared gets stuck at the human gate")])
     (law timeout-kind-has-one-home
       :statement "terminal_reason(sessionhost) => classifiable_by(sessionhost_timeout_kind) and prefix_verbatim_preserved; readers_must_not_reimplement_the_split"
       :counterexamples
         [(counterexample "Counting the daily figures by category=timed_out alone — the 3 kinds (launch ready gate 341 / never-active 36 / pipeline-incomplete 4) get mixed. The ordering seat actually inflated 07-26 from 2→6 and 07-27 from 16→25")
          (counterexample "Rewriting the reason prose entirely and dropping the verbatim `did not become ready` / `launch ready gate` — the argus sensor (launch-ready-gate-failure in scripts/sensor_sessionhost_health.py) and conformance S18/S22 go silent at once")])]
  :enforcement
    [(deftest test-adr-doe-agents-011-gate-vocabulary-and-knobs-frozen
       ;; R-paste-ready-a71c / R-frame-class-6f3d / R-evidence-frames-9c17 /
       ;; R-undelivered-first-class-b5e8, machine side: the closed vocabulary, probe physics, retention count,
       ;; and category table exist as declared.
       (import doeff_agents._sessionhost.effects :as effects-mod)
       (import doeff_agents._sessionhost.policy :as policy-mod)
       (assert (= effects-mod.READY-PROBE-TEXT "doeff-ready-probe"))
       (assert (= effects-mod.READY-PROBE-CLEAR-KEY "BSpace"))
       (assert (= effects-mod.READY-GATE-FRAME-RETENTION 3))
       (assert (= effects-mod.READY-GATE-FRAME-LINES 15))
       ;; The budget stays in the knob vocabulary's home (ADR-DOE-AGENTS-008 R2)
       (assert (= effects-mod.REPL-IDLE-MAX-WAIT-SECONDS 120))
       (assert (= policy-mod.LAUNCH-NOT-READY-CLASSES
                  #("no-output" "conversation-not-found" "no-agent-frame"
                    "provider-limit-screen"
                    "dialog-not-dismissed" "unknown-dialog" "unrecognized-screen"
                    "mcp-boot-window" "composer-occupied" "paste-not-consumed"
                    "composer-not-clearable" "deadline-race")))
       ;; Undelivered is its own category, retryable=true
       (assert (= (get policy-mod.TERMINAL-CAUSE-RETRYABLE "prompt_undelivered") True))
       ;; Only the provider limit is distilled into rate_limited (single home)
       (assert (= (policy-mod.launch-not-ready-category "provider-limit-screen")
                  "rate_limited"))
       (assert (= (policy-mod.launch-not-ready-category "no-agent-frame")
                  "prompt_undelivered"))
       (assert (= (policy-mod.launch-not-ready-category "paste-not-consumed")
                  "prompt_undelivered"))
       ;; Conversation resolution failure also stays undelivered (the root fix is 006 R10's admission — this class
       ;; is an honest name for the fact, not a new category)
       (assert (= (policy-mod.launch-not-ready-category "conversation-not-found")
                  "prompt_undelivered")))
     (deftest test-adr-doe-agents-011-classification-covers-measured-shapes
       ;; R-frame-class-6f3d, machine side: the screen shapes that existed in the 2026-08-11 snapshot
       ;; each map to a different class (so that a category with 0 samples is not called "implemented",
       ;; the shape absent from live data [unknown-dialog] is exercised with a synthetic sample).
       (import doeff [run])
       (import doeff_agents._sessionhost.impls.markers [classify-output])
       (import doeff_agents._sessionhost.policy [launch-not-ready-class])
       (setv shapes
             {"no-output" ""
              ;; Shape of the 314 measured cases (echo of the launch command + zsh's notice)
              "no-agent-frame"
                (+ "The default interactive shell is now zsh.\n"
                   "CA-1:inv_wi_x s22625$ claude --dangerously-skip-permissions "
                   "--model claude-opus-5")
              ;; Same shape as the verbatim capture codex_mcp_boot.txt
              "mcp-boot-window" "Starting MCP servers (1/2)\n› "
              "composer-occupied" "› [Pasted text #1 +1238 lines]"
              ;; Synthetic sample (0 cases among the 367 live ones)
              "unknown-dialog"
                (+ "Do you trust the files in this folder?\n"
                   "› 1. Yes, proceed\n  2. No, exit\n"
                   "Enter to confirm · Esc to cancel")
              ;; Notice shape that actually appeared on 16 of 283 screens of running seats
              "provider-limit-screen"
                (+ "What do you want to do?\n"
                   "› 1. Stop and wait for limit to reset\n"
                   "  2. Ask your admin for more usage\nEnter to confirm")
              ;; 3 measured cases (ready on the final frame = race between budget and drawing)
              "deadline-race" "────────────────\n❯ \n────────────────"
              ;; Shape of the 91 measured cases (the 2026-08-16–17 total resume failure — shell echo of a pane
              ;; that came up in $HOME because its work_dir was gone + claude's loud death)
              "conversation-not-found"
                (+ "The default interactive shell is now zsh.\n"
                   "CA-1:~ s22625$ claude --dangerously-skip-permissions "
                   "--resume 6129f510-70f3-4cac-a29c-3956d5c925a0\n"
                   "No conversation found with session ID: "
                   "6129f510-70f3-4cac-a29c-3956d5c925a0\n"
                   "CA-1:~ s22625$")})
       (for [[expected frame] (.items shapes)]
         (setv obs (run (classify-output frame)))
         (setv actual (launch-not-ready-class obs frame))
         (assert (= actual expected)
                 f"frame classified as {actual}, not {expected}")))
     (deftest test-adr-doe-agents-011-timeout-kind-is-total
       ;; R-timeout-kind-single-home-3c5f, machine side: none of the real reasons from producing sites
       ;; falls into unclassified + the verbatim phrases existing observation surfaces depend on remain.
       (import doeff [run])
       (import doeff_agents._sessionhost.policy [LAUNCH-NOT-READY-CLASSES
                                                launch-not-ready-reason
                                                sessionhost-timeout-kind])
       ;; (i) launch-stage gate: the reason of every class falls into launch-ready-gate
       (for [cls LAUNCH-NOT-READY-CLASSES]
         (setv reason (run (launch-not-ready-reason "claude" 120 cls)))
         (assert (= (run (sessionhost-timeout-kind reason)) "launch-ready-gate") reason)
         (assert (in f"[{cls}]" reason))
         ;; keeps the verbatim phrases the argus sensor / conformance pick up
         (assert (in "did not become ready" reason))
         (assert (in "the prompt was never delivered" reason)))
       ;; (ii)(iii) verbatim phrases of the monitor-side watchdog (2 sites in policy.hy)
       (assert (= (run (sessionhost-timeout-kind
                         "launch timeout: never reached active state within 60s (stuck in startup — likely a hung MCP server)"))
                  "launch-never-active"))
       (assert (= (run (sessionhost-timeout-kind
                         "launch timeout: launch pipeline did not complete within 180s (BOOTING row left behind — launcher died mid-launch?)"))
                  "launch-pipeline-incomplete"))
       ;; the 2 sites of ADR-DOE-AGENTS-010 R2/R3
       (assert (= (run (sessionhost-timeout-kind
                         "unsubmitted-prompt: composer still holds an unsubmitted prompt/attachment after 5 Enter resubmit(s)"))
                  "unsubmitted-prompt"))
       (assert (= (run (sessionhost-timeout-kind
                         "awaiting-response timeout: prompt/solicitation was delivered but no work evidence appeared within 600s (turn never started)"))
                  "awaiting-response"))
       ;; Out of vocabulary is unclassified (material for a gate error — never silently passed)
       (assert (= (run (sessionhost-timeout-kind "some other terminal reason"))
                  "unclassified")))
     (deftest test-adr-doe-agents-011-retired-fixed-prose-cannot-return
       ;; R-frame-class-6f3d tripwire: forbids regression to the asserting message that matched 0 of 341
       ;; measured cases, and to the old gate without classification (wait-for-repl-idle returning a bool).
       (import pathlib [Path])
       (import doeff_agents._sessionhost.launch :as launch-mod)
       (setv source (.read-text (Path (. launch-mod __file__)) :encoding "UTF-8"))
       ;; Only code lines are checked — comments may quote the phrase verbatim as the history of its
       ;; retirement (being able to trace why it was removed has value). Only produced messages are forbidden.
       (setv code (.join "\n" (lfor line (.splitlines source)
                                   :if (not (.startswith (.lstrip line) ";;"))
                                   line)))
       (assert (not-in "unrecognized screen (a dialog outside" code)
               "the retired asserting message is back in launch.hy code lines (it matched 0 of 341 measured cases)")
       (assert (in "wait-for-launch-ready" source))
       (assert (not-in "(wait-for-repl-idle " source)
               "a call to the old gate without classification remains")
       ;; has not gone back to the old form that keeps only 1 frame from an extra capture after budget exhaustion
       (assert (in "format-evidence-frames gate.frames" source)))])
