;;; semgrep hit fixture: doeff-agents-turn-carried-key-must-not-become-a-launch-flag
;;; (a charter re-declares the field per turn;
;;;  resolving it by "copying it into the line" is a forbidden form. It makes two sources of truth).

;; BAD: put the turn's payload into the flag set (= it remains in the line's launch_overlay)
(setv LAUNCH-FLAG-KEYS #("auto_compact_window" "memory_dir"))

;; BAD: push the volume directly onto the line's overlay
(setv launch-overlay {"session_env" {} "memory_files" books})

;; BAD: the same shape even with the spelling order reversed
(setv row (replace row :launch-overlay {"memory_dir" home}))
