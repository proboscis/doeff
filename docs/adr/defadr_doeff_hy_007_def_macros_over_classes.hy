;;; Executable ADR: declarations that have a name and are read by tools are written with def*. Classes that touch the outside world (client, store) and classes
;;; with changing state (fake stores, caches) are not created; they go into foundation handlers and session val / var. Value classes (Point2D) are allowed.
;;;
;;; The decisions are in the :problem facts. The proposal to choose def* over classes was adopted, and the target was then narrowed twice (classes in general → classes with behavior →
;;; classes that touch the outside world and classes with changing state; value classes are allowed). The judgment is by evidence in the content, not by name, and no reason annotation is needed.
;;;
;;; Placement decision: this was not appended to ADR-DOE-HY-004 (function vocabulary is defk only) but made a new ADR file. HY-004 is the file holding the vocabulary of function
;;; definitions (defn / deff / defk) and the ledger of deff without reason annotations; type and class definitions are a separate axis — mixing them in the same file
;;; would spread the ledger, probes, and laws over 2 axes. This file pairs with HY-004 (functions) and HY-005 R5 (macros live only in doeff-hy).
;;;
;;; Addendum (2026-09-28): the smell rules (doeff-linter DOEFF121–125, Jev's DOEFF205) and the failure-type mark
;;; (defrecord's :failure True) were added as R9 and R10 (and Jev's R11). Placement decision: ADR-DOE-CORE-EFFECTS-003 (Absent / Raise) was, on the same day,
;;; in the middle of a large rewrite by another change (wt/absent-raise-impl), and adding clauses to the same file would conflict at landing. The smell rules continue this file's R1,
;;; "write as declarations tools read (def*)" — the failure-type mark is a declaration placed in defrecord's head dict, which the linter reads without executing.
;;;
;;; Rollback: revert the commit that added this ADR (one ADR file disappears. DOEFF119 and defrecord's :tags / :check are
;;; implemented by other changes, so revert those commits separately).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val var <-])
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass FrozenInstanceError])
(import doeff_hy._wire [parse dump Malformed])


