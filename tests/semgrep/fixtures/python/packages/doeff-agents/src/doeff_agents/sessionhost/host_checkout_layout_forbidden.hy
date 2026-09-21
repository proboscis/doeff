;;; 陽性対照(D10)の Hy 側 — 綴りの道具が Python と違っても同じ規則が当たる。
;;; 当たるのは 3〜6 行ちょうど。
(setv MEMORY "dotfiles/claude/CLAUDE.md")
(setv ABSOLUTE "/home/kento/dotfiles/claude/CLAUDE.md")
(setv JOINED (os.path.join home "dotfiles" "claude" "CLAUDE.md"))
(setv INTERPOLATED f"{home}/dotfiles/agent/skills")
;; 陰性対照(散文): dotfiles の中の置き場は宿ごとに違う、という註は綴りではない
(setv NOT-A-SEGMENT "my-dotfiles/claude")
