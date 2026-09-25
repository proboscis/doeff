;;; 盲検 B の違反の実測: M2 の出力が herdr の答えではなく policy の語彙で変わる。
(import doeff [run])
(import doeff_agents.sessionhost.substrate_herdr [herdr-foreground-command])
(import doeff_agents.sessionhost.policy [IDLE-SHELL-COMMANDS])
(defn answer [procs]
  {"type" "pane_process_info" "process_info" {"pane_id" "w1:p1" "foreground_processes" procs}})
(setv wrapper (answer [{"pid" 20 "name" "bash" "argv" ["bash" "./run-agent.sh"]}
                       {"pid" 21 "name" "2.1.201" "argv0" "claude"}]))
(print f"観測: 入力 A(wrapper の bash と claude)→ {(run (herdr-foreground-command wrapper))}(先頭の process は bash)")
(setv nu-git (answer [{"pid" 30 "name" "nu" "argv" ["nu"]} {"pid" 31 "name" "git" "argv" ["git" "status"]}]))
(setv before (run (herdr-foreground-command nu-git)))
(.add IDLE-SHELL-COMMANDS "nu")
(setv after (run (herdr-foreground-command nu-git)))
(.discard IDLE-SHELL-COMMANDS "nu")
(print f"観測: 入力 B(nu と git)→ policy の語彙を変える前 {before}・後 {after}(herdr の答えは同じ)")
