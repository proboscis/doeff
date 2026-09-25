;;; 盲検 A の再現: 答えに答えた herdr の版が載らないので、M2 は同じ答え X を版にかかわらず同じに読む。
;;; 版ごとに厳しく読む(protocol 23 なら argv0 の欠けを名指す)には、X の外から版を渡すしかない。
(import json)
(import doeff [run])
(import doeff_agents.sessionhost.substrate_herdr [herdr-foreground-command])
(setv schema (json.load (open "docs/design/daily-red-639/herdr-argv0/evidence/herdr-0.9.1-api.schema.json")))
(setv text (json.dumps schema))
(setv protocol (get schema "protocol"))
(setv has-handoff (in "expected_protocol" text))
(setv success (get (get (get schema "schemas") "success_response") "$defs"))
(setv fields (sorted (.keys (get (get success "PaneProcessInfo") "properties"))))
(print f"観測: schema(protocol {protocol})に server.live_handoff の expected_protocol がある = {has-handoff}")
(print f"観測: PaneProcessInfo の欄 = {fields}(版の欄は無い)")
(setv X {"type" "pane_process_info"
         "process_info" {"pane_id" "w7:p1" "shell_pid" 1643746 "foreground_process_group_id" 1643746
                         "foreground_processes" [{"pid" 1643746 "name" "zsh" "argv" ["/usr/bin/zsh"]
                                                  "cmdline" "/usr/bin/zsh" "cwd" "/home/kento"}]}})
(setv read (run (herdr-foreground-command X)))
(print f"観測: M2(X)= {read !r}(protocol 22 の server でも仮の protocol 23 の server でも入力は同じ X)")
