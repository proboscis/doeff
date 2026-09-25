;;; 事前の主張(design-before-blind.md)の S1〜S4 を設計者が実際に試す最小の実験。共有の source は変えない。
;;; M2(herdr-foreground-command)・M3(herdr-pane-current-command-io)・M4(herdr-substrate)は作業樹の module を
;;; そのまま import し、herdr は偽の socket で置き換える(M1 の herdr-call は本物のまま socket へ話す)。
;;; 使い方: uv run --no-sync hy docs/design/daily-red-639/herdr-argv0/evidence/experiments_scenarios.hy
;;; 出力は「観測:」の行だけを見る。

(require doeff-hy.macros [defk <-])
(import doeff [run])
(import json os re shutil socket tempfile threading)
(import pathlib [Path])
(import doeff_agents.sessionhost.substrate_herdr [herdr-foreground-command
                                                 herdr-pane-current-command-io
                                                 herdr-substrate
                                                 HerdrApiError
                                                 HerdrContractError])
(import doeff_agents.sessionhost.effects [tmux-pane-current-command])
(import doeff_agents.sessionhost.policy [IDLE-SHELL-COMMANDS])

(defn process-info [processes]  ; defk にできない: 実験の材料を組む素の dict の工場(Program にしない)
  "実験用に pane.process_info の result を組む。"
  {"type" "pane_process_info"
   "process_info" {"pane_id" "w7:p1" "shell_pid" 100 "foreground_processes" processes}})

(defn envelope [result]  ; defk にできない: 同上
  "偽の socket が返す 1 行の封筒。"
  {"id" "doeff-substrate" "result" result})

(setv ZEUS (process-info [{"pid" 100 "name" "zsh" "argv" ["/usr/bin/zsh"]
                           "cmdline" "/usr/bin/zsh" "cwd" "/home/kento"}]))
(setv MAC (process-info [{"pid" 100 "name" "2.1.201" "argv0" "claude"
                          "argv" ["claude" "--resume"]}]))
