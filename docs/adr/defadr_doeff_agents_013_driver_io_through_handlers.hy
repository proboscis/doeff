;;; Executable ADR: doeff-agents の driver 層は、file・子 process・unix socket・
;;; 環境変数に自分で触らない。要求は `doeff_agents.io_effects` の語彙で出し、
;;; 果たすのは handler(本番 `io_handlers` / 検 `io_fake`)ちょうど 1 つで、
;;; どちらを当てるかは composition root(``io_root``)が選ぶ。
;;;
;;; 出自 = agora-redesign 段 7 lane 7c(決定 1.3・`docs/plans/decisions-merge-2026-09-12.md`):
;;;   「Hy は原則限定(B): 新規は Hy。既存の doeff の Python は当面残すが、
;;;     直接 I/O の 8 file は handler へ」
;;;
;;; 既知の形(known shapes 台帳): algebraic effects — effect は要求の値、
;;; handler は実行の家、composition root が家を選ぶ。この便は形の中の変更で、
;;; 新しい仕組みを 1 つも足していない。
;;;
;;; 語彙(agora-redesign master plan 0b): ここは「道具」の層。domain の言葉
;;; (会話・仕事・郵便)も、仕組みの語(手番)も持ち込まない。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <-])
(import doeff [run])
(import re)
(import pathlib [Path])

(import doeff_agents.io_effects [read-text write-text which-executable run-process])
(import doeff_agents.io_fake [FakeIoWorld run-fake-io])


;; ---------------------------------------------------------------------------
;; 名簿 — round 3 の棚卸しが数えた「直接 I/O を持つ 8 file」。
;;
;; 判定の条件(再現できる形): doeff-agents の src のうち sessionhost(agentd・
;; 既に Hy で適合)と handlers(I/O を果たす側)を除いた driver 層で、下の
;; IO-CALL-PATTERNS に当たる行を持つ file。2026-09-12 の実測で、この条件が
;; 数える file はちょうど 8 だった(round 3 の数と一致)。
;; ---------------------------------------------------------------------------

(setv DRIVER-IO-ROSTER
  #{"adapters/claude.py"
    "adapters/codex.py"
    "adapters/gemini.py"
    "agentd_client.py"
    "claude_home.py"
    "session_backend.py"
    "session_store.py"
    "tmux.py"})

;; 直接 I/O の字面。file・子 process・unix socket・環境変数・PATH の 5 種。
;; 時計(time.sleep / time.monotonic)は本 ADR の母集団の外 — driver 層の
;; 残り 2 file(cli.py・session.py)の待ちの effect 化は別便で、そこまでを
;; この法の射程にすると未着手を赤として持ち込むことになる。
;; `urllib.parse` は純粋な綴りの解釈なので I/O ではない(`urllib.request` だけ)。
(setv IO-CALL-PATTERNS
  ["(?<![\\w.])open\\("
   "(?<![\\w.])subprocess\\."
   "(?<![\\w.])socket\\.socket\\("
   "(?<![\\w.])shutil\\."
   "(?<![\\w.])urllib\\.request"
   "(?<![\\w.])tempfile\\."
   "(?<![\\w.])os\\.(?:environ|access|getpid|makedirs|replace|remove|symlink|listdir|chmod|kill)"
   "\\.read_text\\("
   "\\.write_text\\("
   "\\.mkdir\\("
   "\\.touch\\("
   "\\.chmod\\("
   "\\.rglob\\("
   "Path\\.home\\("])

