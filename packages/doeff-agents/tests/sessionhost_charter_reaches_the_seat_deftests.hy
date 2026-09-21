;;; charter の欄は 4 枚の名簿を渡って席へ届く — 1 枚でも落ちたら赤
;;; (card acp:kanban-issue:ki-a40292ed30d9)。
;;;
;;; 直す欠陥: 同じ壊れ方が 3 度起きた。
;;;   2026-09-15  添付(attachments)    — resume の名簿に入れ忘れ、**腕が resume の手番だけ**画像が落ちた
;;;   2026-09-18  圧縮の閾値           — wire の受理形が 23 欄の閉じた名簿で、どの腕でも席に届かなかった
;;;   2026-09-21  記憶の**本文**       — 置き場(memory_dir)だけを 4 枚の名簿へ手で足した便の次の便で、
;;;                                      同じ族の memory_files が wire の受理形で落ちた(pool の pod で実測:
;;;                                      ACP の行 44 冊 / 置き場 0 file・索引も無し)
;;;
;;; 3 度とも警告は落ちている名簿の 20 行上に在った ⇒ 註の距離の問題ではない。根は
;;; **欄を 1 つ足す操作が名簿を 4 枚とも手で触らせる形**。直したのはその形:
;;;
;;;   ・席へ運ぶ欄の集合は定義点 1 つ(acp/effects.py CHARTER_CARRIED_KEYS ↔ policy.CHARTER-CARRIED-KEYS)
;;;   ・4 枚の名簿は policy.carry-charter-fields を呼ぶだけで、欄の名を 1 つも数えない
;;;   ・この検は **欄の手書きの名簿を持たない**: charter の欄は module を反射して数える
;;;     (CHARTER_*_KEY の定数)。新しい欄を足して行き先を宣言しないと、ここが赤になる。
;;;
;;; 母集団は「起こす側が組んだ charter」で止めない(依頼書 制約 4): 4 枚の名簿を全部通し、
;;; 最後は**器が置き場へ書いた file** を数える(届いた所が母集団)。
;;; 冊数は本文に焼かない(制約 5): N 冊 → 置き場に N + 1 file(冊 N + 索引 MEMORY.md)。

(require doeff-hy.macros [deftest defk <-])

(import json)

(import doeff_agents.sessionhost.policy [CHARTER-CARRIED-KEYS LAUNCH-FLAG-KEYS TURN-CARRIED-KEYS])
(import doeff_agents.sessionhost.acp [effects])
(import doeff_agents.sessionhost.acp.effects [
  CHARTER-CARRIED-KEYS :as ACP-CHARTER-CARRIED-KEYS
  CHARTER-KEYS-AGENTD-CONSUMES
  CHARTER-MEMORY-DIR-KEY
  CHARTER-MEMORY-FILES-KEY
  MEMORY-INDEX-FILE])
(import doeff_agents.sessionhost.acp.judgment [memory-files-of resume-params-of])
(import doeff_agents.sessionhost.effects [SessionRow])
(import doeff_agents.sessionhost.host [
  DEFAULT-PROMPT-JUDGE-CMD
  HostConfig
  build-launch-program-params
  build-resume-program-params])
(import doeff_agents.sessionhost.launch [resume-launch-params-of])

(import sessionhost_launch_deftests [LaunchWorld launch-params run-launch])
(import sessionhost_resume_deftests [resume-params run-resume seed-source])


(setv MEMORY-HOME "/state/doeff/agent-memory/c-01M28FFPKA9NDM1WCASFVC63W1")


;; ---------------------------------------------------------------------------
;; charter の欄の母集団は**反射で**採る(手書きの名簿を持たない)
;; ---------------------------------------------------------------------------

(defn #^ frozenset declared-charter-keys []
  "acp/effects.py が宣言した charter の欄の綴り(CHARTER_*_KEY の定数)。

   ⚠ ここが検の母集団で、**手で並べた列ではない** — 新しい欄の定数を足した瞬間に母集団へ入る
   (依頼書 制約 2: 検が欄の手書き名簿を持たないこと)。"
  (frozenset (gfor #(name value) (.items (vars effects))
                   :if (and (.startswith name "CHARTER_") (.endswith name "_KEY")
                            (isinstance value str))
                   value)))


