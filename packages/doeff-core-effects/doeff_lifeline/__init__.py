"""When the process it started ends, its children end too -- the home of the body of the StartProcess promise. Written with the standard library only.

This package has no public names (ADR-DOE-API-001 R1 and R5 -- the names are neither effects nor handlers, so they are kept inside).
The body is in the inner module _watch, and only 2 things inside doeff read it: the StartProcess responder (os_process.hy of doeff_core_effects -- Lifeline) and
the shim of doeff-cluster's worker (watched_until_eof and the type of the party to stop).

It sits in the doeff-core-effects distribution, as a top-level package separate from doeff_core_effects: importing the doeff_core_effects package adds about 100 ms to startup
(measured 2026-10-07), so it is separated so that the shim started per job can read only this body.
"""

if __name__ == "__main__":
    # The Lifeline of the previous version starts the watcher with this file (`python -I -S -B <this file>`). So that watchers of processes running across versions also start,
    # pass it to the body (with -I a package cannot be imported, so run it as a file).
    import pathlib
    import runpy

    runpy.run_path(str(pathlib.Path(__file__).with_name("_watch.py")), run_name="__main__")