;; 走査から除く: agentd(sessionhost — ADR-DOE-AGENTS-004 の凍結契約で
;; 自前の substrate 語彙を持つ)と handlers(I/O を果たす側)と、
;; driver 層の I/O を果たす 2 つの家。
(setv SCAN-SKIP-DIRS #{"sessionhost" "handlers" "__pycache__"})
(setv IO-HOME-FILES #{"io_handlers.hy" "io_fake.hy"})


(defk driver-source-root [package-root]
  {:pre [(: package-root Path)]
   :post [(: % Path)]}
  "driver 層の source の根。無ければ即座に落ちる — 走査が空になる形で
   法が緑になると、針は何も守らない。"
  (setv root (/ package-root "packages" "doeff-agents" "src" "doeff_agents"))
  (when (not (.is-dir root))
    (raise (AssertionError f"driver 層の source の根が無い: {root}")))
  root)


(defk scan-driver-io [package-root]
  {:pre [(: package-root Path)]
   :post [(: % dict)]}
  "driver 層の file 別の直接 I/O の行数(針と法の共通の物差し)。"
  (setv pattern (re.compile (.join "|" IO-CALL-PATTERNS)))
  (setv counts {})
  (setv root (run (driver-source-root package-root)))
  (for [source (sorted (+ (list (.rglob root "*.py")) (list (.rglob root "*.hy"))))]
    (setv rel (.relative-to source root))
    (when (or (& (set rel.parts) SCAN-SKIP-DIRS) (in rel.name IO-HOME-FILES))
      (continue))
    (setv hits (len (lfor line (.splitlines (.read-text source :encoding "utf-8" :errors "replace"))
                          :if (.search pattern line)
                          line)))
    (when (> hits 0)
      (setv (get counts (str rel)) hits)))
  counts)


(defk io-home-holds-the-syscalls [package-root]
  {:pre [(: package-root Path)]
   :post [(: % bool)]}
  "本番の家が実際に生の syscall を持っているか(空の法にしない反証)。"
  (setv pattern (re.compile (.join "|" IO-CALL-PATTERNS)))
  (setv home (/ (run (driver-source-root package-root)) "io_handlers.hy"))
  (> (len (lfor line (.splitlines (.read-text home :encoding "utf-8" :errors "replace"))
                :if (.search pattern line)
                line))
     0))


(defadr ADR-DOE-AGENTS-013
  :title "doeff-agents の driver 層は file・子 process・unix socket・環境変数へ自分で触らない: 要求は io_effects の語彙で出し、果たす家は本番 io_handlers と検 io_fake の 2 つ、どちらを当てるかは composition root の ``io_root`` が選ぶ。判断(argv の組み立て・protocol の解釈・何を書くかの決定)は driver の file に残る"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_agents_013_driver_io_through_handlers.hy"
          "packages/doeff-agents/src/doeff_agents"
          "packages/doeff-agents/tests"]
  :problem
    [(fact
       "operator の決め(agora-redesign 段 7・決定 1.3、2026-09-12): Hy は原則限定(B)— 新規は Hy、既存の doeff の Python は当面残すが、直接 I/O の 8 file は handler へ。"
       :evidence "agora-redesign docs/plans/decisions-merge-2026-09-12.md 1.3 / docs/design/known-shapes-2026-09-12.md:59")
     (fact
       "round 3 の棚卸し(2026-09-12): doeff-agents の sessionhost は Hy 9,351 行で既に適合。その外の driver 層は Python 8,600 行で、22 file が doeff の effect で書かれ、8 file が直接 I/O を持っていた。"
       :evidence "agora-redesign docs/design/discuss-remaining-decisions-round3-2026-09-12.html:82")
     (fact
       "直接 I/O は判断と同居していた: 例 = tmux.py の送信確認ループ(capture → 判定 → Enter 再送 → 待つ)が subprocess.run と time.sleep を直に持ち、検は `doeff_agents.tmux.subprocess.run` を monkeypatch する以外に撃てなかった(7 箇所)。"
       :evidence "変更前 packages/doeff-agents/tests/test_session_backend.py の monkeypatch site")
     (fact
       "monkeypatch は module の綴りに依存するので、I/O の家が動くたびに検が黙って素通しになる: `agentd_client._start_agentd_process` は raising=False で patch されており、関数名が変わると本物の Popen が走った。"
       :evidence "変更前 packages/doeff-agents/tests/test_agentd_client.py の 11 箇所の monkeypatch(raising=False)")]
  :context
    [(interpretation
       "effect は「要求の値」で、handler は「実行の家」。driver の file が値を出すだけになれば、判断は同じ場所に残したまま、実行だけを本番と検で差し替えられる — 既知の形(algebraic effects)の中の変更であり、新しい仕組みは 1 つも増えない。")
     (interpretation
       "agentd(sessionhost)の substrate 語彙は ADR-DOE-AGENTS-004 が Rust の oracle に対して凍結した契約面で、driver 層の要求(実行ファイルの探索・home の複製・unix socket の 1 往復)を持たない。2 つの語彙を今 1 つに畳むと凍結契約を揺らすので、driver 層は自分の語彙を持ち、統合は別便に登記する。")
     (interpretation
       "composition root は『どの家を当てるか』を 1 つの値(``io_root``)で持つ側。backend・handler・client を組む場所がその値を受け取れば、検は同じ program を記憶の中の世界で回せる — monkeypatch と違い、家が動いても検は素通しにならない。")]
  :decision
    [(rule R1 "driver 層(packages/doeff-agents/src/doeff_agents から sessionhost / handlers を除いた木)の file は、file・子 process・unix socket・環境変数・PATH に直接触らない。要求は `doeff_agents.io_effects` の語彙で出す。")
     (rule R2 "driver 層の I/O を実世界で果たすのは `doeff_agents.io_handlers` ちょうど 1 つ。検の家は `doeff_agents.io_fake` で、同じ語彙を記憶の中の file 系と台本にした子 process で果たす。")
     (rule R3 "どちらの家を当てるかは composition root が ``io_root`` で選ぶ。既定は本番の家で、検は fake を渡す。新しい I/O の要求を足す時は、2 つの家の両方に同じ便で足す。")
     (rule R4 "判断(argv の組み立て・出力の読み・protocol の組み立てと解釈・何を書くかの決定)は driver の file に純粋な関数として残す。handler に判断を移さない(handler は答えを素通しする)。")
     (rule R5 "検は I/O を monkeypatch で差し替えない。差し替えるのは家(``io_root``)で、実 socket・実 file が要る検は層を重ねた handler(`recorded-spawn-handler`)で必要な 1 点だけを控える。")]
  :laws
    [(law driver-io-goes-through-effects
       :statement "for_all source f in driver_layer: count_direct_io_calls(f) = 0 — driver 層に file・子 process・unix socket・環境変数の直書きは 1 行も存在しない"
       :counterexamples
         [(counterexample "tmux.py が subprocess.run を直に呼ぶ — 検は module の綴り(doeff_agents.tmux.subprocess.run)を monkeypatch するしかなく、I/O の家が動くと検が黙って素通しになる(2026-09-12 変更前の実測 7 箇所)")
          (counterexample "agentd_client.py が socket.socket を直に開く — 判断(JSON の組み立てと解釈)と 1 往復の物理が同じ関数に同居し、protocol の検が実 socket を要求する")])
     (law driver-io-has-one-production-home
       :statement "exists_unique module m in driver_layer: m holds the raw syscalls, and m = doeff_agents.io_handlers"
       :counterexamples
         [(counterexample "2 つ目の家(別 module の生の subprocess.run)が増える — 同じ要求が場所によって違う物理で果たされ、composition root の選択が効かなくなる")
          (counterexample "法だけ在って家が空 — 走査の除外 file が実際には syscall を 1 つも持たないなら、法は何も守っていない(空の法の検知が要る)")])
     (law driver-io-is-drivable-by-a-fake-home
       :statement "for_all driver_program p: run(fake_home, p) terminates without touching the real world, and answers from the in-memory world"
       :counterexamples
         [(counterexample "fake の家が要求を 1 つ落とす(handler に節が無い)— program は UnhandledEffect で落ち、検が『実世界を触らない』ことを示せない")])]
  :enforcement
    [(deftest test-adr-doe-agents-013-driver-layer-has-no-direct-io
       ;; 針: 実測 = scan-driver-io、法 = 0 行。
       (setv package-root (. (Path __file__) parent parent parent))
       (setv counts (run (scan-driver-io package-root)))
       (assert (= counts {})
               (+ "driver 層に直接 I/O が残っている(ADR-DOE-AGENTS-013 R1 — "
                  "要求は doeff_agents.io_effects の語彙で出し、果たすのは "
                  "io_handlers / io_fake): " (str (sorted (.items counts))))))
     (deftest test-adr-doe-agents-013-roster-files-are-in-the-driver-layer
       ;; 名簿の 8 file が実在し、走査の母集団に入っていること(名簿の腐り検知)。
       (setv package-root (. (Path __file__) parent parent parent))
       (setv root (run (driver-source-root package-root)))
       (setv missing (sorted (lfor rel DRIVER-IO-ROSTER
                                   :if (not (.exists (/ root rel)))
                                   rel)))
       (assert (= missing [])
               (+ "名簿の file が実在しない(ADR-DOE-AGENTS-013 — 移設した時は "
                  "DRIVER-IO-ROSTER を同便で直す): " (str missing)))
       (assert (= (len DRIVER-IO-ROSTER) 8)
               "round 3 の棚卸しが数えた 8 file と名簿の数が違う"))
     (deftest test-adr-doe-agents-013-production-home-is-not-empty
       ;; 空の法の検知: 本番の家が現に生の syscall を持っていること。
       (setv package-root (. (Path __file__) parent parent parent))
       (assert (run (io-home-holds-the-syscalls package-root))
               (+ "本番の家に生の syscall が無い(ADR-DOE-AGENTS-013 R2 — "
                  "家が空なら法は何も守っていない)")))
     (deftest test-adr-doe-agents-013-fake-home-drives-the-same-program
       ;; 検の家が同じ語彙を全部果たし、実世界を触らずに答えること。
       (setv world (FakeIoWorld :files {"/agent/in.txt" "hello"}
                                :which {"tmux" "/usr/bin/tmux"}))
       (assert (= (run-fake-io world (read-text "/agent/in.txt")) "hello"))
       (assert (is (run-fake-io world (read-text "/agent/missing.txt")) None))
       (assert (= (run-fake-io world (which-executable "tmux")) "/usr/bin/tmux"))
       (assert (is (run-fake-io world (which-executable "nope")) None))
       (run-fake-io world (write-text "/agent/out.txt" "written"))
       (assert (= (get world.files "/agent/out.txt") "written"))
       (assert (not (.exists (Path "/agent/out.txt")))
               "検の家が実 file を作っている(記憶の中の世界だけで果たすこと)")
       (setv outcome (run-fake-io world (run-process #("tmux" "-V"))))
       (assert (= outcome.exit-code 127)
               "台本に無い命令は『その命令は無い』で返る(黙って成功にしない)")
       (assert (= (len world.commands) 1)))]
  :plans [])