(defn #^ frozenset keys-to-the-seat []
  "席へ届かなければならない欄 = 宣言された欄のうち、agentd 自身が読んで消費すると
   名乗っていないもの(引き算 — こちらの名簿は作らない)。"
  (- (declared-charter-keys) (frozenset CHARTER-KEYS-AGENTD-CONSUMES)))


;; 型が要る欄の**値**の見本(名簿ではない — 鍵の集合は上の反射が決める)。
;; ここに無い欄は文字列の見本で撃つ。新しい欄が型を要るなら、ここへ 1 行足すまで検は赤のまま。
(setv PROBE-VALUES {"work_dir" "/work/dir"
                    "auto_compact_window" 800000
                    "memory_dir" MEMORY-HOME
                    "memory_files" [{"name" "a.md" "text" "A"}
                                    {"name" MEMORY-INDEX-FILE "text" "# MEMORY\n"}]})


(defn #^ dict probe-charter []
  "宣言された『席へ届く欄』を全部名乗った charter(見本の値つき)。"
  (setv charter {"session_id" "s1" "session_name" "doeff-s1" "agent_type" "claude"})
  (for [key (sorted (keys-to-the-seat))]
    (setv (get charter key) (.get PROBE-VALUES key f"probe::{key}")))
  charter)


(defn #^ HostConfig host-config []
  (HostConfig :db-path "/tmp/x.db" :socket-path "/tmp/x.sock"
              :tmux-bin "tmux" :monitor-interval-seconds 1.0
              :max-running 4 :result-solicitation-limit 3
              :prompt-stall-seconds 90 :prompt-unblock-limit 3
              :prompt-judge-cmd DEFAULT-PROMPT-JUDGE-CMD))


(defn #^ list missing-of [charter arrived]
  "charter が名乗った『席へ届く欄』のうち、届いた params に無い / 値が変わったもの。"
  (lfor key (sorted (keys-to-the-seat))
        :if (and (in key charter)
                 (or (not-in key arrived) (!= (get arrived key) (get charter key))))
        key))


;; ---------------------------------------------------------------------------
;; (1) 欄を足す操作は行き先を宣言させる(この 1 本が「検の名簿」を charter に縛る針)
;; ---------------------------------------------------------------------------

