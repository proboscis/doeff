;;; Executable ADR: a handler without settings is not made a factory. A factory taking no arguments at all is allowed only when each call creates new state.
;;; await_handler is a plain handler value, not await_handler(), and is written [await_handler (lazy-ask :strict True) try-handler (state) writer].
;;;
;;; Enforcement is in one place: tools/handler_factory_census.py picks up the factories without arguments from shipped code (doeff/, packages/, examples/ — excluding tests)
;;; and matches them against the known table tests/data/zero_parameter_handler_factories.json (R4). The deftests in this file call that
;;; matching, and confirm that the tool picks up the old await_handler shape and that await_handler works as a plain handler.
;;;
;;; Rollback: to turn await_handler back into a factory, change await_handler in _handlers.py back to def await_handler() -> ProgramHandler,
;;; remove _former_factory_handler and FormerFactoryHandler from doeff.program, change the callers back to await_handler(), and add an
;;; await_handler row with the verdict "to fix" to the known table. Remove it only on the maintainer's word.

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [defk deftest val <-])
(import importlib.util)
(import sys)
(import pathlib [Path])
(import types [ModuleType])
(import pytest)
(import asyncio)
(import doeff [Await run])
(import doeff_core_effects [await-handler scheduled try-handler])


(val ROOT (get (. (.resolve (Path __file__)) parents) 2))
(val CENSUS "tools/handler_factory_census.py")
(val OLD-AWAIT-HANDLER
  "from doeff.program import handler as _program_handler\n\ndef await_handler() -> ProgramHandler:\n    @do\n    def handler(effect, k):\n        yield Pass(effect, k)\n\n    return _program_handler(handler)\n")
(val NEW-AWAIT-HANDLER
  "@do\ndef _await_handler(effect, k):\n    yield Pass(effect, k)\n\n\nawait_handler: FormerFactoryHandler = _former_factory_handler(_await_handler, \"await_handler\")\n")


(defk census-module []
  {:pre [] :post [(: % ModuleType)] :tags {:context "doeff-api-adr" :role "foundation"}}
  "Load the tool that lists factories without arguments (tools/ is not a package — load it under another name)."
  (val spec (importlib.util.spec-from-file-location "_adr_doe_api_002_census" (/ ROOT CENSUS)))
  (val module (importlib.util.module-from-spec spec))
  ;; dataclass reads its defining module from sys.modules, so register it under the name before loading.
  (setv (get sys.modules "_adr_doe_api_002_census") module)
  (.exec-module spec.loader module)
  module)


(defk awaits-two []
  {:pre [] :post [(: % int)] :tags {:context "doeff-api-adr" :role "program"}}
  "Sample program that await_handler answers: Await a coroutine that returns 2."
  (<- answer int (Await (asyncio.sleep 0 2)))
  answer)


