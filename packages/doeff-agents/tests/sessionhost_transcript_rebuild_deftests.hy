;;; 会話の記録から Claude Code の transcript を組み直して `--resume` で続ける腕の焦点の検
;;; (card acp:kanban-issue:ki-c3aace97d825)。
;;;
;;; 背景の実測(~/experiments/agent-subtask-cost/out/turn-reopen-cold-cache/): 手番の 76〜88% が履歴の畳み直し
;;; (rehydrate)で始まり、隣り合う手番で共通の先頭は中央 2.9% しか無い。畳み直しは毎回ゼロから畳むので、
;;; 固定の前置き(共通の指示 + skill の一覧 + 基礎の system prompt ≈ 33,623 トークン)まで書き直しになる。
;;; ⇒ 同じ材料から**同じ並びの transcript**を組んで `--resume` で続ければ、先頭が byte 同一になり読みに変わる。
;;;
;;; ここで撃つのは:
;;;   * transcript の形(宣言した最小の必要集合 — 実射 2026-09-23 で `claude --resume` が通った 7 欄ちょうど)
;;;   * 同じ材料からは byte 同一の transcript(= 器の cache が読みになる条件そのもの)
;;;   * user と assistant が交互で、user から始まり assistant で終わる(器は他の並びを読まない)
;;;   * 親子の鎖・会話の id の検査が、壊れた行を起動の**前**に断る
;;;   * 並べ方と手番への割り方が rehydrate と同じ 1 点(history-groups-of)から来ている
;;;   * 旗を切る / 圧縮のための再開 / claude 以外の器 では組み直さない(= 今日どおり rehydrate)
;;;   * 本文の無い薄い再開(見出しだけ)は組み直さない
;;; HTTP も subprocess も無い(純関数だけ)。

(require doeff-hy.macros [deftest])

(import json)
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  ArmChoice
  HeadlineTurns
  MESSAGE-KIND
  NEXT-ARM-REBUILD
  NEXT-ARM-REHYDRATE
  NEXT-ARM-RESUME
  RecordEvent
  RecordedTurns
  TranscriptBuilt
  TranscriptRefused])
(import doeff_agents.sessionhost.acp.judgment [
  history-groups-of
  rebuild-arm-of
  transcript-jsonl-of
  transcript-lines-of
  transcript-readable-of
  transcript-session-id-of])
(import doeff_agents.sessionhost.policy [launch-conversation-plan-of])

(setv CONVERSATION "c-01ARZ3NDEKTSV4RRFFQ69G5FAV")
(setv OTHER "c-01ARZ3NDEKTSV4RRFFQ69G5FAW")
(setv AT 1789000000000)
(setv BUDGET 262144)


(defn #^ AcpRow message-row [#^ str message-id #^ str to #^ str sender #^ str body #^ int at]
  "契約 message の 1 通(配達済み)。"
  (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{MESSAGE-KIND}:{message-id}"
          :kind MESSAGE-KIND :resource-id message-id :version "v1" :generation 1 :created-at-ms at
          :labels {} :payload {}
          :spec {"id" message-id "to" to "from" sender "kind" "note" "items" [] "body" body "refs" []
                 "sha256" (* "0" 64) "at" at}
          :status {"state" "delivered"}))


(defn #^ RecordEvent event-of [#^ int record-seq #^ str stream #^ int seq #^ int at #^ str kind #^ dict fields]
  "service の出来事 1 つ(契約 storedEvent)。"
  (RecordEvent :record-seq record-seq :stream-id stream :stream-kind "turn" :producer-seq seq :at at :kind kind
               :bytes (.get fields "bytes" 1) :sha256 "0"
               :text (.get fields "text") :summary (.get fields "summary") :input (.get fields "input")
               :output (.get fields "output") :tool-name (.get fields "toolName") :tool-use-id (.get fields "toolUseId")
               :model (.get fields "model") :is-error (= (.get fields "isError") True)
               :truncated (= (.get fields "truncated") True)))


(defn sample-material []
  "2 手番ぶんの材料(郵便 2 通 + 出来事 4 つ)。"
  #((message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" AT)
    (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ AT 9000)))
  )