(deftest test-every-declared-charter-key-declares-where-it-goes
  ;; charter の欄の行き先は 2 つしかない: 席へ運ぶ / agentd が読む。新しい CHARTER_*_KEY を
  ;; 足して行き先を宣言しないと、下の (2)(3) が「席に届かない欄」として赤くなる。
  ;; ここではその 2 分割が **charter の欄と一致している**ことだけを撃つ(依頼書 制約 2 の針)。
  (setv declared (declared-charter-keys))
  (setv consumes (frozenset CHARTER-KEYS-AGENTD-CONSUMES))
  (setv carried (frozenset ACP-CHARTER-CARRIED-KEYS))
  ;; 腐った名 = 宣言されていない欄を名指している(欄の定数を消した便が名簿を置き去りにした印)。
  (assert (<= consumes declared)
          #("agentd が読むと名乗る欄が charter の欄に無い" (sorted (- consumes declared))))
  (assert (<= carried declared)
          #("名簿が写す欄が charter の欄に無い" (sorted (- carried declared))))
  ;; 2 つの行き先は重ならない(同じ欄が「席へ運ぶ」と「agentd が読む」を同時に名乗らない)。
  (assert (not (& consumes carried))
          #("行き先が 2 つに割れている欄" (sorted (& consumes carried))))
  ;; wire(ACP)側と host(policy)側の写しは**同じ語**(綴りが割れると黙って落ちる)。
  (assert (= (tuple ACP-CHARTER-CARRIED-KEYS) (tuple CHARTER-CARRIED-KEYS))
          #(ACP-CHARTER-CARRIED-KEYS CHARTER-CARRIED-KEYS))
  ;; 旗(行にも残る)は席へ運ぶ欄の部分集合 — 行に残す腕だけが旗を写す。
  (assert (<= (frozenset LAUNCH-FLAG-KEYS) (frozenset CHARTER-CARRIED-KEYS)))
  (assert (= (frozenset CHARTER-CARRIED-KEYS)
             (| (frozenset LAUNCH-FLAG-KEYS) (frozenset TURN-CARRIED-KEYS)))))


;; ---------------------------------------------------------------------------
;; (2) 起こす腕(launch / rehydrate)— wire の受理形で 1 欄も落ちない
;; ---------------------------------------------------------------------------

(deftest test-declared-charter-keys-reach-the-seat-on-the-launch-arm
  ;; 名簿 1 枚目(host.build-launch-program-params)。2026-09-18 の閾値と 2026-09-21 の
  ;; 記憶の本文は、どちらもこの 1 枚で落ちた。
  (setv charter (probe-charter))
  (setv arrived (build-launch-program-params charter (host-config)))
  (setv missing (missing-of charter arrived))
  (assert (not missing)
          #("起こす腕で席に届かなかった charter の欄" missing
            "足す所は policy.CHARTER-CARRIED-KEYS の 1 点(名簿を 4 枚触らない)")))


;; ---------------------------------------------------------------------------
;; (3) 蘇生の腕 — 3 枚の名簿を続けて渡っても 1 欄も落ちない
;; ---------------------------------------------------------------------------

(deftest test-declared-charter-keys-reach-the-seat-on-the-resume-arm
  ;; 名簿 2〜4 枚目を**続けて**通す: charter → judgment.resume-params-of → wire の受理形
  ;; (host.build-resume-program-params)→ 蘇生の名簿(launch.resume-launch-params-of)。
  ;; 2026-09-15 の添付はこの列の 1 枚目で落ちて、腕が resume の手番だけ画像が消えた。
  (setv charter (probe-charter))
  (<- wire dict (resume-params-of "s1" charter))
  (setv program (build-resume-program-params wire (host-config) "s1" "resume" #()))
  (setv source (SessionRow :session-id "s1" :session-name "doeff-s1" :pane-id "%1"
                           :agent-type "claude" :lifecycle "run_to_completion" :status "done"
                           :started-at "2026-09-21T00:00:00+00:00"
                           :work-dir (get PROBE-VALUES "work_dir")
                           :effective-identity {"CLAUDE_CONFIG_DIR" "/x/claude"}
                           :conversation {"session_id" "conv-1"} :generation 1))
  (<- arrived dict (resume-launch-params-of program source {} {} None {"session_id" "conv-1"}
                                            "resume" "s1~g2" "doeff-s1~g2" "s1" 2 None))
  (setv missing (missing-of charter arrived))
  (assert (not missing)
          #("蘇生の腕で席に届かなかった charter の欄" missing
            "足す所は acp/effects.py CHARTER_CARRIED_KEYS の 1 点(名簿を 4 枚触らない)")))


;; ---------------------------------------------------------------------------
;; (4) 届いた所で測る — 器が置き場へ書いた file を数える
;; ---------------------------------------------------------------------------

(defn #^ list books-in [world home]
  "置き場に書かれた file の名(器が実際に書いた物だけ)。"
  (sorted (gfor path (.keys world.fs)
                :if (.startswith path f"{home}/")
                (.removeprefix path f"{home}/"))))


(defn #^ tuple sample-books [n]
  "N 冊 + 索引 = 器へ渡す memory_files(冊の本文は行から来た体)。N は検の引数で、本文に焼かない。"
  (setv books (lfor i (range n)
                    {"name" f"book-{i}.md"
                     "text" (+ "---\n" f"name: book-{i}\n" "description: d\n"
                               "metadata:\n  type: project\n---\n\n" f"body {i}\n")}))
  (tuple (+ books [{"name" MEMORY-INDEX-FILE "text" "# MEMORY\n"}])))


(deftest test-the-seat-writes-one-file-per-book-and-the-index-on-the-launch-arm
  ;; 起こす腕の**届いた所**: 器(impls/claude_code)が置き場へ書いた file を数える。
  ;; N 冊を渡したら N + 1 file(冊 N + 索引 MEMORY.md)。冊数は本文に焼かない。
  (for [n #(0 1 3)]
    (setv world (LaunchWorld))
    (setv world.capture-script ["❯ {composer}"])
    (setv books (sample-books n))
    (<- row (run-launch world (launch-params
                                :agent_type "claude"
                                :binding {"kind" "claude-code" "config_dir" "/x/claude"}
                                :memory_dir MEMORY-HOME
                                :memory_files (list books))))
    (setv written (books-in world MEMORY-HOME))
    (assert (= (len written) (+ n 1))
            #("N 冊 → 置き場に N + 1 file(冊 N + 索引)" n written))
    (assert (in MEMORY-INDEX-FILE written) written)
    ;; 本文は byte で届く(名だけ在って中身が空の file を数えない)。
    (for [book books]
      (assert (= (get world.fs f"{MEMORY-HOME}/{(get book "name")}") (get book "text"))
              #("置き場の本文が器へ渡した本文と違う" (get book "name"))))))


(deftest test-the-seat-writes-one-file-per-book-and-the-index-on-the-resume-arm
  ;; 同じ計り方を蘇生の腕で(名簿 3 枚を渡った後)。この腕が落ちていると、**手番のたびに**
  ;; 記憶が剥がれる: ACP の手番は前の incarnation を継ぐので、普段の手番はこの腕を通る。
  (setv n 3)
  (setv world (LaunchWorld))
  (seed-source world :agent_type "claude"
               :effective_identity {"CLAUDE_CONFIG_DIR" "/x/claude"}
               :conversation {"session_id" "conv-A"})
  (setv world.capture-script ["❯ {composer}"])
  (setv (get world.fs "/x/claude/projects/-work-dir/conv-A.jsonl") "{}")
  (setv books (sample-books n))
  (<- row (run-resume world (resume-params :memory_dir MEMORY-HOME
                                           :memory_files (list books))))
  (setv written (books-in world MEMORY-HOME))
  (assert (= (len written) (+ n 1))
          #("N 冊 → 置き場に N + 1 file(冊 N + 索引)" n written))
  (assert (in MEMORY-INDEX-FILE written) written))


;; ---------------------------------------------------------------------------
;; (5) 起こす側が組んだ charter で、端から端まで 1 度に通す
;; ---------------------------------------------------------------------------

(deftest test-the-charter-the-agentd-built-loses-no-book-on-the-way-to-the-seat
  ;; 母集団を「起こす側が組んだ charter」で止めない(依頼書 制約 4)。agentd が行から読んだ冊
  ;; (judgment.memory-files-of の本物)を charter に載せ、wire の受理形を通し、器に書かせる。
  ;; 行 N 冊 → 置き場 N + 1 file が 1 本の道で繋がる。
  (setv n 4)
  (setv books (sample-books n))
  (setv charter {"session_id" "s1" "session_name" "doeff-s1" "agent_type" "claude"
                 "work_dir" "/work/dir" "lifecycle" "run_to_completion"
                 "binding" {"kind" "claude-code" "config_dir" "/x/claude"}
                 "session_env" {} "prompt" "go" "mcp_servers" {}
                 "skip_trust_setup" False "command" None
                 CHARTER-MEMORY-DIR-KEY MEMORY-HOME
                 CHARTER-MEMORY-FILES-KEY (list books)})
  ;; wire の受理形(RPC 越しの手番が通る 1 枚) — ここを素通りしなければ器は 1 冊も書けない。
  (setv program (build-launch-program-params charter (host-config)))
  (assert (in CHARTER-MEMORY-FILES-KEY program) (sorted (.keys program)))
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯ {composer}"])
  (setv (get program "socket_path") "/tmp/agentd.sock")
  (<- row (run-launch world program))
  (setv written (books-in world MEMORY-HOME))
  (assert (= (len written) (+ n 1))
          #("行 N 冊 → 置き場 N + 1 file" n written)))


(deftest test-the-index-the-agentd-derives-from-the-rows-reaches-the-seat
  ;; 索引 MEMORY.md は file としての正本を持たず、行から組み直される(memory-files-of)。
  ;; その索引も同じ 1 枚の名簿を渡る — 渡らないと置き場に索引だけが古いまま残る。
  (<- files tuple (memory-files-of #()))
  (assert (= (len files) 1) files)
  (assert (= (get (get files 0) "name") MEMORY-INDEX-FILE) files))