(defadr ADR-DOE-API-002
  :title "A handler without settings is not made a factory. A factory taking no arguments at all is only for when each call creates new state; otherwise it is a handler value. await_handler is a plain handler, and the old await_handler() returns the same handler with a DeprecationWarning for one release. Enforcement is in one place: matching the list of factories without arguments against the known table"
  :status "accepted"
  :scope ["packages/doeff-core-effects/doeff_core_effects/_handlers.py (await_handler)"
          "doeff/program.py (FormerFactoryHandler, _former_factory_handler, former_factory in _install_raw)"
          "tools/handler_factory_census.py, tests/data/zero_parameter_handler_factories.json"
          "functions returning a handler in doeff/, packages/, examples/ (excluding tests)"]
  :problem
    [(fact
       "On 2026-10-29 it was decided to fix the shape where await_handler goes through a call await_handler() of a function taking no arguments, and not to make handlers without settings into factories.")
     (fact
       "On main as of 2026-10-29, await_handler was def await_handler() -> ProgramHandler, which took no arguments and built and returned an @do def handler(effect, k) inside. The only reasons for making it a factory were the deferred import of asyncio and the scheduler effects, and enclosing _get_await_bridge_loop() inside; it held no per-call state (the bridge loop is one per process). The README example was [await_handler(), lazy_ask(strict=True), try_handler, state(), writer], with called and uncalled items side by side in the same list."
       :evidence "packages/doeff-core-effects/doeff_core_effects/_handlers.py (doeff c83155f55de0)")
     (fact
       "In the same tree, shipped code had 15 functions that take no arguments at all and return a handler. 6 create new state on each call (a storage, a table of waiters, a table of progress bars), and 9 (await_handler, sequential, maybe, result, 3 production_handlers, and conductor's agent_handler and git_handler) hold neither settings nor per-call state."
       :evidence "the listing of tools/handler_factory_census.py and the reasons on the rows of tests/data/zero_parameter_handler_factories.json")]
  :context
    [(interpretation
       "A factory is where settings (state's initial value, lazy_ask's env) or separately held per-call state are received. The () of a factory with neither adds nothing and makes the writer remember \"whether this handler is called\". Making it a value like try_handler and writer lets the list line up in the same shape: [await_handler try_handler writer].")
     (interpretation
       "A factory that creates new state on each call (in_memory_cache_handler() — a separate cache per call) stays a factory, because as a value every program in the process would share one state. Even without arguments, it is a maker of per-call state, not of settings.")]
  :decision
    [(rule R1 "A handler with neither settings (arguments) nor per-call state is not made a factory; it is exposed as a handler value (a marked ProgramHandler). It is applied to a program as h(p) and placed in a list as h.")
     (rule R2 "A factory taking no arguments at all is allowed only when each call creates new state (a storage, a table of waiters, etc.) and encloses it in the handler. A factory whose arguments all have defaults (state(initial=None)) receives settings, so it is outside R1.")
     (rule R3 "When turning a factory into a value, keep the old call (the call without arguments) for one release in a shape that emits a DeprecationWarning and returns the same handler (doeff.program's _former_factory_handler — its type is FormerFactoryHandler, and type checking also reports the old call as deprecated). Once the callers inside doeff and in repos that read doeff's main have been rewritten to the new shape, remove the old call. examples/testing_without_mocks and publications/zenn-use-cases-v0 in the doeff-publications repo, which run daily against the PyPI release, are rewritten when a release with the value shape is on PyPI (same reason as ADR-DOE-CORE-003 R4). The print output and pytest check of examples/testing_without_mocks are held only by item 8 of tools/verify_core_install.py --pypi, which runs against the PyPI release, and are not run against the repo version. When the value-shape release is on PyPI and they are rewritten, decide whether to move back to checking the repo version.")
     (rule R4 "Enforcement is in one place: tools/handler_factory_census.py picks up factories without arguments from shipped code (git-tracked Python and Hy in doeff/, packages/, examples/ — excluding tests dirs and examples/testing_without_mocks) and matches them against the known table tests/data/zero_parameter_handler_factories.json. Each table row has a verdict (per-call state, to fix, not a factory) and a reason. A factory not in the table is red, and a row in the table that is not picked up is also red (the table only shrinks along with the code). A \"to fix\" row writes the card that fixes it in its reason.")
     (rule R5 "await_handler was changed to the R1 shape (2026-10-29). The imports of asyncio and the scheduler effects inside it are loaded when it first answers an Await (importing doeff does not load asyncio; the scheduler module is loaded after this module). The meaning of Await and the behavior of the bridge loop are unchanged.")]
  :laws
    [(law a-handler-without-settings-is-a-value
       :statement "for_all f in shipped_code with params(f) = {} and returns_handler(f): row(f) in known_table and (verdict(row(f)) = per_call_state => new_state_per_call(f)); for_all row in known_table: row.key in census(shipped_code)"
       :counterexamples
         [(counterexample "def await_handler() -> ProgramHandler only builds and returns a handler inside, and await_handler() and try_handler sit side by side in a list")
          (counterexample "a new package adds def xxx_handler() without arguments and passes without being written in the known table")
          (counterexample "a factory is turned into a value but its row stays in the known table, and the table does not shrink")
          (counterexample "the old call xxx() of a handler turned into a value silently returns the same thing without a warning, and callers remain unrewritten")]
       :enforced-by ["docs/adr/defadr_doeff_api_002_handler_without_settings_is_a_value.hy::test-adr-doe-api-002-every-factory-without-parameters-is-in-the-known-table"
                     "docs/adr/defadr_doeff_api_002_handler_without_settings_is_a_value.hy::test-adr-doe-api-002-the-census-finds-the-former-await-handler"
                     "docs/adr/defadr_doeff_api_002_handler_without_settings_is_a_value.hy::test-adr-doe-api-002-await-handler-is-a-handler-value"
                     "tests/test_await_handler_is_a_handler_value.py"]
       :wiring "wired (2026-10-29) — the listing tool and the known table, the 3 deftests in this file, and 1 file under tests")]
  :enforcement
    [(deftest test-adr-doe-api-002-every-factory-without-parameters-is-in-the-known-table
       ;; R4: the factories without arguments in shipped code match the known table (no factory missing from the table, no row that is not picked up).
       (<- census ModuleType (census-module))
       (assert (= (census.differences) []) (census.differences)))
     (deftest test-adr-doe-api-002-the-census-finds-the-former-await-handler
       ;; R4, R5: the await_handler shape before 2026-10-29 is picked up as a factory, and the value shape is not. Hy's (defn #^ type name []) is picked up too.
       (<- census ModuleType (census-module))
       (assert (= (lfor f (census._python-factories "x.py" OLD-AWAIT-HANDLER) f.name) ["await_handler"]))
       (assert (= (census._python-factories "x.py" NEW-AWAIT-HANDLER) []))
       (assert (= (lfor f (census._hy-factories "x.hy" "(defn #^ ProgramHandler clock-handler []\n  (handler answer))\n") f.name)
                  ["clock-handler"])))
     (deftest test-adr-doe-api-002-await-handler-is-a-handler-value
       ;; R1, R3, R5: await_handler is a marked value; applied to a program it answers Await. The old await_handler() emits a warning and returns the same value.
       (assert (is (getattr await-handler "_doeff_is_handler_fn" False) True))
       (assert (= (run (scheduled (await-handler (awaits-two)))) 2))
       (with [(pytest.warns DeprecationWarning :match "await_handler has no settings")]
         (val called (await-handler)))
       (assert (is called await-handler)))]
  :plans ["packages/doeff-core-effects/doeff_core_effects/_handlers.py (await_handler as a value)"
          "doeff/program.py (FormerFactoryHandler, _former_factory_handler)"
          "tools/handler_factory_census.py, tests/data/zero_parameter_handler_factories.json"
          "tests/test_await_handler_is_a_handler_value.py"
          "callers of await_handler() and (await-handler) inside doeff (excluding historical records and dirs that run against the PyPI release)"
          "the changes that converted the \"to fix\" rows of the listing to the R1 shape (sequential, the 3 production_handlers and the 2 of conductor, maybe, result)"])
