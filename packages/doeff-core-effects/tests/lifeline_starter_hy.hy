;;; Run lifeline_starter.py from a Hy entry point. The Hy entry point rewrites its own process's sys.executable to the hy launcher
;;; (hy/cmdline.py), so, as with the doeff-cluster worker (boot.sh's `exec hy -m ...`), sys.executable is not python
;;; and becomes the spawning side. Usage: hy lifeline_starter_hy.hy <mode> <out-path> - (the child only sleeps -- the form that spawns a grandchild uses sys.executable, so
;;; it is not used here)
(require doeff-hy.macros [val])
(import os runpy sys)

(val STARTER (os.path.join (os.path.dirname (os.path.abspath __file__)) "lifeline_starter.py"))
(setv sys.argv [STARTER #* (cut sys.argv 1 None)])
(runpy.run-path STARTER :run-name "__main__")
