;;; 陽性対照(D11 doeff-agents-seat-home-instruction-names-have-one-home)。
;;; 当たるのは 6・7・8 行ちょうど。9〜11 行は**改行またぎ**の陰性対照で、
;;; 3 本目が [^"] のままなら散文の中の引用符 1 つと 11 行目の綴りが 1 致になる
;;; (実測: 旧形は 10 → 11 行を 1 致として返す)。
(setv label "seat-memory")
(setv HOME-NAME "CLAUDE.md")
(setv DIR-NAME "skills")
(setv SEAT-MEMORY "/home/kento/.claude/CLAUDE.md")
;; ⚠ 陰性対照(改行またぎ): この行に開き引用符が 1 つだけ在る "
;; 次の行に家の中の名が在ると、[^"] は改行を越えるので 1 致になる。
;; <家>/skills"
(setv ok (carried-home-name source))