;; Live probe — a data type is built in one line with defrecord, and runtime values are made by calling that type.
(defrecord ProbeBudgetRow
  #^ str name
  #^ int used
  #^ int limit)

;; Live probe (R8) — a JSON boundary type is declared with defwire, decoded and checked with parse, and turned back into JSON with dump.
(defwire ProbeBudgetWire
  "One budget row (JSON boundary)"
  {:names :camel}
  #^ str name
  #^ int used-units)


(defadr ADR-DOE-HY-007
  :title "Declarations that have a name and are read by tools (linter, index, editor, type checker) are written with def* (placed in doeff-hy, with reading rules). Classes that touch the outside world (raw side effects in methods, resources held in fields — client, store) are replaced by foundation handlers (resources in session val); classes with changing state (methods rewrite self — fake stores, caches) are replaced by a handler's session var. Value classes (fields do not change and methods only compute from fields — Point2D), exceptions, Enums, Protocols, and subclasses of external bases are allowed. defrecord is recommended for field-only types. The judgment is by evidence in the content, not by name (doeff-linter DOEFF119; no reason annotation needed)"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_hy_007_def_macros_over_classes.hy"
          "packages/doeff-hy/src/doeff_hy/record.hy"
          "packages/doeff-hy/src/doeff_hy/_wire.hy"
          "packages/doeff-hy/src/doeff_hy/_json_value.py"
          "packages/doeff-linter"
          "packages/doeff-linter/src/project/smells.rs"]
  :problem
    [(fact
       "Whether to keep choosing def* macros over creating classes was examined, and it was decided to proceed with def*.")
     (fact
       "Narrowing the target: defclass itself may be used, but in doeff + Hy code there is no reason to make something like a client a class. Values such as Point2D / Point3D may be classes; clients and stores are written as handlers / effects.")
     (fact
       "Remaining question: pure data such as ASTs and numbers may be data classes, and whole-system services are always written as handlers / effects. Then how are stateful things such as game characters written?")
     (fact
       "Measured: agora-controllers has defclass 848 and defrecord 338 (coordinator's count, 2026-09-27). This session's count on the same day (git grep occurrences, origin/main 380ae943) is defclass 819 and defrecord 414. doeff (origin/main 772a4405) has defclass 614 and defrecord 98."
       :evidence "git grep -c '(defclass' / '(defrecord' -- '*.hy'")
     (fact
       "defrecord is a template macro owned by doeff-hy that builds, in one line, a frozen dataclass declaring field names and types. It currently has no :tags or :check clause — types that need checks are written as plain defclass with __post_init__."
       :evidence "packages/doeff-hy/src/doeff_hy/record.hy (defrecord), ADR-DOE-HY-005 R4")
     (fact
       "On 2026-09-28 it was decided to add smell-detecting rules to doeff-linter. The subject was decide-tag in agora-controllers' controllers/kanban/core/tag_judgment.hy; at the time of the decision the linter reported nothing on this file.")
     (fact
       "The shape of decide-tag (agora-controllers 068dee21): inside a core judgment it checks (.get payload \"subject\") with (isinstance subject str) / it rewraps and returns a received refusal with (match said (Refusal) (do (<- refused … (rejected (+ said.reason \": \" said.detail))) (return refused))) / (return bad-subject) right after (<- bad-subject WritePlan …) / (:= writes (+ writes #(…))) inside a for. Applied to mainline 0a528aa1: DOEFF121 78, 122 9 (after marking Refusal), 123 112, 124 168, 125 30."
       :evidence "editor-json of doeff-linter wt/hy-smells (agora-controllers wt/hy-smells)")
     (fact
       "The head dicts of defk / deff / defp / defhandler / defeffect accept :tags and keep them as attributes of the definition. A plain defclass has no place for tags and does not appear in browsing that lists definitions by tag."
       :evidence "packages/doeff-hy/src/doeff_hy/_declarations.hy")
     (fact
       "Origin of R8: JsonValue in wire.hy was being used directly. Nobody should use JsonValue directly; instead, like pydantic, actually parse and check.")
     (fact
       "Whether JSON boundary types can also have a def* macro was examined, and of option A (a separate macro defwire) and option B (a :wire clause on defrecord), option A was chosen.")
     (fact
       "Measured: on agora-controllers' mainline, 36 files / 179 lines use JsonValue (a renamed plain dict — dict | list | str | int | float | bool | None). Judgment functions in 7 core files take JsonValue and return plain dict / list, and protocols read JsonValue by decomposing it by hand. Besides doeff_records._wire, there are 4 TypeAlias copies and 3 ad-hoc definitions."
       :evidence "git grep -nwE 'JsonValue|JSONValue' -- controllers services (agora-controllers, 2026-09-28)")]
  :context
    [(interpretation
       "A def* declaration fixes the name, fields, contract, and tags in the macro's shape, so the linter, index, editor, and type checker can read it with one set of reading rules (the HY-005 R5 projection). A plain class can contain anything in its methods, so tools cannot tell whether it is a value type or behavior.")
     (interpretation
       "Methods that touch the outside world, like a client's or store's, do not go through effects, so handler substitution (fakes), record and replay, and layer rules (raw side effects only in the foundation) do not apply, and raw side effects leak outside the foundation. If connections and resources are held by a foundation handler with (session val …) and state by a handler's (session var …), both sit behind effects.")
     (interpretation
       "Value classes (Point2D, Point3D — fields do not change and methods only compute from fields) touch neither the outside world nor changing state, so they have none of the harm above and are allowed. The line is drawn by content, not by name — whether methods contain raw side effects, whether fields hold resources, whether self is rewritten.")
     (interpretation
       "Checks that were written in a dataclass's __post_init__ move to defrecord's :check clause — the checks become readable by tools as part of the type declaration.")
     (interpretation
       "Carrying JSON as JsonValue hides misspelled field names and wrong types until the place of use, and every reading function decomposes it by hand (the same shape knowledge is scattered across every caller). If it is decoded and checked into a declared type once at the boundary, the inside sees only typed values. Decoding is done by pydantic's TypeAdapter (field-name mapping, unknown fields, strict type checks, JSON Schema), so no home-grown decomposition functions are written. Making the declaration a def* lets tools read fields, types, and wire names without executing (R1).")]
  :decision
    [(rule R1 "Declarations that have a name and are read by tools (linter, index, editor, type checker) are written with def*. def* macros live in doeff-hy and ship their reading rules (projection) in the same change (ADR-DOE-HY-005 R4, R5).")
     (rule R2 "Writing field-only types with defrecord is recommended (info). defrecord has :tags ({:context … :role …}) and :check (checks when a value is made) clauses. Checks that were written in a dataclass's __post_init__ move to :check.")
     (rule R3 "Do not create classes that touch the outside world (raw side effects in methods, resources [connection, file, socket] held in fields — clients, stores, things wrapping procedures; e.g. PgStore, Client). Replace them with foundation handlers, and hold resources with (session val …).")
     (rule R4 "Do not create classes with changing state (methods rewrite self — fake stores, caches, entities such as game characters; e.g. FakeRecordStore) (warning). A stateful entity puts its value in a defrecord (immutable), its behavior in pure functions returning new values (defk), and its state in one place, a world handler's (session var …), with changes flowing through effects (e.g. GetCharacter, UpdateCharacter). Reason: rewriting self means changes do not go through effects, so fakes, record and replay, and concurrency do not work. Even when mutation is needed for speed (many entities, numpy arrays, ECS), mutation happens only inside the handler.")
     (rule R5 "Allowed classes: value classes (fields do not change and methods only compute from fields — Point2D, Point3D), exceptions, Enums, Protocols, and subclasses of external library bases. No reason annotation is needed. The judgment is by evidence in the content (raw side effects in methods, resource fields, rewriting self), not by name; the source of truth is doeff-linter's DOEFF119. Until DOEFF119 and defrecord's :tags / :check land, the laws of this file are unwired.")
     (rule R7 "Classification table: pure data (AST, numbers, Point2D) = defrecord (pure methods allowed) / global services (client, store) = foundation handlers and effects / stateful entities (game characters, etc.) = value (defrecord) + pure functions + a world handler's session var.")
     (rule R8 "JSON boundary types are declared with defwire (doeff-hy's record.hy — option A). It is written with the same field shape as defrecord, and always takes a head dict {:names :camel|:snake|:kebab|{field \"wire name\" …} :unknown :reject|:ignore :tags … :check […]} (:names is required — the mapping of wire field names is never decided silently; an explicit dict names every field; :unknown defaults to :reject). The expansion uses defrecord as-is (frozen, keyword arguments only, :tags, :check) and puts on the type __pydantic_config__ and __doeff_wire__ (WireShape — field-name mapping, handling of unknown fields, TypeAdapter). The decoders are in one place, doeff_hy._wire: parse (JSON value, frozen JSON), parse-json (JSON string), dump, dump-json, json-schema; type checks are strict (strings are not made numbers, booleans are not made numbers, arrays go into tuple fields, defenum fields use the value spelling). A JSON answer of the wrong shape is Malformed (which type, which field, why — the 5th failure kind, ADR-DOE-CORE-EFFECTS-003 R17). Until stage 2 of Absent / Raise is on mainline, parse returns Malformed as a value (answer type = (| T Malformed)), and at the stage 3 switch it moves to Raise(Malformed). Only doeff_hy._wire and the foundation modules that do the sending and receiving themselves may touch JSON values (JsonValue — its only definition is doeff_hy._json_value) (doeff-linter DOEFF120 — existing uses are in the using repo's registry). Nested field types are also defwire types. dump / dump-json do not write fields equal to their defaults (including nested-type fields — for `(setv #^ (| str None) x None)`, None means the field is absent, not null. Reading fills absent fields with defaults, so it round-trips. A field without a default is written as null even when None. Addendum 2026-09-28: the nested agent declaration in the screen settings row — the contract expresses optional fields by absence, not null. Rollback = remove :exclude-defaults in wire.hy). Rollback: re-expand defwire into defrecord plus a hand-written TypeAdapter (callers' parse / dump usage stays as is). This clause was added on 2026-09-28.")
     (rule R9 "Smell rules (doeff-linter, deterministic, default severity warning): DOEFF121 = a definition in the judgment layer (setting smells.shape_check_layers) checks input shape with string-keyed (.get x \"field\") and isinstance on that field (fix = parse into a defwire type at the protocol boundary) / DOEFF122 = a match arm receives a failure type and only returns the received value or a rewrap of it (hand-written re-raise — fix = (<- (Raise …)) and on-raise at the caller; early successful exits are not flagged) / DOEFF123 = (return x) right after (<- x T (f …)) with x used nowhere else (fix = (return (! (f …))) or Raise) / DOEFF124 = joining 2 or more fields of the same value into one string with + or an f-string / DOEFF125 = (:= xs (+ xs #(…))) inside for / while (fix = comprehension). Type annotations of JsonValue and dict belong to DOEFF120 and are not duplicated. The severity was to start at info and be raised to warning once stages 1 and 2 of Absent / Raise were on doeff's mainline — they landed on 2026-09-28 (cf6ef9db), so the default was made warning. A repo setting [tool.doeff-linter.rules.<ID>] severity can lower it to info (not to error). Existing hits listed in the registry are info.")
     (rule R10 "Failure types are not hard-coded by name; they are taken from declarations: (a) the types listed in defeffect's :failure / :absent (ADR-DOE-CORE-EFFECTS-003 R5), (b) :failure True in defrecord's head dict (failure values that are not effect answers — e.g. refusals returned by check functions). defrecord keeps the mark in the class attribute __doeff_failure__, and doeff-linter reads it without executing. Types are resolved down to the module through the file's imports and definition sites, so even if a same-named type exists in another module, only the declared one is a failure type. Choice of the mark's shape (2026-09-28, a reversible decision made by the f143ee92 session): defeffect's :failure is a list of types, but defrecord's :failure is a literal True / False — because it is a property of the type itself. To avoid touching the same files as the Absent / Raise work (doeff-hy's macros.hy, handle.hy), the mark was placed only on defrecord in record.hy. Rollback: remove :failure from the keys the head dict accepts, and return DOEFF122 to judging by defeffect declarations only.")
     (rule R11 "Jev rule DOEFF205: for definitions whose role is judgment / program (the definition's :tags or the module's head tags), Jev chooses among mixed / shape checks only / judgment only / neither, as to whether input-shape checks and business judgment are mixed. The yardstick is the description of the layer in the repo's architecture.hy (setting semantic.mixed_concerns.layer — core for agora). Severity at most warning (never error). Calibration positive example = decide-tag, counterexample = a pure judgment without shape checks (card-tags-of). It fires only with --semantic / --semantic-all; runs on every save only read the cache (unjudged does not count as passing).")
     (rule R12 "For service dependencies (doeff-linter DOEFF116), which layers of a dependency may be read is declared per reading layer with :dependency-layers on the layer in architecture.hy (if absent, :open-layers — default intent). This expresses, by declaration and without writing layer names into the linter, the rule that only the assembly entry may also read the dependency service's protocol (translation handlers) (because the entry is where the whole is assembled). agora has (layer entry … :dependency-layers [intent protocol]). Reading a service not in :depends-on is a violation as before, and a layer without :dependency-layers uses only :open-layers as before (2026-09-28, a reversible decision made by the f143ee92 session. Rollback: remove the reading of the layer's :dependency-layers and return DOEFF116 to judging by :open-layers only).")
     (rule R13 "Do not call a defk definition bare and use its answer as a value (doeff-linter DOEFF126, error). Calling a defk bare returns a Program, not the answer, and it flows silently as a wrong value instead of failing with a type error — in one fix change, latest-by-ref, which had been changed to defk, was still being called bare by deffs (text-at, expected-inputs) and 4 check files (found by checks). Hundreds of changes converting defn / deff to defk are ongoing, so watch for callers left behind. Only places that use the answer as a value (comparison, arithmetic, built-in functions, methods, attributes, conditions, iteration sources, record fields) are flagged; passing to a call that accepts a Program, binding to a name, and returning as a function's answer are not flagged (the first version's 'everything outside Program positions' had 239 hits on agora's mainline, mostly misses). Calls that cannot be traced (functions received as arguments, methods) are not flagged either. Decided by the coordinator on 2026-09-28 (reversible — Rollback: remove DOEFF126 from enable. To widen what is flagged, extend VALUE_HEADS and the position table in bare_calls.rs).")
     (rule R14 "Close two gaps in DOEFF126 (coordinator's request 2026-09-28): (1) passing as a method argument — places such as (.append out (breach …)) that pass a defk's answer as a method argument also count as using the answer as a value. (2) calling a function received as an argument bare — rows-by-text, fixed in agora's L1550, called its argument field-of bare as (setv value (field-of row)), and callers passed fnk (which calls payload-of) there. The value was always a Program, isinstance str was always false, and the index was always empty. Coverage: collect repo calls that pass a repo defk name or (fnk …) as an argument, look up that argument in the callee's top-level definition's parameter list, and if the body calls that parameter outside places that pass it as a Program, flag that call in the callee (binding to a name is also a violation). Not covered: callers that put a defk into a variable or field before passing it, or wrap it with partial etc.; callees that pass the parameter on to another function that calls it bare (only one level is traced); callees that are methods, nested functions, or not resolvable by name; callers outside the repo. These belong to types (narrowing :pre's Callable to a function type returning Program) or effect inference (ADR-DOE-CORE-EFFECTS-003 R13) (2026-09-28, the f143ee92 session, reversible — Rollback: remove param_calls.rs and return method arguments to targets only). (Addendum 2026-09-28, coordinator's request) Two more shapes are flagged: (3) assert arguments — the message and condition of (assert cond (describe …)) are also places that use the answer (with a defk as the condition the Program is always truthy and the check always passes). (4) binding a bare call to a name and then using it as an answer — in agora's incident_placement.hy (before the fix, 9e2574a6^), (get all job) after (setv … all (histories run)). If, within one definition, a name bound to a bare defk call by setv, val, var, :=, or let appears in a place that uses the answer (comparison, built-in functions, method targets and arguments, method calls on the name all.get, attributes, conditions, iteration sources), the bound call is flagged. Passing that name as a Program (to run-on etc.) or returning it is not flagged. A definition that also binds the same name with (<- name …) does not track that name. Added to not covered: passing the bound name to another definition and using it as an answer there; putting it into a field or container (dict, list) and taking it out to use. Applied to the pre-fix shape, it was confirmed that (.items (histories run)) was already flagged, and that the use after (setv … all (histories run)) is flagged with this addendum.")
     (rule R6 "Revision record (2026-09-27): the first version's 'do not create classes with behavior; an escape hatch with a reason annotation only where an external library requires a class' was replaced by R3–R5 through two narrowings of the target. Rollback: to widen the target again, revert this revision's commit (back to the first version's R2–R5 and laws).")]
  :laws
    [(law world-touching-classes-become-foundation-handlers
       :statement "for_all class c (excluding Hy, exceptions, Enums, Protocols, and subclasses of external bases): c's methods have no raw side effects, and c's fields hold no resources (connection, file, socket) — things that touch the outside world are foundation handlers, and resources are session val"
       :counterexamples
         [(counterexample "(defclass PgStore [] (defn put [self row] (with [c (psycopg.connect self.dsn)] …))) — writes do not go through effects, so neither substitution with a fake nor record and replay is possible")
          (counterexample "(defclass BudgetClient [] (defn spend [self n] (requests.post self.url …))) — a client becomes an effect and a foundation handler")]
       :enforced-by ["doeff-linter DOEFF119"]
       :wiring "Unwired (2026-09-27) — DOEFF119 has not landed in doeff-linter")
     (law state-changing-classes-become-session-var
       :statement "for_all class c (Hy, same exclusions as above): c's methods do not rewrite self — changing state is a handler's session var"
       :counterexamples
         [(counterexample "(defclass FakeRecordStore [] (defn put [self row] (.append self.rows row))) — the fake store's state lives outside the handler and is not swapped by handler substitution")
          (counterexample "A cache class remembers with (setv (get self.entries key) v) — state is held with (session var …)")
          (counterexample "(defclass Character [] (defn take-damage [self n] (-= self.hp n))) — character changes do not go through effects, so record and replay and concurrency do not work. The value goes in defrecord, take-damage is a function returning a new value, state goes in a world handler's session var, and it flows through UpdateCharacter")]
       :enforced-by ["doeff-linter DOEFF119"]
       :wiring "Unwired (2026-09-27) — DOEFF119 has not landed in doeff-linter (this law is a warning)")
     (law value-classes-are-allowed
       :statement "for_all class c: c's fields do not change and c's methods only compute from fields ⇒ c is not a DOEFF119 violation (Point2D, Point3D)"
       :counterexamples
         [(counterexample "Flagging Point2D just because it has a norm method — judging by name or by the presence of methods rejects even harmless value classes")]
       :enforced-by ["doeff-linter DOEFF119"]
       :wiring "Unwired (2026-09-27) — DOEFF119 has not landed in doeff-linter")
     (law json-is-parsed-into-declared-types
       :statement "for_all module m (excluding doeff_hy._wire and the foundation modules named in architecture.hy's :wire-modules): m does not use JsonValue / JSONValue / JsonObject / JSONObject ∧ for_all JSON value v and defwire type T: parse(T, v) is a value of T or Malformed (which type, which field, why), and if a value of T then parse(T, dump(parse(T, v))) = parse(T, v)"
       :counterexamples
         [(counterexample "(defk state-of [row] {:pre [(: row JsonValue)] …} (.get row \"state\")) — reads JSON by decomposing it by hand. Misspellings and wrong types are invisible until the place of use")
          (counterexample "A core judgment function takes (: value JsonValue) and returns a plain dict — carries it inside without decoding into a typed value")
          (counterexample "Silently putting {\"landedAt\": \"3\"} into an int field as 3 — swallows the type mismatch instead of making it a business failure (Malformed)")]
       :enforced-by ["doeff-linter DOEFF120" "packages/doeff-hy/tests/defwire_deftests.hy"]
       :wiring "Partly wired (2026-09-28) — defwire_deftests.hy checks the parse/dump round trip and Malformed on type mismatch. DOEFF120 is a separate change in doeff-linter")
     (law defk-answers-are-not-used-bare
       :statement "for_all repo defk f and call (f …): the call's answer is not in a place that uses it as a value (comparison, arithmetic, built-in functions, methods, attributes, conditions, iteration sources, record fields) — the answer is received with (<- x (f …)) or (! (f …))"
       :counterexamples
         [(counterexample "(defk text-at [ref] (.get (latest-by-ref ref) \"text\")) — latest-by-ref is a defk. It reads .get of a Program and is silently wrong while believing it is the answer")]
       :enforced-by ["doeff-linter DOEFF126"]
       :wiring "Wired (2026-09-28) — doeff-linter DOEFF126 (wt/hy-defk-bare-call)")
     (law judgment-does-not-check-json-shape
       :statement "for_all definition d ∈ judgment layer: d contains no isinstance on a field read by string key (.get x \"field\") — input shape is decoded into typed values at the communication boundary"
       :counterexamples
         [(counterexample "(val subject (.get payload \"subject\")) (when (not (isinstance subject str)) …) — a core judgment checks the JSON shape")]
       :enforced-by ["doeff-linter DOEFF121"]
       :wiring "Wired (2026-09-28) — doeff-linter DOEFF121 (wt/hy-smells)")
     (law failures-are-raised-not-returned
       :statement "for_all match arm a: a's type is a declared failure type ⇒ a's body does not merely return the received value (or a value made from it) — failures are emitted with Raise and received with on-raise"
       :counterexamples
         [(counterexample "(match said (Refusal) (do (<- refused Plan (rejected (+ said.reason \": \" said.detail))) (return refused)) (TagsAccepted) None) — hand-written re-raise")]
       :enforced-by ["doeff-linter DOEFF122"]
       :wiring "Wired (2026-09-28) — doeff-linter DOEFF122 (wt/hy-smells). Failure types come from declarations (defrecord :failure True, defeffect :failure / :absent)")
     (law binds-are-not-returned-straight-away
       :statement "for_all (return x) right after (<- x T (f …)): x is used elsewhere in the definition"
       :counterexamples
         [(counterexample "(<- bad-subject WritePlan (rejected …)) (return bad-subject) — the name exists only to be returned")]
       :enforced-by ["doeff-linter DOEFF123"]
       :wiring "Wired (2026-09-28) — doeff-linter DOEFF123 (wt/hy-smells)")
     (law typed-values-are-not-flattened-into-text
       :statement "for_all + or f-string e: e does not join 2 or more fields of the same value together with strings"
       :counterexamples
         [(counterexample "(+ said.reason \": \" said.detail) — flattens a typed refusal into one string")]
       :enforced-by ["doeff-linter DOEFF124"]
       :wiring "Wired (2026-09-28) — doeff-linter DOEFF124 (wt/hy-smells)")
     (law accumulators-are-not-rebuilt-in-loops
       :statement "for_all for / while body: no (:= xs (+ xs #(…))) / (setv xs (+ xs […])) — accumulations are built at once with a comprehension"
       :counterexamples
         [(counterexample "(for [word adding] (:= writes (+ writes #((AttachTag …))))) — an accumulation rebuilt every iteration")]
       :enforced-by ["doeff-linter DOEFF125"]
       :wiring "Wired (2026-09-28) — doeff-linter DOEFF125 (wt/hy-smells)")
     (law judgments-do-not-mix-shape-checks
       :statement "for_all definition d (role judgment / program): d does not mix input-shape checks with business judgment (Jev's judgment, at most warning)"
       :counterexamples
         [(counterexample "decide-tag — shape checks (subject's type, the shape of add / remove) and the judgment of attaching / removing tags sit side by side in one defk")]
       :enforced-by ["doeff-linter DOEFF205"]
       :wiring "Wired (2026-09-28) — doeff-linter DOEFF205 (wt/hy-smells, reads Jev's cache)")]
  :enforcement
    [(deftest test-adr-doe-hy-007-values-are-made-by-calling-a-defrecord-type
       ;; Demonstration: a data type is built in one defrecord line, and values are made by calling that type (frozen — rewriting is refused).
       (val row (ProbeBudgetRow :name "lab" :used 3 :limit 10))
       (assert (= #(row.name row.used row.limit) #("lab" 3 10)))
       (assert (= row (ProbeBudgetRow :name "lab" :used 3 :limit 10)))
       (var refused False)
       (try
         (setv row.used 4)  ; try rewriting the value's contents (a check that it is refused)
         (except [FrozenInstanceError]
           (:= refused True)))
       (assert refused "defrecord values are frozen (rewriting is refused)"))
     (deftest test-adr-doe-hy-007-json-is-parsed-into-a-defwire-type
       ;; Demonstration (R8): JSON is decoded and checked into a defwire type — fields are read by wire name, type mismatches are Malformed, and dump returns the same JSON.
       (<- row (parse ProbeBudgetWire {"name" "lab" "usedUnits" 3}))
       (assert (= row (ProbeBudgetWire :name "lab" :used-units 3)))
       (<- raw (dump row))
       (assert (= raw {"name" "lab" "usedUnits" 3}))
       (<- wrong (parse ProbeBudgetWire {"name" "lab" "usedUnits" "3"}))
       (assert (isinstance wrong Malformed))
       (assert (= (lfor f wrong.fields f.field) ["usedUnits"])))]
  :plans [])
