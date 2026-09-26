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

(require doeff-hy.macros [deftest defk <- defhandler])

(import datetime [datetime timezone])
(import json)
(import doeff [run])

(import doeff_agents.sessionhost.policy [CHARTER-CARRIED-KEYS LAUNCH-FLAG-KEYS TURN-CARRIED-KEYS
                                         carry-keys carry-launch-flags])
(import doeff_agents.sessionhost.acp [effects])
(import doeff_agents.sessionhost.acp.effects [
  CHARTER-CARRIED-KEYS :as ACP-CHARTER-CARRIED-KEYS
  CHARTER-KEYS-AGENTD-CONSUMES
  CHARTER-MEMORY-DIR-KEY
  CHARTER-MEMORY-FILES-KEY
  CHARTER-MEMORY-RETIRED-FILES-KEY
  MEMORY-BASE-FILE
  MEMORY-FILE-SUFFIX
  MEMORY-INDEX-FILE
  MemoryBaseline
  MemoryBook
  MemoryTurnFiles])
(import doeff_agents.sessionhost.acp.judgment [
  memory-baseline-text-of
  memory-baselines-of-text
  memory-files-of
  resume-params-of])
(import doeff_agents.sessionhost.effects [
  ClockNow
  EnvGet
  FsMakeDirs
  FsReadText
  FsRemoveFile
  FsWriteTextAtomic
  LogLine
  SessionRow
  SessionStoreGet
  SessionStoreRecordEvent
  SessionStoreUpsert])
(import doeff_agents.sessionhost.headless_effects [
  BuildHeadlessLaunch
  HeadlessDeliver
  HeadlessPoll
  HeadlessSpawn])
(import doeff_agents.sessionhost.headless [headless-send-program])
(import doeff_agents.sessionhost.impls.headless_argv [build-headless])
(import doeff_agents.sessionhost.impls.claude_code [claude-code-impl])
(import doeff_agents.sessionhost.host [
  DEFAULT-PROMPT-JUDGE-CMD
  HostConfig
  build-launch-program-params
  build-resume-program-params])
