;;; Layer declarations for the files under tools/docs-video.
;;;
;;; For now this holds only two layers that are excluded from the rule population (narration and recording), in the same form as
;;; tools/architecture.hy and scripts/architecture.hy. doeff-linter decides the population of per-statement Python rules from the nearest
;;; architecture.hy found walking up from the file (#2811). The files here have no module name of their own (no __init__.py, and a bare stem
;;; such as tts would name other modules too), so the layers name them by path in :files. Only the named files are exempted; the population
;;; for the other files in this dir is unchanged (DOEFF004 still applies as before).
;;;
;;; narration = tts.py, which makes a page's narration with OpenAI's speech endpoint (a PEP 723 script, with empty dependencies = stdlib only).
;;;   It is run with `uv run tts.py` and works without the repository's Python environment -- it runs outside a doeff Program and cannot
;;;   import doeff's handlers (the subprocess_handler that answers ReadEnvironment). The key (OPENAI_API_KEY) and the relay of the endpoint
;;;   (OPENAI_BASE_URL) are given as environment variables; the entry point main reads the environment in one place and passes the values on.
;;;   - Exempted rule: DOEFF004 -- limited to the named file (tts.py).
;;;   - Forbidden imports: doeff modules. If the stdlib-only promise breaks, DOEFF032 turns red.
;;; recording = record/terminal_window.py, which records the terminal-and-window gif. It runs pinned to the published doeff
;;;   (`uv run --no-project --with doeff==0.8.0`), whose names differ from this tree's (ReadEnvironment lives in
;;;   doeff_core_effects.process_effects there), and it starts Xvfb, xterm and xdotool with this process's environment plus DISPLAY and
;;;   PATH. The entry point (`__main__`) reads the environment in one place and passes the mapping to record. It imports doeff to play
;;;   the fight, so no import is forbidden.
;;;   - Exempted rule: DOEFF004 -- limited to the named file (record/terminal_window.py).
;;; How to revert: delete the :exempt line and DOEFF004 applies to that file's read again. If this file is deleted, the population for
;;;   the files in this dir returns to tools/architecture.hy.
(defarchitecture doeff-docs-video
  :root "."
  :layers [(layer narration
             :summary "Stdlib-only script that makes a page's narration with the speech endpoint -- called with uv run, code outside a Program"
             :knows "the speech endpoint, narration.json, wav files"
             :does-not-know "doeff Programs, effects, handlers"
             :files ["tts.py"]
             :exempt [(rule DOEFF004 "A PEP 723 stdlib-only script runs outside a doeff Program and cannot import doeff's handlers; the API key and the endpoint relay are environment variables, and main reads the environment in one place")]
             :forbid-modules [doeff doeff_core_effects doeff_hy doeff_vm doeff_agents doeff_cluster])
           (layer recording
             :summary "Script that records the terminal-and-window gif -- runs pinned to doeff==0.8.0, code outside a Program"
             :knows "Xvfb, xterm, xdotool, fonts, the effects tour's spire.py"
             :does-not-know "which business uses which setting"
             :files ["record/terminal_window.py"]
             :exempt [(rule DOEFF004 "It runs pinned to the published doeff 0.8.0, whose ReadEnvironment is under another module name than this tree's, and starts its X children with this process's environment; the entry point reads the environment in one place and passes it to record")])])
