;;; Layer declarations for doeff-hy.
;;;
;;; For now this holds a single layer that is removed from the rule population. doeff-linter decides the population of per-statement
;;; Python rules from the nearest architecture.hy above a file (this file) -- the doeff root run (pre-commit, make lint-doeff) also reads this declaration's exemption.
;;;
;;; startup = the Hy bytecode guard (doeff_hy_bytecode_guard) that enters from `.pth` (src/doeff_hy_bytecode_guard.pth) at Python startup.
;;;   When the venv's Python starts it runs before any Program or handler, so there is no entry point to receive settings via dependency injection
;;;   (doeff's Ask). The directory of the shared code store and the flag that turns the store off are read from environment variables (DOEFF_HY_CODE_STORE, XDG_CACHE_HOME)
;;;   (turning the store off with DOEFF_HY_CODE_STORE=off is needed as the stopgap of #2799).
;;;   Reviewed in #3012 and kept exempt: wrapping doeff handlers here would make every Python startup of the venv load doeff (the same kind as the build entry PEP 517).
;;;   - Rule removed: DOEFF004 (reading os.environ directly) -- limited to the named module (doeff_hy_bytecode_guard and below).
;;;   - Forbidden imports: doeff business modules. If business code enters the exempted layer, DOEFF032 turns red (do not create a shape where the removed rule
;;;     also stops applying to business code).
;;; The layer environment, which had exempted the module that reads the places the tool uses from environment variables (doeff_hy._env_places), is gone -- the environment variables are now asked
;;;   with the ReadEnvironment effect under the real handler subprocess_handler, so DOEFF004 does not apply.
;;; How to revert: delete the :exempt line and DOEFF004 applies to the guard again as before.
;;;
;;; Invariants (checking the .pyc record of the startup layer -- doeff_hy_bytecode_guard._macro_use. It is outside a Program, so it cannot be put in defservice's :invariants,
;;; and the clauses are written here in one place):
;;;   B1 stale-expansion-is-never-current -- if any of the things the record's digest depended on (the contents of the file that defines the macro, the contents of the files of the value modules the macro follows,
;;;     values decided outside files such as environment variables) differs from when the record was made, the check judges it stale and has it expanded again. Neither the digest nor
;;;     the check's answer (matches / stale) is remembered across processes (that would trust a live value only by the files traced, the files of the packages the record names, and the environment -- a plain value does not know which module it came from).
;;;     What may be remembered across processes is only pure reads decided by contents: the reading of source statements that cover globals that cannot be traced
;;;     (bound_source -- key = source contents, names, the reader's code and version) and the reading of the code of functions the walk reads (macro_use's CodeFacts --
;;;     key = sha256 of the marshaled code contents, the reader's code, and the Python and Hy versions). Whether a class is traced by contents or covered only by its public shape is judged
;;;     by the suffix of the standard extension modules (not by a copy of source extensions -- an unknown suffix is traced by contents).
;;;     The code contents of functions cover only the functions executed during that module's expansion (picked up by called_code via sys.monitoring) and the functions behind binding statements (the code that
;;;     made the value), and the record carries marks of the covered functions -- the code of functions that were not executed does not change the expansion result (child of #3769). Function
;;;     names, names of class members, values, and binding statements all still go in as before. When covering code that reads a function's contents as a value (docstrings, argument shapes), and
;;;     in environments where picking up is impossible, everything is covered. Failure cases = tests/test_macro_use_called.py (changing a method that is not called still matches, a called method is stale,
;;;     a function that builds a table at import time, a macro that reads contents) and the := presence check in test_macro_use_record.py (the real defk and binding_forms).
;;;     Environment variables are process state that neither versions nor files cover: a digest whose walk hit a read of environment variables (environ, environb, getenv, getenvb of os, posix, nt
;;;     -- code looked up by name, binding statements, imports inside functions, objects hit as values) cannot be checked -- it is not remembered, the record's row carries the mark
;;;     records.ENVIRONMENT_READ, and the check always judges it stale (globals of plain values built from environment variables go in by value, so they do not hit). When covering a
;;;     value that cannot be traced by the owner module's file (default values, closure cells, globals whose binding cannot be traced), it gets the mark if the owner's source may read environment variables
;;;     -- a file does not copy the environment variables read at load time. The reading of bindings (bound_source) also resolves Hy imports through to their targets.
;;;     Reads not seen: environment variables read inside standard library functions, reads by string lookup, reads through the module table, star imports (macro_use's docstring).
;;;     The judgment of the mark belongs only to macro_use (the walk that makes and the check that verifies) and the definition point records; the loading entry does not distinguish marks.
;;;     Failure cases = clauses 1 to 3 of "memory across processes" in tests/test_macro_use_record.py and the check of values of another top-level package (red for a shape that remembers a digest by
;;;     file state and a shape that remembers the check's answer by package files and environment), the check of a class from a source with an extension not in the copy,
;;;     the checks of code-reading keys (a helper made by a macro in another file,
;;;     the Hy version in the key, the reading function living in a module the key covers),
;;;     the checks per macro shape that reads a value decided by environment variables (appendix 3 of the same design review's blind A -- and the
;;;     Hy imports, default values, and closure cell shapes of that design review's blind A), and the checks of blind B (a user that reads environment variables whose macro was changed in a different worktree is
;;;     compiled again even via the store path, and the mark is not read outside records.py and macro_use.py).

(defarchitecture doeff-hy
  :root "doeff_hy"
  :layers [(layer startup
             :summary "Hy bytecode guard that enters at Python startup (.pth) -- outside Program"
             :knows "Python import machinery, Hy bytecode, the directory of the shared code store"
             :does-not-know "doeff Program, effect, handler"
             :modules [doeff_hy_bytecode_guard]
             :exempt [(rule DOEFF004 "Enters from .pth when the venv's Python starts and runs before any Program or handler, so there is no entry point to receive settings via Ask (the store directory and the flag that turns the store off are read from environment variables)")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm])])
