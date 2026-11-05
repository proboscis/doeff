;;; Positive control (D11 doeff-agents-seat-home-instruction-names-have-one-home).
;;; Exactly lines 6, 7, 8 match. Lines 9-11 are a **newline-spanning** negative control:
;;; if the third pattern stayed [^"], one quote in the prose and the spelling on line 11 would form a single match
;;; (measured: the old form returned 10 -> 11 as one match).
(setv label "seat-memory")
(setv HOME-NAME "CLAUDE.md")
(setv DIR-NAME "skills")
(setv SEAT-MEMORY "/home/user/.claude/CLAUDE.md")
;; WARNING negative control (newline-spanning): this line has exactly one opening quote "
;; If a name inside the home is on the next line, [^"] crosses the newline, so it becomes one match.
;; <home>/skills"
(setv ok (carried-home-name source))
