;;; 会話の自動記憶の正本の座は機体の file system ではなく行(card acp:kanban-issue:ki-9fc7d4bca4dc・
;;; 法 ACP 575b1e conversation-memory-lives-in-the-row)の焦点の検。
;;;
;;; 直す欠陥(2026-09-20 に生きた pool pod と会社 Mac を突き合わせて測定): ADR-DOE-AGENTS-006 R11 は
;;; 置き場の割れを機体の**中**で直し、最後の 1 文で「機体をまたぐ継続は本 R の範囲外 — 別に立てる」と
;;; 空けた。記憶の根は依然その機体の disk で、pod の家は emptyDir なので世代交代で丸ごと消える:
;;;   * pod の 52 冊のうち 46 冊(76,106 byte)がどの Mac にも無い
;;;   * 名前の一致した 6 組は 6 組とも中身が違い、後に書かれた方が 5 組で平均 24% 薄い
;;;     ⇒ 失われ方は 2 通り(消える / 薄い版が濃い版を上書きする)
;;;   * 索引 MEMORY.md は本 18 冊に対し 15 行に腐っていた
;;;
;;; ここで撃つのは:
;;;   * 冊の読み(frontmatter)と、読めない形を**書かない**こと(欄を発明して行を作らない)
;;;   * 撃ち分けの 1 規則(法 575b1e): 行の recordSeq の在否 — 版を数えて選ばない
;;;   * 2 台目が同じ名前へ書いても **前の版が消えない**(supersede の鎖・素の再 append は保存済みが勝つ)
;;;   * 索引は行から導く(行数 == 冊の数)
;;;   * 退役した冊は置き場に file が残っていても書き戻さない(退役が取り消されない)
;;;   * 水入れと畳み戻しが**対で**効く(2 手番目の頭に 1 手番目の本が載る)
;;; fake の handler で同じ腕(agentd.hy)を走らせる。HTTP も subprocess も無い。

(require doeff-hy.macros [deftest <-])

(import json)
(import dataclasses [replace])
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  CHARTER-MEMORY-FILES-KEY
  CONDITION-MEMORY-UNWRITABLE
  MEMORY-INDEX-FILE
  MEMORY-KIND
  MEMORY-SPEC-RECORD-SEQ-KEY
  MEMORY-SPEC-SHA256-KEY
  MEMORY-SPEC-VERSION-KEY
  MESSAGE-KIND
  NODE-KIND
  PHASE-BOUND
  RECORD-STREAM-MEMORY
  TURN-RECORD-KIND
  MemoryAppend
  MemoryBook
  MemoryMalformed
  MemorySupersede
  MemoryUnchanged])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeRecord FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  memory-book-of
  memory-files-of
  memory-index-of
  memory-row-retired?
  memory-rows-by-name
  memory-spec-of
  memory-stream-id-of
  memory-write-verdict])
(import doeff_agents.sessionhost.acp.runtime [initial-state run-tick])


(setv NODE "CA-20038667")
(setv CID "c-01M28FFPKA9NDM1WCASFVC63W1")
(setv MEMORY-ROOT "/state/doeff/agent-memory")
(setv HOME f"{MEMORY-ROOT}/{CID}")
(setv NOW 1789200000000)


(defn #^ str book-text [#^ str name #^ str description #^ str body]
  (+ "---\n"
     f"name: {name}\n"
     f"description: {description}\n"
     "metadata:\n"
     "  type: project\n"
     "---\n"
     "\n"
     body
     "\n"))


(defn #^ str stream-line [#^ dict record]
  (+ (json.dumps record) "\n"))


(defn #^ str claude-events [#^ str session-id #^ str text]
  "claude の print mode(stream-json)の 1 手番の行(init・本文・result)。"
  (setv usage {"input_tokens" 3 "output_tokens" 7 "cache_creation_input_tokens" 1 "cache_read_input_tokens" 2})
  (.join "" [(stream-line {"type" "system" "subtype" "init" "session_id" session-id "model" "claude-opus-5"
                           "permissionMode" "bypassPermissions" "cwd" "/work" "tools" ["Bash"]})
             (stream-line {"type" "assistant"
                           "message" {"id" "msg_1" "role" "assistant" "model" "claude-opus-5"
                                      "content" [{"type" "text" "text" text}] "usage" usage}})
             (stream-line {"type" "result" "subtype" "success" "is_error" False "usage" usage})]))


