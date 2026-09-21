;;; semgrep hit fixture: doeff-agents-turn-carried-key-must-not-become-a-launch-flag
;;; (card acp:kanban-issue:ki-a40292ed30d9 / 法 ACP 575b1e — 手番ごとに charter が
;;;  名乗り直す欄を「行へ写して」解くのは禁止形。正本が 2 つになる)。

;; BAD: 手番の荷を旗の集合へ入れる(= 行の launch_overlay に残る)
(setv LAUNCH-FLAG-KEYS #("auto_compact_window" "memory_dir"))

;; BAD: 行の overlay へ冊を直接積む
(setv launch-overlay {"session_env" {} "memory_files" books})

;; BAD: 綴りの順が逆でも同じ形
(setv row (replace row :launch-overlay {"memory_dir" home}))