(setv PANE "w7:p1")
(setv ZSH-OK #("value" "zsh"))
(setv CONTRACT-ERR #("HerdrContractError" None))
(setv BAD (process-info [{"pid" 100 "name" "zsh" "argv0" 5}]))

(defn serve [answers]  ; defk にできない: 偽の herdr server を thread で立てる実験の足場
  "answers を accept の順に 1 行ずつ返す偽の herdr socket。(path server thread d) を返す。"
  (setv d (tempfile.mkdtemp :dir "/tmp"))
  (setv path (os.path.join d "h.sock"))
  (setv server (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
  (.bind server path)
  (.listen server 64)
  (.settimeout server 20.0)
  (defn loop []  ; defk にできない: threading.Thread の target
    (for [answer answers]
      (setv #(conn _) (.accept server))
      (with [conn conn]
        (setv buf b"")
        (while (not-in b"\n" buf)
          (setv data (.recv conn 65536))
          (when (= data b"") (break))
          (+= buf data))
        (.sendall conn (.encode (+ (json.dumps answer) "\n") "utf-8")))))
  (setv thread (threading.Thread :target loop :daemon True))
  (.start thread)
  #(path server thread d))

(defn close [served]  ; defk にできない: 実験の足場の後片付け
  (setv #(path server thread d) served)
  (.join thread 20.0)
  (.close server)
  (shutil.rmtree d :ignore-errors True))

(defn outcome [thunk]  ; defk にできない: 実験の観測を値か例外の型名に畳む
  "run の値か、送出された例外の型名(と code)を返す。"
  (try
    #("value" (thunk))
    (except [e HerdrContractError] #("HerdrContractError" None))
    (except [e HerdrApiError] #("HerdrApiError" e.code))))

;; ---------------------------------------------------------------- S1(hardware)
(print "== S1 hardware")
(setv shapes {"zeus(Linux・argv0 無し)" ZEUS
              "Mac(argv0 有り・name は version)" MAC
              "Linux が argv0 を埋め始めた版" (process-info [{"pid" 1 "name" "zsh" "argv0" "zsh"
                                                              "argv" ["/usr/bin/zsh"]}])
              "argv も無い(WSL の agent・zombie)" (process-info [{"pid" 1 "name" "bash"}])
              "login shell の argv[0]" (process-info [{"pid" 1 "name" "bash" "argv" ["-bash"]}])})
(for [#(label shape) (.items shapes)]
  (print f"観測: M2 {label} → {(run (herdr-foreground-command shape))!r}"))
;; M4 → M3 → M2 → M1 の経路を effect から通し、M5 の語彙で判断できることを見る(本体は無改変)。
(setv served (serve [(envelope ZEUS) (envelope BAD)]))
(setv zeus-cmd (run ((herdr-substrate (get served 0)) (tmux-pane-current-command "w7:p1"))))
(print f"観測: effect TmuxPaneCurrentCommand(zeus の答え)→ {zeus-cmd !r}・idle shell = {(in zeus-cmd IDLE-SHELL-COMMANDS)}")
(print f"観測: effect TmuxPaneCurrentCommand(argv0 = 5 の答え)→ {(outcome (fn [] (run ((herdr-substrate (get served 0)) (tmux-pane-current-command PANE)))))}")
(close served)
;; 読みの知識(欄の名・正規化)が M2 の定義の外に無いこと(source の走査)。
(setv src (.read-text (Path "packages/doeff-agents/src/doeff_agents/sessionhost/substrate_herdr.hy")))
(setv m2-start (.index src "(defk herdr-foreground-command"))
(setv m2-end (.index src "(defk herdr-pane-current-command-io"))
(setv outside (+ (cut src 0 m2-start) (cut src m2-end None)))
(for [token ["\"foreground_processes\"" "\"argv0\"" "\"argv\"" "PurePosixPath" "removeprefix"]]
  (print f"観測: 語 {token} の出現 — M2 の中 {(.count (cut src m2-start m2-end) token)}・M2 の外 {(.count outside token)}"))
(setv src-root (Path "packages/doeff-agents/src"))
(setv other-files (lfor p (sorted (.rglob src-root "*.hy"))
                        :if (and (!= p.name "substrate_herdr.hy")
                                 (re.search r"foreground_processes|process_info|\"argv0\"" (.read-text p)))
                        (str p)))
(print f"観測: substrate_herdr.hy の外で process_info を読む source = {other-files}")

;; ---------------------------------------------------------------- S2(effects)
(print "== S2 effects")
(setv future (process-info [{"pid" 1 "name" "2.1.201" "argv0" "claude" "command" "claude"
                             "argv" ["claude"] "exe" "/opt/claude/bin/claude"}]))
(print f"観測: (a) 未知の欄を足した答え → {(run (herdr-foreground-command future))!r}(未知の欄は読まず、既存の保証欄で読める)")
(setv served (serve [{"id" "x" "error" {"code" "pane_not_found" "message" "pane not found"}}
                     {"id" "x" "error" {"code" "pane_exited" "message" "pane exited"}}]))
(print f"観測: (b) pane_not_found → {(outcome (fn [] (run (herdr-pane-current-command-io (get served 0) PANE))))}")
(print f"観測: (b) 未知の code pane_exited → {(outcome (fn [] (run (herdr-pane-current-command-io (get served 0) PANE))))}(None に畳まれない — 足すなら M3 の except の 1 点)")
(close served)

;; ---------------------------------------------------------------- S3(simulation)
(print "== S3 simulation")
(setv recorded (json.loads (json.dumps {"id" "probe" "result" ZEUS})))
(setv served (serve [recorded (envelope BAD)]))
(print f"観測: 記録した zeus の答えの再生(実 herdr なし)→ {(outcome (fn [] (run (herdr-pane-current-command-io (get served 0) PANE))))}")
(print f"観測: 契約の外の答えの再生 → {(outcome (fn [] (run (herdr-pane-current-command-io (get served 0) PANE))))}")
(close served)

;; ---------------------------------------------------------------- S4(concurrency)
(print "== S4 concurrency")
(defn concurrent [answers]  ; defk にできない: thread で M3 を並行に呼ぶ実験の足場
  (setv served (serve answers))
  (setv results [])
  (setv lock (threading.Lock))
  (defn call []  ; defk にできない: threading.Thread の target
    (setv got (outcome (fn [] (run (herdr-pane-current-command-io (get served 0) PANE)))))
    (with [lock] (.append results got)))
  (setv threads (lfor _ answers (threading.Thread :target call)))
  (for [t threads] (.start t))
  (for [t threads] (.join t 30.0))
  (close served)
  results)
(setv ok (concurrent (* [(envelope ZEUS)] 16)))
(print f"観測: 16 並行・全部 zeus の答え → {(len ok)} 件・値 {(sorted (set (map str ok)))}")
(setv mixed (concurrent (+ (* [(envelope ZEUS)] 15) [(envelope BAD)])))
(print f"観測: 16 並行・1 通だけ契約の外 → zsh {(.count mixed ZSH-OK)} 件・HerdrContractError {(.count mixed CONTRACT-ERR)} 件")