(defn sample-events []
  #((event-of 1 "j-1#a1" 0 (+ AT 1000) "text" {"text" "覚えました" "model" "claude-opus-5"})
    (event-of 2 "j-1#a1" 1 (+ AT 1100) "tool_use" {"toolName" "Read" "toolUseId" "t1" "input" {"file_path" "a.txt"}})
    (event-of 3 "j-1#a1" 2 (+ AT 1200) "tool_result" {"toolUseId" "t1" "output" "中身"})
    (event-of 7 "j-2#a1" 0 (+ AT 9500) "text" {"text" "ひまわり です"})))


(defn build [[budget BUDGET] [exclude #()]]
  "材料 → 組み直した transcript(呼びの並びは agentd.transcript-of と同じ)。"
  (setv messages (sample-material))
  (setv source (RecordedTurns :events (sample-events) :complete True))
  (setv grouped (run (history-groups-of CONVERSATION messages source exclude {} None 65536)))
  (setv session-id (run (transcript-session-id-of CONVERSATION 7)))
  (run (transcript-lines-of CONVERSATION session-id source (get grouped 0) #() budget)))


(deftest test-rebuilt-transcript-has-the-declared-minimal-shape
  ;; 宣言した最小の必要集合ちょうど(実射 2026-09-23: この 7 欄だけの transcript を家に置いて
  ;; 器を `--resume <sid>` で起こすと通り、model が組んだ履歴の中身を答えた)。欄を足す / 減らす便は
  ;; ここが赤くなる —— 器の内部仕様には契約が無いので、宣言はこちらが持つ。
  (setv built (build))
  (assert (isinstance built TranscriptBuilt) built)
  (setv user-keys #{"parentUuid" "isSidechain" "type" "uuid" "timestamp" "sessionId" "message"})
  (for [line built.lines]
    (assert (= (set (.keys line)) user-keys) line)
    (assert (in (get line "type") #{"user" "assistant"}) line)
    (assert (= (get line "sessionId") built.session-id) line)
    (assert (.endswith (get line "timestamp") "Z") line)
    (assert (is (get line "isSidechain") False) line))
  ;; user の本文は文字列ちょうど / assistant は text の block の列 + 器が自分で作った印。
  (setv users (lfor line built.lines :if (= (get line "type") "user") line))
  (setv assistants (lfor line built.lines :if (= (get line "type") "assistant") line))
  (assert users)
  (assert assistants)
  (for [line users]
    (setv message (get line "message"))
    (assert (= (set (.keys message)) #{"role" "content"}) message)
    (assert (= (get message "role") "user") message)
    (assert (isinstance (get message "content") str) message))
  (for [line assistants]
    (setv message (get line "message"))
    (assert (= (set (.keys message))
               #{"id" "type" "role" "model" "content" "stop_reason" "stop_sequence" "usage"}) message)
    (assert (= (get message "role") "assistant") message)
    (assert (= (get message "model") "<synthetic>") message)
    (assert (= (lfor block (get message "content") (get block "type")) ["text"]) message)))


(deftest test-rebuilt-transcript-alternates-and-chains
  ;; 器は user と assistant が交互に並ぶ形しか読まない。親子の鎖は最初の行の親が無し・以降は直前の行の uuid。
  ;; 最後は assistant ―― 器は次の本文を user の行として積むので、user で終わると user が 2 つ続く。
  (setv built (build))
  (assert (= (get (get built.lines 0) "parentUuid") None))
  (assert (= (get (get built.lines 0) "type") "user"))
  (assert (= (get (get built.lines -1) "type") "assistant"))
  (setv previous None)
  (setv expected "user")
  (for [line built.lines]
    (assert (= (get line "parentUuid") previous) line)
    (assert (= (get line "type") expected) line)
    (setv previous (get line "uuid"))
    (setv expected (if (= expected "user") "assistant" "user")))
  ;; 会話の中身は落ちていない(郵便も出来事も本文のどこかに在る)。
  (setv whole (run (transcript-jsonl-of built.lines)))
  (for [needle ["合言葉は ひまわり" "覚えました" "中身" "ひまわり です" "合言葉は何でしたか"]]
    (assert (in needle whole) needle)))


(deftest test-rebuilt-transcript-is-byte-identical-for-the-same-material
  ;; **この便の目的そのもの**: 同じ材料からは同じ transcript(uuid も timestamp も材料から導く)。
  ;; 実射 2026-09-23: 同じ中身を別の会話 id で 2 度置くと、2 度目は読み 66,655 / 書き 0 で先頭が丸ごと
  ;; cache から読まれた。決定的でなければこの効き目は 1 byte も出ない。
  (setv first (build))
  (setv second (build))
  (assert (= (run (transcript-jsonl-of first.lines)) (run (transcript-jsonl-of second.lines))))
  (assert (= first.session-id second.session-id))
  ;; 会話の id は「会話 + その拍の記録の頭」から導く — 記録が進めば別の id(前の組み直しの file を上書きしない)。
  (assert (!= (run (transcript-session-id-of CONVERSATION 7)) (run (transcript-session-id-of CONVERSATION 8))))
  (assert (!= (run (transcript-session-id-of CONVERSATION 7)) (run (transcript-session-id-of OTHER 7)))))


(deftest test-rebuilt-transcript-grows-only-at-the-tail
  ;; 手番が 1 つ増えても**前の手番までの行は 1 byte も変わらない**(= 器の cache の先頭が保たれる)。
  ;; 履歴の畳み直し(rehydrate)はここが保てない(実測: 隣り合う手番の共通の先頭が中央 2.9%)。
  (setv messages (sample-material))
  (setv source (RecordedTurns :events (sample-events) :complete True))
  (setv grown-events (+ (sample-events)
                        #((event-of 8 "j-2#a1" 1 (+ AT 9600) "text" {"text" "追記の手番"}))))
  (setv grown (RecordedTurns :events grown-events :complete True))
  (setv sid (run (transcript-session-id-of CONVERSATION 7)))
  (setv before (run (transcript-lines-of CONVERSATION sid source
                                         (get (run (history-groups-of CONVERSATION messages source #() {} None 65536)) 0)
                                         #() BUDGET)))
  (setv after (run (transcript-lines-of CONVERSATION sid grown
                                        (get (run (history-groups-of CONVERSATION messages grown #() {} None 65536)) 0)
                                        #() BUDGET)))
  (assert (isinstance before TranscriptBuilt) before)
  (assert (isinstance after TranscriptBuilt) after)
  (setv head (run (transcript-jsonl-of (cut before.lines 0 (- (len before.lines) 1)))))
  (assert (.startswith (run (transcript-jsonl-of after.lines)) head)
          #((run (transcript-jsonl-of after.lines)) head)))


(deftest test-readability-check-refuses-broken-lines-before-launch
  ;; 起動の前の 1 回の検査(実 CLI の『No conversation found』で 120 秒かけて死ぬ形にしない)。
  ;; 鎖が切れた / 並びが崩れた / 会話の id が揃わない —— どれも typed な断りで、呼び手は rehydrate に戻る。
  (setv built (build))
  (assert (isinstance (run (transcript-readable-of built)) TranscriptBuilt))
  ;; 鎖を切る
  (setv broken (list (lfor line built.lines (dict line))))
  (setv (get (get broken 1) "parentUuid") "00000000-0000-0000-0000-000000000000")
  (setv verdict (run (transcript-readable-of (TranscriptBuilt :session-id built.session-id :lines (tuple broken)
                                                              :turns built.turns :dropped 0 :cut-bytes 0 :size-bytes 0))))
  (assert (isinstance verdict TranscriptRefused) verdict)
  (assert (= verdict.reason "broken-chain") verdict)
  ;; 並びを崩す(user が 2 つ続く)
  (setv disordered (list (lfor line built.lines (dict line))))
  (setv (get (get disordered 1) "type") "user")
  (setv (get (get disordered 1) "message") {"role" "user" "content" "x"})
  (setv verdict (run (transcript-readable-of (TranscriptBuilt :session-id built.session-id :lines (tuple disordered)
                                                              :turns built.turns :dropped 0 :cut-bytes 0 :size-bytes 0))))
  (assert (isinstance verdict TranscriptRefused) verdict)
  (assert (= verdict.reason "role-disorder") verdict)
  ;; 会話の id が揃わない
  (setv mismatched (list (lfor line built.lines (dict line))))
  (setv (get (get mismatched -1) "sessionId") "別の会話")
  (setv verdict (run (transcript-readable-of (TranscriptBuilt :session-id built.session-id :lines (tuple mismatched)
                                                             :turns built.turns :dropped 0 :cut-bytes 0 :size-bytes 0))))
  (assert (isinstance verdict TranscriptRefused) verdict)
  (assert (= verdict.reason "session-mismatch") verdict)
  ;; 1 行も無い
  (setv verdict (run (transcript-readable-of (TranscriptBuilt :session-id built.session-id :lines #()
                                                             :turns 0 :dropped 0 :cut-bytes 0 :size-bytes 0))))
  (assert (= verdict.reason "empty-lines") verdict))


(deftest test-thin-record-and-empty-history-are-refused
  ;; 記録の service に届かず見出しだけ(本文なし)の材料は transcript にしない —— 見出しを本文のふりで積むと、
  ;; 器の履歴に『言っていないこと』が入る。履歴がまるごと無い会話も同じく組み直さない。
  (setv thin (HeadlineTurns :records #() :reason "record service read failed"))
  (setv verdict (run (transcript-lines-of CONVERSATION "s-1" thin [] #() BUDGET)))
  (assert (isinstance verdict TranscriptRefused) verdict)
  (assert (= verdict.reason "thin-record") verdict)
  (setv empty (RecordedTurns :events #() :complete True))
  (setv verdict (run (transcript-lines-of CONVERSATION "s-1" empty [] #() BUDGET)))
  (assert (isinstance verdict TranscriptRefused) verdict)
  (assert (= verdict.reason "no-history") verdict))


(deftest test-budget-drops-the-oldest-turns-and-says-so
  ;; 上限は古い手番から落として満たし(最新の 1 手番は残す)、落とした区間は最初の user の行の見出しが名乗る。
  (setv built (build :budget 400))
  (assert (isinstance built TranscriptBuilt) built)
  (assert (> built.dropped 0) built)
  (assert (= built.turns 1) built)
  (setv head (get (get (get built.lines 0) "message") "content"))
  (assert (in "上限" head) head)
  ;; 落とした後も器が読める形のまま。
  (assert (isinstance (run (transcript-readable-of built)) TranscriptBuilt)))


(deftest test-rebuild-arm-is-taken-only-when-the-flag-and-the-arm-and-the-kind-agree
  ;; 解くのは 3 つが揃った時だけ。圧縮のための再開(compacts)は解かない —— あれは『温かい cache を捨てて
  ;; 縮めて始める』が目的なので、履歴をそのまま transcript に積むと圧縮にならない。
  (setv on (AgentdSettings :node-name "n" :transcript-rebuild-enabled True))
  (setv off (AgentdSettings :node-name "n" :transcript-rebuild-enabled False))
  (setv rehydrate (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire "s-1"))
  (assert (= (. (run (rebuild-arm-of rehydrate on "claude")) arm) NEXT-ARM-REBUILD))
  ;; 旗を切れば今日どおり(戻し方 = この 1 つ)
  (assert (= (. (run (rebuild-arm-of rehydrate off "claude")) arm) NEXT-ARM-REHYDRATE))
  ;; 器が claude でなければ組み直さない(transcript の物理は claude の家のもの)
  (assert (= (. (run (rebuild-arm-of rehydrate on "codex")) arm) NEXT-ARM-REHYDRATE))
  ;; 圧縮のための再開は解かない
  (setv compacting (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire "s-1" :compacts True))
  (assert (= (. (run (rebuild-arm-of compacting on "claude")) arm) NEXT-ARM-REHYDRATE))
  ;; 他の腕はそのまま(温かい session を組み直しで置き換えない)
  (setv resuming (ArmChoice :arm NEXT-ARM-RESUME :source "s-1" :retire "s-1"))
  (assert (= (. (run (rebuild-arm-of resuming on "claude")) arm) NEXT-ARM-RESUME))
  ;; 片付ける session の名指しは腕を解いても残る(家の違う温かい器を 2 つ生かさない)
  (assert (= (. (run (rebuild-arm-of rehydrate on "claude")) retire) "s-1")))


(deftest test-one-oversized-turn-is-cut-at-the-head-so-the-context-cannot-grow
  ;; 手番を 1 つに落としてもまだ超える形(= 1 手番が巨大な会話)では、その塊の先頭を切って末尾を残し、
  ;; 切った byte を名乗る。⚠ この栓が無いと、組み直しは畳み直しが持っていた「文脈を切る」役を失い、
  ;; 器が読み直す文脈が膨らんで書き直しの節約を相殺する(費用の実測 kanban-worth/cost)。
  (setv source (RecordedTurns :events #((event-of 1 "j-1#a1" 0 AT "text" {"text" (* "あ" 20000)}))
                              :complete True))
  (setv grouped (run (history-groups-of CONVERSATION #() source #() {} None 65536)))
  (setv built (run (transcript-lines-of CONVERSATION "s-1" source (get grouped 0) #() 4000)))
  (assert (isinstance built TranscriptBuilt) built)
  (assert (> built.cut-bytes 0) built)
  (assert (<= built.size-bytes (* 4000 2)) built.size-bytes)
  (assert (isinstance (run (transcript-readable-of built)) TranscriptBuilt))
  ;; 切った時も本文のどこかに「切った」ことが書いてある(黙って捨てない)。
  (setv whole (run (transcript-jsonl-of built.lines)))
  (assert (in "上限" whole) whole))


(deftest test-an-adopted-transcript-is-resumed-so-the-cold-compaction-runs-first
  ;; operator 決定 2026-09-23: account / profile / model / 機体 の変更が避けられない手番では、最初の model の
  ;; 呼び出しの**前に**必ず圧縮を走らせる(cache はどのみち書き直されるので、そこで文脈を小さくするのが最も安い)。
  ;; 組み直しの腕はまさにその手番なので、起こし方が "resume" でなければならない —— headless.hy はこの値ちょうどを
  ;; 見て起動前の `/compact fast-jev-if-cold` を撃つ(headless-cold-compaction-run の門)。
  ;; さらに、組み直しは**新しい会話の id**を鋳造する(会話 + 記録の頭から導く)ので、圧縮の plugin から見て
  ;; その session の温かい記録は構造的に存在しない = 必ず「冷えた」と判定される。
  (setv minted {"session_id" "11111111-2222-3333-4444-555555555555"})
  (setv plan (run (launch-conversation-plan-of None minted True "claude" False)))
  (assert (= (get plan "mode") "resume") plan)
  (assert (= (get plan "conversation") minted) plan)
  (assert (= (get plan "row_conversation") minted) plan)
  ;; 継いでいない手番は今日どおり(新しい会話 = --session-id・起動前の圧縮は撃たない — 温かい cache が無いので不要)。
  (setv fresh (run (launch-conversation-plan-of None minted False "claude" False)))
  (assert (= (get fresh "mode") None) fresh)
  (assert (= (get fresh "conversation") minted) fresh)
  ;; 蘇生の腕(session.resume / fork)は今日どおり親会話。
  (setv parent {"session_id" "99999999-2222-3333-4444-555555555555"})
  (setv resumed (run (launch-conversation-plan-of {"mode" "resume" "conversation" parent} minted True "claude" False)))
  (assert (= (get resumed "conversation") parent) resumed)
  (setv forked (run (launch-conversation-plan-of {"mode" "fork" "conversation" parent} minted True "claude" False)))
  (assert (= (get forked "row_conversation") None) forked))


(deftest test-this-turn-inputs-are-not-folded-into-the-transcript
  ;; この手番の郵便(inputs)は本文として別に届くので transcript には畳まない —— 畳むと同じ本文が 2 度届く。
  (setv built (build :exclude #("m-2")))
  (assert (isinstance built TranscriptBuilt) built)
  (setv whole (run (transcript-jsonl-of built.lines)))
  (assert (in "合言葉は ひまわり" whole))
  (assert (not-in "合言葉は何でしたか" whole) whole))
