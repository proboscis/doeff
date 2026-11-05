;;; Hy side of the positive control (D10) -- the same rule fires even though the spelling tools differ from Python.
;;; Fires on exactly lines 3-6.
(setv MEMORY "dotfiles/claude/CLAUDE.md")
(setv ABSOLUTE "/home/user/dotfiles/claude/CLAUDE.md")
(setv JOINED (os.path.join home "dotfiles" "claude" "CLAUDE.md"))
(setv INTERPOLATED f"{home}/dotfiles/agent/skills")
;; Negative control (prose): the note that the location inside dotfiles differs per host is not a spelling
(setv NOT-A-SEGMENT "my-dotfiles/claude")