(defn #^ AcpRow row-of [#^ str namespace #^ str kind #^ str resource-id #^ dict spec #^ (| dict None) status]
  (AcpRow :namespace namespace :key f"{namespace}:{kind}:{resource-id}" :kind kind :resource-id resource-id
          :version "v1" :generation 1 :created-at-ms 500 :labels {} :payload {} :spec spec :status status))


(defn #^ AcpRow bound-job [#^ str job-id #^ list inputs]
  (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND job-id
          {"subject" CID "inputs" inputs
           "charter" {"session_id" f"charter-{job-id}" "session_name" f"charter-{job-id}"
                      "agent_type" "claude" "work_dir" "/work" "prompt" "start" "model" "claude-opus-5"}}
          {"phase" PHASE-BOUND "binding" {"node" NODE "profile" "ca" "account" "acct"} "conditions" []}))


(defclass MemoryWorld []
  "backend = headless の器で agentd を**一周**させる世界。記憶の腕は手番の頭(incarnate)と終い
   (settle-record)に在るので、腕を直に呼ばず run-tick 越しに動かす(この族の 9 file と同じ作法 —
   agentd.hy は agentd-runtime だけが読む module)。"

  (defn #^ None __init__ [self]
    (setv self.settings (replace (AgentdSettings :node-name NODE :homes-root "/homes"
                                                 :backend-kind "headless" :stream-capability "events"
                                                 :record-enabled True :node-capacity 1)
                                 :memory-root MEMORY-ROOT))
    (setv self.acp (FakeAcp :births {TURN-RECORD-KIND (Birth "state" "running")}))
    (.put-row self.acp (row-of AGORA-KINDS-NAMESPACE NODE-KIND NODE
                               {"name" NODE "labels" {} "capacity" 1 "streamCapability" "events"}
                               {"state" "joined"}))
    (setv self.custody (FakeCustody :tokens {"acct" "sk-ant-oat01-secret"}))
    (setv self.sessions (FakeSessions :agent-type "claude" :backend-kind "headless" :events-root "/events"))
    (setv self.local (FakeLocal :now-ms NOW))
    (setv self.record (FakeRecord))
    (setv self.state (initial-state))
    (setv self.turns 0))

  (defn #^ list dispatchers [self]
    [self.record.dispatch self.acp.dispatch self.custody.dispatch self.sessions.dispatch self.local.dispatch])

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state (run-tick self.settings self.state (.dispatchers self)))
    None)

  (defn #^ None put-file [self #^ str name #^ str text]
    "置き場に file を 1 つ置く(器が書いた体)。"
    (setv (get self.local.files f"{HOME}/{name}") text)
    None)

  (defn #^ str job-id [self]
    f"aj-{self.turns}")

  (defn #^ str sid [self]
    (setv status (. (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{(.job-id self)}") status))
    (assert (isinstance status dict))
    (setv handle (get status "sessionHandle"))
    (assert (isinstance handle dict))
    (setv session-id (get handle "sessionId"))
    (assert (isinstance session-id str))
    session-id)

  (defn #^ None turn [self]
    "1 手番を頭から終いまで(水入れ = incarnate → 本文 → 畳み戻し = settle-record)。

     手番のたびに器の session を落とす — この便の世界では次の手番がどの機体に落ちるか分からず、
     器は毎回作り直される(pod の世代交代・機体の移動)。生きた session が残っていると 2 手番目は
     送りになって水入れの腕(incarnate)を通らないので、検が水入れを 1 度しか撃てない。"
    (.clear self.sessions.views)
    (setv self.turns (+ self.turns 1))
    (setv job (.job-id self))
    (.put-row self.acp (row-of AGORA-KINDS-NAMESPACE MESSAGE-KIND f"m-{self.turns}"
                               {"id" f"m-{self.turns}" "body" "first"} {"state" "inbox"}))
    (.put-row self.acp (bound-job job [f"m-{self.turns}"]))
    (.tick self 0)
    (setv (get self.local.transcripts f"/events/{(.sid self)}.events.jsonl") (claude-events (.sid self) "hello"))
    (.tick self 1000)
    (.finish-turn self.sessions (.sid self) (+ self.local.now-ms 100))
    (.tick self 1000)
    None)

  (defn #^ dict charter [self]
    "この手番で器へ渡した charter(水入れが載せた memory_files はここに在る)。"
    (get self.sessions.launches -1))

  (defn #^ tuple hydrated [self]
    (tuple (.get (.charter self) CHARTER-MEMORY-FILES-KEY #())))

  (defn #^ list job-conditions [self]
    (setv status (. (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{(.job-id self)}") status))
    (assert (isinstance status dict))
    (list (.get status "conditions" [])))

  (defn #^ list memory-appends [self]
    "記録の service へ撃った追記のうち**記憶の stream のものだけ**(手番ごとの turn-record を数に混ぜない)。"
    (lfor batch self.record.appends :if (= batch.stream.kind RECORD-STREAM-MEMORY) batch))

  (defn #^ tuple memory-rows [self]
    (tuple (gfor row (.values self.acp.rows) :if (= row.kind MEMORY-KIND) row))))


(deftest test-a-book-is-read-from-its-frontmatter-and-an-unreadable-file-is-not-written
  ;; 読めない形は書かずに理由を名乗る — 欄を発明して行を作らない(契約の required を満たさない行は engine に断られる)。
  (<- reading (| MemoryBook MemoryMalformed)
      (memory-book-of "co-mac-is-a-worker.md"
                      (book-text "co-mac-is-a-worker" "会社 Mac は退役させない" "worker として寄与し続ける。[[seat-is-a-vessel]] も見る。")))
  (assert (isinstance reading MemoryBook) reading)
  (assert (= reading.name "co-mac-is-a-worker") reading)
  (assert (= reading.type "project") reading)
  (assert (= reading.description "会社 Mac は退役させない") reading)
  (assert (= reading.links #("seat-is-a-vessel")) reading)
  ;; 名は **file 名**が正本(frontmatter と食い違っても file 名を採る — 行の identityKey がそれを写す)。
  (<- renamed (| MemoryBook MemoryMalformed) (memory-book-of "other-name.md" (book-text "co-mac-is-a-worker" "要旨" "本文")))
  (assert (= renamed.name "other-name") renamed)
  ;; 索引そのもの・接尾辞違い・綴れない名・frontmatter 無し・閉じない・要旨無し・語彙外の種類は書かない。
  (for [#(name text) [#(MEMORY-INDEX-FILE "- [a](a.md) — x")
                      #("notes.txt" (book-text "notes" "要旨" "本文"))
                      #("Upper.md" (book-text "Upper" "要旨" "本文"))
                      #("plain.md" "frontmatter がありません")
                      #("open.md" "---\nname: open\ndescription: 要旨\n")
                      #("no-desc.md" "---\nname: no-desc\nmetadata:\n  type: project\n---\n本文")
                      #("bad-type.md" "---\nname: bad-type\ndescription: 要旨\nmetadata:\n  type: diary\n---\n本文")]]
    (<- bad (| MemoryBook MemoryMalformed) (memory-book-of name text))
    (assert (isinstance bad MemoryMalformed) #(name bad))
    (assert (.strip bad.reason) bad)))


(deftest test-the-write-verdict-turns-on-whether-the-row-has-a-record-seq
  ;; 法 575b1e: 撃ち分けの鍵は**行の recordSeq の在否**の 1 規則。版を数えて選ばない。
  (setv digest (* "a" 64))
  (<- empty (| MemoryUnchanged MemoryAppend MemorySupersede) (memory-write-verdict None digest))
  (assert (isinstance empty MemoryAppend) "行が無ければ append")
  (setv row (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{MEMORY-KIND}:mem-1"
                    :kind MEMORY-KIND :resource-id "mem-1" :version 1 :generation 1 :created-at-ms NOW
                    :labels {} :payload {}
                    :spec {"conversationId" CID "name" "a" MEMORY-SPEC-SHA256-KEY digest
                           MEMORY-SPEC-RECORD-SEQ-KEY 7 MEMORY-SPEC-VERSION-KEY 2}
                    :status {"state" "current"}))
  (<- same (| MemoryUnchanged MemoryAppend MemorySupersede) (memory-write-verdict row digest))
  (assert (isinstance same MemoryUnchanged) "同じ本文には 1 bit も撃たない")
  (<- verdict (| MemoryUnchanged MemoryAppend MemorySupersede) (memory-write-verdict row (* "b" 64)))
  (assert (isinstance verdict MemorySupersede) verdict)
  (assert (= verdict.record-seq 7) verdict)
  (assert (= verdict.version 2) verdict)
  ;; 行は在るが recordSeq が読めない = append に倒す(409 が『行が消えて stream が残っている』の合図になる)。
  (setv broken (replace row :spec {"conversationId" CID "name" "a" MEMORY-SPEC-SHA256-KEY (* "c" 64)}))
  (<- fallback (| MemoryUnchanged MemoryAppend MemorySupersede) (memory-write-verdict broken (* "b" 64)))
  (assert (isinstance fallback MemoryAppend) "recordSeq の無い行は append"))


(deftest test-the-index-is-derived-from-the-rows-and-counts-every-book
  ;; 受入 4: MEMORY.md の項の数 == その会話の current の冊の数(索引の腐りが構造的に起きない)。
  (setv books (tuple (lfor n ["zeta" "alpha" "mid"] (MemoryBook :name n :text "x" :type "project"
                                                                :description f"{n} の要旨" :links #()))))
  (<- index str (memory-index-of books))
  (setv items (lfor line (.splitlines index) :if (.startswith line "- [") line))
  (assert (= (len items) (len books)) index)
  ;; 名の順(file の並びと同じ)で、各項が冊の名と要旨を運ぶ。
  (assert (= items [f"- [alpha](alpha.md) — alpha の要旨"
                    f"- [mid](mid.md) — mid の要旨"
                    f"- [zeta](zeta.md) — zeta の要旨"]) items)
  ;; 書き出す列は 冊 + 索引 で、索引は最後の 1 つ。
  (<- files tuple (memory-files-of books))
  (assert (= (len files) (+ (len books) 1)) files)
  (assert (= (get (get files -1) "name") MEMORY-INDEX-FILE) files)
  ;; 冊が 0 でも索引は書く(前の手番の腐った索引を残さない)。
  (<- empty tuple (memory-files-of #()))
  (assert (= (len empty) 1) empty)
  (assert (= (get (get empty 0) "name") MEMORY-INDEX-FILE) empty))


(deftest test-a-first-write-lands-in-the-record-and-the-row-carries-only-the-claim-check
  (setv world (MemoryWorld))
  (.put-file world "co-mac-is-a-worker.md" (book-text "co-mac-is-a-worker" "会社 Mac は退役させない" "worker として寄与し続ける。"))
  (.turn world)
  (assert (= (.job-conditions world) []) (.job-conditions world))
  (setv rows (.memory-rows world))
  (assert (= (len rows) 1) rows)
  (setv spec (. (get rows 0) spec))
  ;; 行は claim check と索引の材料ちょうど — **本文の欄は 1 つも無い**(法 575b1e)。
  (for [field ["recordRef" "recordSeq" "bytes" "sha256" "version"]]
    (assert (in field spec) #(field spec)))
  (for [spelling ["text" "body" "content" "markdown" "data"]]
    (assert (not (in spelling spec)) #(spelling spec)))
  (assert (= (get spec "version") 1) spec)
  ;; 本文は記録の service の stream(memory#<name>)に 1 つ。
  (setv stream-id (run (memory-stream-id-of "co-mac-is-a-worker")))
  (setv events (.events-of world.record CID stream-id))
  (assert (= (len events) 1) events)
  (assert (= (get (get events 0) "kind") "memory") events)
  (assert (in "worker として寄与し続ける" (get (get events 0) "text")) events)
  ;; 次の手番でも本文が同じなら 1 bit も撃たない(冪等)。
  (setv appends (len (.memory-appends world)))
  (.turn world)
  (assert (= (len (.memory-appends world)) appends) "同じ本文で追記を撃っている")
  (assert (= world.record.supersedes []) "同じ本文で置き換えを撃っている"))


(deftest test-a-second-write-supersedes-and-the-earlier-body-is-not-destroyed
  ;; 実測の欠陥(後に書かれた方が 5 組で平均 24% 薄い)がここで閉じる: 上書きではなく版を足す。
  (setv world (MemoryWorld))
  (setv rich (book-text "co-mac-is-a-worker" "会社 Mac は退役させない"
                        "系の頭脳を k3s へ移すのであって、Mac は worker(手番の実行)として寄与し続ける。"))
  (.put-file world "co-mac-is-a-worker.md" rich)
  (.turn world)
  ;; 別の機体の手番が同じ名前へ薄い本を書いた(置き場の file が入れ替わった体)。
  (setv thin (book-text "co-mac-is-a-worker" "会社 Mac は worker" "Mac は worker。"))
  (.put-file world "co-mac-is-a-worker.md" thin)
  (.turn world)
  (assert (= (.job-conditions world) []) (.job-conditions world))
  ;; 置き換えを 1 回撃った(素の再 append ではない — それは保存済みが勝って新しい本文が落ちる)。
  (assert (= (len world.record.supersedes) 1) world.record.supersedes)
  ;; 行は最後の版を指す(version が 1 つ進む)。
  (setv spec (. (get (.memory-rows world) 0) spec))
  (assert (= (get spec "version") 2) spec)
  ;; ⇒ **前の版は消えていない**(tombstone を撃たない限り鎖として在る)。
  (setv kept (get (list (.values world.record.superseded)) 0))
  (assert (= (len kept) 1) kept)
  (assert (in "k3s へ移すのであって" (get (get kept 0) "text")) kept))


(deftest test-a-retired-memory-is-not-written-back-from-a-file-left-in-the-home
  ;; 置き場の file は水入れが消さないので、退役した冊の file は残る。書き戻すと退役が次の手番で取り消される。
  (setv world (MemoryWorld))
  (.put-file world "stale.md" (book-text "stale" "もう要らない" "古い事実。"))
  (.turn world)
  (setv row (get (.memory-rows world) 0))
  (assert (not (run (memory-row-retired? row))) row)
  ;; operator が退役させた(spec はそのまま・status.state だけ retired へ)。
  (setv retired (replace row :status {"state" "retired"}))
  (setv (get world.acp.rows retired.key) retired)
  (assert (run (memory-row-retired? retired)) retired)
  ;; file を書き換えて手番が終わっても、退役した冊は 1 bit も撃たれない。
  (.put-file world "stale.md" (book-text "stale" "もう要らない" "書き換えた本文。"))
  (setv appends (len (.memory-appends world)))
  (.turn world)
  (assert (= (len (.memory-appends world)) appends) "退役した冊を追記している")
  (assert (= world.record.supersedes []) "退役した冊を置き換えている")
  (assert (= (. (get (.memory-rows world) 0) status) {"state" "retired"})
          "退役した行の status が書き換わった")
  ;; 退役した行は手番の頭にも載らない(索引だけ)。
  (setv files (.hydrated world))
  (assert (= (lfor f files (get f "name")) [MEMORY-INDEX-FILE]) files))


(deftest test-the-next-turn-starts-with-the-book-the-previous-turn-wrote
  ;; 受入 2: 同じ会話が 2 つの機体で続けて手番を持っても、2 手番目の頭に 1 手番目の本が載る
  ;; (置き場は空から始まる = pod の世代交代 / 機体の移動)。
  (setv world (MemoryWorld))
  (setv text (book-text "voice-origin-tag" "先頭が (voice) の指示は読み上げ向けに答える" "平易文体を既定にする。[[wait-protocol]]"))
  (.put-file world "voice-origin-tag.md" text)
  (.turn world)
  ;; ここで機体が変わった(置き場は空・行と記録の service だけが残る)。
  (setv world.local.files {})
  (.turn world)
  (setv files (.hydrated world))
  (setv by-name (dfor f files (get f "name") (get f "text")))
  (assert (= (sorted (.keys by-name)) (sorted ["voice-origin-tag.md" MEMORY-INDEX-FILE])) by-name)
  ;; 本文は逐語(記録の service から引いた本文そのもの)。
  (assert (= (get by-name "voice-origin-tag.md") text) by-name)
  ;; 索引は行から組み直され、冊を 1 つ数える(受入 4)。
  (setv items (lfor line (.splitlines (get by-name MEMORY-INDEX-FILE)) :if (.startswith line "- [") line))
  (assert (= items ["- [voice-origin-tag](voice-origin-tag.md) — 先頭が (voice) の指示は読み上げ向けに答える"]) items)
  ;; charter に載る欄は 1 つ(器の側はこれを書き出すだけ — 行も記録の service も知らない)。
  (assert (= CHARTER-MEMORY-FILES-KEY "memory_files")))


(deftest test-a-record-service-that-will-not-take-the-book-does-not-fail-the-turn
  ;; 記憶が書けないことは手番の失敗ではない — condition を 1 つ立てて手番は続く(summary と同じ扱い)。
  (setv world (MemoryWorld))
  (.put-file world "a-fact.md" (book-text "a-fact" "要旨" "本文。"))
  (setv world.record.unreachable True)
  (.turn world)
  (setv conditions (lfor c (.job-conditions world) :if (= (get c "type") CONDITION-MEMORY-UNWRITABLE) c))
  (assert (= (len conditions) 1) (.job-conditions world))
  (assert (= (.memory-rows world) #()) "本文を積めていないのに行を作った"))