(import doeff_agents.sessionhost.launch [resume-launch-params-of])
(import doeff_agents.sessionhost.impls.claude_code [CLAUDE-MEMORY-BASE-FILE
                                                   CLAUDE-MEMORY-FILE-SUFFIX
                                                   CLAUDE-MEMORY-FILES-KEY
                                                   CLAUDE-MEMORY-RETIRED-FILES-KEY
                                                   CLAUDE-MEMORY-INDEX-FILE])

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
                                    {"name" MEMORY-INDEX-FILE "text" "# MEMORY\n"}
                                    {"name" MEMORY-BASE-FILE "text" "{\"books\": {}}\n"}]
                    ;; 退役した行と同じ名前で置き場から取り除く file(card ki-6b5c4b270ca0)。
                    "memory_retired_files" ["gone.md"]
                    ;; 会話の記録から組み直した transcript(card ki-c3aace97d825)— 器が家へ置いて --resume する。
                    "rebuilt_transcript" {"session_id" "11111111-2222-3333-4444-555555555555"
                                          "text" "{\"type\": \"user\"}\n"}})


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
  (<- program dict (build-resume-program-params wire (host-config) "s1" "resume" #()))
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
;; (3b) 4 つ目の腕 — 降りた process の続き(headless.continue-headless-process)
;; ---------------------------------------------------------------------------
;;
;; claude の headless の process は手番の終わりで必ず降りる(段 12 lane 12e #517)ので、
;; **普段の手番**(温かい session への send)はこの腕を通る。ところがこの腕は起こす腕の名簿
;; (prepare-launch-workspace → PreLaunchSetup)を 1 枚も通らず、行の launch_overlay だけを読んで
;; argv を組み直していた ⇒ 行に残さない手番の荷(policy.TURN-CARRIED-KEYS = 記憶の置き場と冊)は
;; **この腕だけ**落ちる。2026-09-21 の実測(pool の pod): 起こした手番は置き場を名乗るのに、
;; その会話の 2 手番目からは `--settings` に置き場が無く、置き場に冊も索引も書かれない。
;;
;; ここは 2 本とも「届いた所」で測る: 席の argv を組む 1 点(BuildHeadlessLaunch)に届いた欄と、
;; 器が置き場へ書いた file。

(defclass ContinueWorld []
  "4 つ目の腕の世界: 行は在る・process は降りている(HeadlessPoll が None = 登記に process 無し)。"
  (defn __init__ [self]
    (setv self.rows {})
    (setv self.fs {})        ;; path → 本文(器が置き場へ書いた file)
    (setv self.removed [])   ;; 器が置き場から取り除いた path(card ki-6b5c4b270ca0)
    (setv self.dirs (set))
    (setv self.log-lines [])
    (setv self.built [])     ;; BuildHeadlessLaunch が受けた params(= 席に届いた欄)
    (setv self.spawns [])    ;; #(argv env)— 起こし直した process
    (setv self.delivered [])))


(defhandler fake-continue-substrate [world]
  (SessionStoreGet [session-id]
    (resume (.get world.rows session-id)))
  (SessionStoreUpsert [row]
    (setv (get world.rows row.session-id) row)
    (resume None))
  (SessionStoreRecordEvent [session-id event-type row]
    (resume None))
  (ClockNow []
    (resume (datetime 2026 9 21 12 0 0 :tzinfo timezone.utc)))
  (EnvGet [name]
    (resume None))
  (LogLine [text]
    (.append world.log-lines text)
    (resume None))
  (FsMakeDirs [path]
    (.add world.dirs path)
    (resume None))
  (FsWriteTextAtomic [path text tmp-suffix]
    (setv (get world.fs path) text)
    (resume None))
  (FsReadText [path]
    ;; 本物の substrate と同じ契約: 在れば本文・不在は None。続きの腕は起こす前に家の
    ;; settings.json を読み、冷えた再開の前の圧縮を撃つかを決める(headless-cold-compaction-run)。
    ;; この世界の家には settings.json が無い = plugin の効かない profile なので圧縮は撃たれない。
    (resume (.get world.fs path)))
  (FsRemoveFile [path]
    ;; card acp:kanban-issue:ki-6b5c4b270ca0: 名指した 1 file を落とす(不在は成功)。
    (.append world.removed path)
    (resume (is-not (.pop world.fs path None) None)))
  (HeadlessPoll [session-name]
    ;; 降りている = 次の手番は「起こし直す腕」を通る(claude の普段の手番)。
    (resume None))
  (HeadlessDeliver [session-name text attachments]
    (.append world.delivered text)
    (resume True))
  (HeadlessSpawn [session-name work-dir env argv events-path dialogue]
    (.append world.spawns #((list argv) (dict env)))
    (resume 4242))
  (BuildHeadlessLaunch [agent-type params]
    ;; 席へ届いた欄はここで採る(この後ろは argv の組み立てだけ)。argv は本物の組み立てに通す —
    ;; 「params には在るが旗にならない」形をこの検で見落とさないため。
    (.append world.built (dict params))
    (<- built (build-headless agent-type params))
    (resume built)))


(defn #^ SessionRow continued-row [[overlay None]]
  "続きの手番を待つ行(headless・会話 identity 在り・process は降りている)。"
  (SessionRow :session-id "s1" :session-name "doeff-s1" :pane-id "headless:doeff-s1"
              :agent-type "claude" :lifecycle "run_to_completion" :status "running"
              :started-at "2026-09-21T00:00:00+00:00"
              :work-dir (get PROBE-VALUES "work_dir")
              :effective-identity {"CLAUDE_CONFIG_DIR" "/x/claude"}
              :backend-kind "headless"
              :backend-ref {"session_name" "doeff-s1" "pid" 41
                            "events_path" "/state/events/s1.events.jsonl"
                            "argv" [] "socket_path" ""}
              :launch-overlay (or overlay {})
              :conversation {"session_id" "conv-1"} :generation 1))


(defn #^ dict turn-charter-of [charter]
  "この手番の送りが運ぶ荷 = 行に残さない欄(policy.TURN-CARRIED-KEYS)だけ。
   **行の写しではない**: 正本は ACP の行で、手番ごとに charter が名乗り直す(名簿は 1 点)。"
  (carry-keys TURN-CARRIED-KEYS charter {}))


(defk run-continue [world row turn-charter]
  {:pre [(: world ContinueWorld) (: row SessionRow) (: turn-charter dict)]
   :post [(: % SessionRow)]}
  "4 つ目の腕を 1 度撃つ: 行は在るが process が降りている session へ次の手番を送る
   (host の session.send = headless-send-program → continue-headless-process)。"
  (setv (get world.rows row.session-id) row)
  (<- sent ((fake-continue-substrate world)
            ((claude-code-impl "/opt/doeff-sessionhost")
             (headless-send-program row.session-id "next turn" True {} turn-charter))))
  sent)


(deftest test-declared-charter-keys-reach-the-seat-on-the-continue-arm
  ;; 4 つ目の腕でも charter の欄は 1 つも落ちない。行に残る旗(LAUNCH-FLAG-KEYS)は行の
  ;; launch_overlay から、行に残さない手番の荷(TURN-CARRIED-KEYS)は**この手番の送り**から来る。
  (setv charter (probe-charter))
  (setv world (ContinueWorld))
  (setv overlay (carry-launch-flags charter {"session_env" {} "model" None "effort" None
                                             "mcp_servers" {}}))
  (<- _ (run-continue world (continued-row overlay) (turn-charter-of charter)))
  (assert (= (len world.built) 1) #("続きの腕は席の argv を 1 度だけ組む" world.built))
  (setv arrived (get world.built 0))
  (setv missing (missing-of charter arrived))
  (assert (not missing)
          #("続きの腕で席に届かなかった charter の欄" missing
            "足す所は policy.CHARTER-CARRIED-KEYS の 1 点(名簿を 5 枚触らない)"))
  ;; 届いた所まで: 起こし直した argv に置き場が出る(params に在るのに旗にならない形を残さない)。
  (assert (= (len world.spawns) 1) world.spawns)
  (setv argv (get (get world.spawns 0) 0))
  (assert (any (gfor arg argv (and (isinstance arg str) (in MEMORY-HOME arg))))
          #("続きの腕の argv が置き場を名乗らない" argv)))


(deftest test-the-seat-writes-one-file-per-book-and-the-index-on-the-continue-arm
  ;; 置き場の名だけでは足りない(依頼者の必須 B): 冊を書く口も 4 つ目の腕を通らないと、
  ;; 2 手番目から先は**空の置き場**を指した席が走る。N 冊 → 置き場に N + 2 file
  ;; (冊 N + 索引 + 畳み戻しの基準)。畳み戻しの基準がこの腕で落ちると、席が書いた冊が
  ;; 「席の触っていない写し」に見えて行が巻き戻る(9c5fdefc が直した形)。
  ;; 計器も同じ腕で 1 行(0 冊でも名乗る — 黙る拍を作らない)。
  (for [n #(0 1 3)]
    (setv world (ContinueWorld))
    (setv books (sample-books n))
    (<- _ (run-continue world (continued-row)
                        {"memory_dir" MEMORY-HOME "memory_files" (list books)}))
    (setv written (books-in world MEMORY-HOME))
    (assert (= (len written) (+ n 2))
            #("N 冊 → 置き場に N + 2 file(冊 N + 索引 + 畳み戻しの基準)" n written))
    (assert (in MEMORY-INDEX-FILE written) written)
    (assert (in MEMORY-BASE-FILE written) written)
    (for [book books]
      (assert (= (get world.fs f"{MEMORY-HOME}/{(get book "name")}") (get book "text"))
              #("置き場の本文が器へ渡した本文と違う" (get book "name"))))
    (setv lines (memory-log-lines world))
    (assert (= (len lines) 1)
            #("続きの腕も書いた数を 1 行で名乗る(0 冊でも)" n world.log-lines))
    (assert (in f"books={n}" (get lines 0)) (get lines 0))
    (assert (in MEMORY-HOME (get lines 0)) (get lines 0))
    ;; 名乗りは**どの腕の置き場か**まで言う(起こす腕は session.launch)— log だけで
    ;; 「2 手番目から書かれていない」形を読めるようにする。
    (assert (.startswith (get lines 0) "session.send: ") (get lines 0))))


;; ---------------------------------------------------------------------------
;; (4) 届いた所で測る — 器が置き場へ書いた file を数える
;; ---------------------------------------------------------------------------

(defn #^ list books-in [world home]
  "置き場に書かれた file の名(器が実際に書いた物だけ)。"
  (sorted (gfor path (.keys world.fs)
                :if (.startswith path f"{home}/")
                (.removeprefix path f"{home}/"))))


(defn #^ str sample-baseline-text [names]
  "N 冊分の基準(器が置き場へ置く side car の本文)。"
  (run (memory-baseline-text-of
         (dfor name names name (MemoryBaseline :name name :record-seq (+ 1 (len name))
                                               :sha256 (* "a" 64) :version 1)))))


(defn #^ tuple sample-books [n]
  "N 冊 + 索引 + 基準 = 器へ渡す memory_files(本物の memory-files-of と同じ形)。N は検の引数で、
   本文に焼かない。基準は名簿の**最後**で、器はこれを loop では書かず、書けた冊へ絞って最後に置く。"
  (setv books (lfor i (range n)
                    {"name" f"book-{i}{MEMORY-FILE-SUFFIX}"
                     "text" (+ "---\n" f"name: book-{i}\n" "description: d\n"
                               "metadata:\n  type: project\n---\n\n" f"body {i}\n")}))
  (tuple (+ books [{"name" MEMORY-INDEX-FILE "text" "# MEMORY\n"}
                   {"name" MEMORY-BASE-FILE "text" (sample-baseline-text (lfor i (range n) f"book-{i}"))}])))


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
    (assert (= (len written) (+ n 2))
            #("N 冊 → 置き場に N + 2 file(冊 N + 索引 + 畳み戻しの基準)" n written))
    (assert (in MEMORY-INDEX-FILE written) written)
    (assert (in MEMORY-BASE-FILE written) written)
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
  (assert (= (len written) (+ n 2))
          #("N 冊 → 置き場に N + 2 file(冊 N + 索引 + 畳み戻しの基準)" n written))
  (assert (in MEMORY-INDEX-FILE written) written)
  (assert (in MEMORY-BASE-FILE written) written))


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
  (assert (= (len written) (+ n 2))
          #("行 N 冊 → 置き場 N + 2 file(冊 + 索引 + 基準)" n written)))


(deftest test-the-index-the-agentd-derives-from-the-rows-reaches-the-seat
  ;; 索引 MEMORY.md は file としての正本を持たず、行から組み直される(memory-files-of)。
  ;; その索引も同じ 1 枚の名簿を渡る — 渡らないと置き場に索引だけが古いまま残る。
  (<- turn MemoryTurnFiles (memory-files-of #() {} #()))
  (assert (= (lfor f turn.files (get f "name")) [MEMORY-INDEX-FILE MEMORY-BASE-FILE]) turn))


;; ---------------------------------------------------------------------------
;; (6) 器が**書いた数**は起動ごとに 1 行で読める(card ki-a40292ed30d9 受入 3)
;; ---------------------------------------------------------------------------

(defn #^ list memory-log-lines [world]
  (lfor line world.log-lines :if (in "agent-memory-written" line) line))


(deftest test-the-seat-names-how-many-books-it-wrote
  ;; 起票者の受入 3: 「器が冊を書いたことが log の 1 行で読める」。
  ;; agentd 側の agent-memory-hydrated が数えるのは**行から読んだ数**で、書いた数ではない。
  ;; 今回の壊れ方では「読んだ 44 / 書いた 0」だったのに、log は読みの数しか言わなかった
  ;; ⇒ 同じ形がまた起きた時に無音にならないよう、書いた数をこの座で名乗る。
  (setv n 3)
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯ {composer}"])
  (<- row (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code" "config_dir" "/x/claude"}
                              :memory_dir MEMORY-HOME
                              :memory_files (list (sample-books n)))))
  (setv lines (memory-log-lines world))
  (assert (= (len lines) 1) #("書いた数の名乗りは起動ごとに 1 行" world.log-lines))
  (setv line (get lines 0))
  (assert (in f"books={n}" line) line)
  (assert (in "index=1" line) line)
  ;; 基準は冊とも索引とも別に数える(足した file が books を 1 増やすと受入の N + 2 が崩れる)。
  (assert (in "base=1" line) line)
  (assert (in f"declared={(+ n 2)}" line) line)
  (assert (in MEMORY-HOME line) line))


(deftest test-a-memory-home-with-no-books-still-names-itself
  ;; 0 冊でも名乗る(黙る拍を作らない)。ここが黙ると「読んだ N / 書いた 0」の割れが
  ;; log から消え、今回と同じ無音の壊れ方に戻る。
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯ {composer}"])
  (<- row (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code" "config_dir" "/x/claude"}
                              :memory_dir MEMORY-HOME)))
  (setv lines (memory-log-lines world))
  (assert (= (len lines) 1) #("置き場を名乗った手番は 0 冊でも名乗る" world.log-lines))
  (assert (in "books=0" (get lines 0)) (get lines 0))
  (assert (in "declared=0" (get lines 0)) (get lines 0)))


(deftest test-a-seat-without-a-memory-home-says-nothing
  ;; 置き場を名乗らない手番の log は今日と 1 行も変わらない(記憶を使わない会話に行を足さない)。
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯ {composer}"])
  (<- row (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code" "config_dir" "/x/claude"})))
  (assert (= (memory-log-lines world) []) world.log-lines))


(deftest test-the-index-spelling-has-one-home
  ;; 計器が冊・索引・基準を分けて数えるための綴りは器の側にも写しが要る — 割れると
  ;; 予約名が冊として数えられ、「N 冊 → N + 2 file」の受入がずれる。基準の綴りが割れると
  ;; もっと静かに壊れる: 器が基準を**冊として**書き、絞りが効かず、基準が嘘をつく。
  (assert (= CLAUDE-MEMORY-INDEX-FILE MEMORY-INDEX-FILE))
  (assert (= CLAUDE-MEMORY-BASE-FILE MEMORY-BASE-FILE))
  (assert (= CLAUDE-MEMORY-FILE-SUFFIX MEMORY-FILE-SUFFIX))
  (assert (= CLAUDE-MEMORY-FILES-KEY CHARTER-MEMORY-FILES-KEY)))


;; ---------------------------------------------------------------------------
;; (7) 畳み戻しの基準も同じ名簿を渡り、**書けた冊の分だけ**が置き場に着く
;;     (card acp:kanban-issue:ki-9fc7d4bca4dc)
;; ---------------------------------------------------------------------------

(deftest test-the-fold-back-baseline-reaches-the-seat-in-the-same-roster-as-the-books
  ;; 基準が冊と**別の腕**で運ばれると、名簿のどれか 1 枚で落ちた拍に「冊は着いたが基準は無い」
  ;; (= 次の手番が 1 冊も書き戻さない)か「冊は落ちたが基準は着いた」(= 基準が嘘をつく)になる。
  ;; ⇒ 起こす側が組んだ本物の名簿(memory-files-of)を wire に通し、**本物の器**に書かせて数える。
  (setv books (tuple (lfor i (range 3)
                           (MemoryBook :name f"book-{i}"
                                       :text (+ "---\n" f"name: book-{i}\n" "description: d\n"
                                                "metadata:\n  type: project\n---\n\n" f"body {i}\n")
                                       :type "project" :description "d" :links #()))))
  (setv baselines (dfor book books book.name
                        (MemoryBaseline :name book.name :record-seq (+ 3 (len book.name))
                                        :sha256 (* "a" 64) :version 2)))
  (<- turn MemoryTurnFiles (memory-files-of books baselines #()))
  (setv files turn.files)
  (setv charter {"session_id" "s1" "session_name" "doeff-s1" "agent_type" "claude"
                 "work_dir" "/work/dir" "lifecycle" "run_to_completion"
                 "binding" {"kind" "claude-code" "config_dir" "/x/claude"}
                 "session_env" {} "prompt" "go" "mcp_servers" {}
                 "skip_trust_setup" False "command" None
                 CHARTER-MEMORY-DIR-KEY MEMORY-HOME
                 CHARTER-MEMORY-FILES-KEY (list files)})
  (setv program (build-launch-program-params charter (host-config)))
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯ {composer}"])
  (setv (get program "socket_path") "/tmp/agentd.sock")
  (<- row (run-launch world program))
  (setv written (books-in world MEMORY-HOME))
  (assert (= written (sorted ["book-0.md" "book-1.md" "book-2.md" MEMORY-INDEX-FILE MEMORY-BASE-FILE]))
          #("基準が名簿のどこかで落ちた" written))
  ;; 全冊が着いた拍の基準は 1 byte も変わらない(絞りが何も落とさない)。
  (<- landed dict (memory-baselines-of-text (get world.fs f"{MEMORY-HOME}/{MEMORY-BASE-FILE}")))
  (assert (= landed baselines) #("置き場の基準が起こす側の基準と違う" landed))
  ;; 器の名乗りは 冊・索引・基準を分けて数える。
  (setv line (get (memory-log-lines world) 0))
  (assert (in "books=3" line) line)
  (assert (in "index=1" line) line)
  (assert (in "base=1" line) line))


(deftest test-the-seat-writes-the-baseline-only-for-the-books-it-could-write
  ;; 器の書きの脚は名の門で落ちた entry を**黙って飛ばして**次へ進む(declared と written が割れる —
  ;; 計器に books= と declared= の 2 欄が在るのはこのため)。基準がそのまま着くと、飛ばされた冊 A について
  ;; 基準が『置き場の A は行と同じ姿』と嘘をつき、次の畳み戻しが**前の手番の古い写し**で行を supersede する
  ;; (= この便が直している壊れ方そのもの)。⇒ 基準の証拠は「用意した冊」ではなく「書けた冊」の側。
  (setv baseline-text (sample-baseline-text ["book-0" "book-1" "book-2"]))
  ;; book-1 は本文が str でない(行の本文が読めなかった体)⇒ 器の門が黙って飛ばす。
  (setv roster [{"name" "book-0.md" "text" "---\nname: book-0\ndescription: d\nmetadata:\n  type: project\n---\n\nbody 0\n"}
                {"name" "book-1.md" "text" None}
                {"name" "book-2.md" "text" "---\nname: book-2\ndescription: d\nmetadata:\n  type: project\n---\n\nbody 2\n"}
                {"name" MEMORY-INDEX-FILE "text" "# MEMORY\n"}
                {"name" MEMORY-BASE-FILE "text" baseline-text}])
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯ {composer}"])
  (<- row (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code" "config_dir" "/x/claude"}
                              :memory_dir MEMORY-HOME
                              :memory_files roster)))
  (setv written (books-in world MEMORY-HOME))
  (assert (= written (sorted ["book-0.md" "book-2.md" MEMORY-INDEX-FILE MEMORY-BASE-FILE]))
          #("門が落とした冊が置き場に在る / 基準が落ちた" written))
  ;; 基準は**書けた冊だけ**を名乗る(飛ばされた book-1 は基準から消える ⇒ 次の畳み戻しは規則 3b で撃たない)。
  (<- landed dict (memory-baselines-of-text (get world.fs f"{MEMORY-HOME}/{MEMORY-BASE-FILE}")))
  (assert (= (sorted (.keys landed)) ["book-0" "book-2"])
          #("書けなかった冊の基準が置き場に着いた(基準が嘘をつく)" (sorted (.keys landed))))
  ;; 残った項は起こす側が渡した値そのもの(器は絞るだけで、値を作らない)。
  (<- declared dict (memory-baselines-of-text baseline-text))
  (for [name ["book-0" "book-2"]]
    (assert (= (get landed name) (get declared name)) #(name (get landed name))))
  ;; 計器: 書けた冊 2・用意した 5(この割れが log から読める)。
  (setv line (get (memory-log-lines world) 0))
  (assert (in "books=2" line) line)
  (assert (in "declared=5" line) line)
  (assert (in "base=1" line) line))


;; ---------------------------------------------------------------------------
;; (8) 器は運ばれた名の file を取り除く — 名を組まず、行も読まない
;;     (card acp:kanban-issue:ki-6b5c4b270ca0)
;; ---------------------------------------------------------------------------

(deftest test-the-seat-removes-exactly-the-files-the-turn-named
  ;; 置き場はエージェントが触る面なので、退役した冊の file は『書き出さない』だけでは消えない。
  ;; ⇒ 起こす側が**退役した行と同じ名前**を荷に載せ、器がその file を落とす。器に在るのは
  ;; 「名指された 1 file を落とす」だけで、何を落とすかの判断は 1 つも無い。
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯ {composer}"])
  ;; 置き場には 3 file 在る: 退役した冊・残る冊・行をまだ持たない冊。
  (for [name ["gone.md" "kept.md" "brand-new.md"]]
    (setv (get world.fs f"{MEMORY-HOME}/{name}") "---\nname: x\ndescription: d\nmetadata:\n  type: project\n---\n\nbody\n"))
  (<- row (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code" "config_dir" "/x/claude"}
                              :memory_dir MEMORY-HOME
                              :memory_files (list (sample-books 0))
                              :memory_retired_files ["gone.md"])))
  (setv written (books-in world MEMORY-HOME))
  ;; 名指した 1 file だけが消え、名指していない file は 1 つも触られない。
  (assert (not-in "gone.md" written) #("名指した file が残っている" written))
  (assert (in "kept.md" written) #("名指していない file を消した" written))
  (assert (in "brand-new.md" written)
          #("行をまだ持たない冊を消した — 手番の途中に席が書いた記憶が失われる形" written))
  ;; 触った path も名指した 1 つだけ(痕跡で撃つ — 消しは書きと違って跡が残らない)。
  (setv removed (lfor #(verb path) world.trace :if (= verb "fs-remove") path))
  (assert (= removed [f"{MEMORY-HOME}/gone.md"]) #("名簿の外の path を消しに行った" removed))
  ;; 器の名乗りに消した数が出る(log だけで『掃除が落ちている』形を読めるようにする)。
  (setv line (get (memory-log-lines world) 0))
  (assert (in "swept=1" line) line)
  ;; 綴りの家は 2 つちょうど(acp/effects.py と器の写し)— 割れると黙って落ちる。
  (assert (= CLAUDE-MEMORY-RETIRED-FILES-KEY CHARTER-MEMORY-RETIRED-FILES-KEY)))


(deftest test-the-seat-never-builds-a-name-from-the-carried-roster
  ;; 器は運ばれた綴りを**そのまま**使う。置き場の外を指せる綴り(節・上の dir・隠し file)は
  ;; 落とさずに飛ばす — 名の門は起こす側(judgment.memory-name-of-row)に在るが、器の側も
  ;; 素通しにしない(2 重の門: 片方が腐っても置き場の外の file を消さない)。
  (setv world (LaunchWorld))
  (setv world.capture-script ["❯ {composer}"])
  (setv (get world.fs "/etc/passwd") "root")
  (setv (get world.fs f"{MEMORY-HOME}/.hidden") "x")
  (<- row (run-launch world (launch-params
                              :agent_type "claude"
                              :binding {"kind" "claude-code" "config_dir" "/x/claude"}
                              :memory_dir MEMORY-HOME
                              :memory_files (list (sample-books 0))
                              :memory_retired_files ["../../etc/passwd" ".hidden" "" "  " 7 None])))
  (setv removed (lfor #(verb path) world.trace :if (= verb "fs-remove") path))
  (assert (= removed []) #("置き場の外や隠し file を消しに行った" removed))
  (assert (in "/etc/passwd" world.fs) (sorted (.keys world.fs)))
  (assert (in f"{MEMORY-HOME}/.hidden" world.fs) (sorted (.keys world.fs)))
  (setv line (get (memory-log-lines world) 0))
  (assert (in "swept=0" line) line))
