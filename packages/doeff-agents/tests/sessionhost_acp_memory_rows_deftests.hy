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
;;;   * 撃ち分けの 1 点(法 575b1e): **手元・行・基準**の 3 点比較 — 2 点(手元と行)だけでは
;;;     『席が書いた』と『席は触っていないが行が別の機体で動いた』が割れず、触っていない古い写しで
;;;     行を巻き戻していた(2026-09-21 の実弾: 冊 mail-hold-has-two-exits が v2 6,503 → v3 4,620 byte)
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
  CHARTER-MEMORY-DIR-KEY
  CHARTER-MEMORY-FILES-KEY
  CONDITION-MEMORY-UNWRITABLE
  MEMORY-BASE-FILE
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
  MemoryBaseline
  MemoryBook
  MemoryMalformed
  MemorySupersede
  MemoryUnbased
  MemoryUnchanged])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeRecord FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  memory-baseline-of-row
  memory-baseline-text-of
  memory-baselines-of-text
  memory-body-of
  memory-book-of
  memory-files-of
  memory-index-of
  memory-row-retired?
  memory-rows-by-name
  memory-sha256-of
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
    (setv self.turns 0)
    (setv self.warm False)
    ;; 旧い版の器(この族より前 — 記憶の綴りを 1 つも知らない)の体。charter は届いているのに置き場へ
    ;; 1 file も書かないので、置き場は前の手番の写しのまま凍る。基準を持てないまま走り続ける席
    ;; (裁定 (f) の窓)と、席が触っていない写しが行を巻き戻す実弾の形は、この形でしか作れない。
    (setv self.stale-seat False)
    ;; 席がこの手番で置き場へ起こす編集(水入れの**後**に落ちる — 本物の順序)。
    (setv self.pending []))

  (defn #^ list dispatchers [self]
    [self.record.dispatch self.acp.dispatch self.custody.dispatch self.sessions.dispatch self.local.dispatch])

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state (run-tick self.settings self.state (.dispatchers self)))
    None)

  (defn #^ None put-file [self #^ str name #^ str text]
    "席がこの手番で置き場へ書く file(水入れの**後**に落ちる)。手番の頭に置くと水入れが上書きしてしまい、
     『席が書いた』と『水入れが置いた写し』が検で割れない。"
    (.append self.pending #("put" name text))
    None)

  (defn #^ None drop-file [self #^ str name]
    "席がこの手番で置き場から消す file(水入れの後)。基準を消す = 旧い agentd に起こされた席の形。"
    (.append self.pending #("drop" name ""))
    None)

  (defn #^ None hydrate [self]
    "器(impls/claude_code)が charter の memory_files を置き場へ書いた体。この世界の器は fake なので
     『全部書けた席』を写す — 名の門と基準の絞りは**本物の器**の検(sessionhost_charter_reaches_the_seat)側。"
    (when (not self.sessions.launches)
      (return None))
    (when self.stale-seat
      (return None))
    (for [book (.hydrated self)]
      (setv (get self.local.files f"{HOME}/{(get book "name")}") (get book "text")))
    None)

  (defn #^ None put-elsewhere [self #^ str name #^ str text]
    "別の機体の書きを**この手番の中**(水入れの後・畳み戻しの前)に落とす。"
    (.append self.pending #("elsewhere" name text))
    None)

  (defn #^ None apply-pending [self]
    (for [#(verb name text) self.pending]
      (cond (= verb "put") (setv (get self.local.files f"{HOME}/{name}") text)
            (= verb "drop") (.pop self.local.files f"{HOME}/{name}" None)
            True (.elsewhere self name text)))
    (setv self.pending [])
    None)

  (defn #^ None elsewhere [self #^ str name #^ str text]
    "**別の機体**が同じ冊を手番の途中で書いた体: 記録の stream の今の版を置き換え、行の claim check を
     進める(この会話の席は 1 字も触っていない)。規則 2a / 2d が割る形はこの口でしか作れない。"
    (setv body (run (memory-body-of text (+ self.local.now-ms 5))))
    (setv sha256 (run (memory-sha256-of body)))
    (setv stream-id (run (memory-stream-id-of name)))
    (setv seq (.rewrite self.record CID stream-id body))
    (setv row (get self.acp.rows f"{AGORA-KINDS-NAMESPACE}:{MEMORY-KIND}:mem-{CID}-{name}"))
    (setv spec (dict row.spec))
    (setv (get spec MEMORY-SPEC-RECORD-SEQ-KEY) seq)
    (setv (get spec MEMORY-SPEC-SHA256-KEY) sha256)
    (setv (get spec MEMORY-SPEC-VERSION-KEY) (+ (.get spec MEMORY-SPEC-VERSION-KEY 1) 1))
    (setv (get self.acp.rows row.key) (replace row :spec spec))
    None)

  (defn #^ dict baseline-of-home [self]
    "置き場の基準(器が書いた物 / 畳み戻しが進めた物)。"
    (setv text (.get self.local.files f"{HOME}/{MEMORY-BASE-FILE}"))
    (if (isinstance text str) (run (memory-baselines-of-text text)) {}))

  (defn #^ list metric-lines [self #^ str metric]
    (lfor line self.local.metrics :if (= (.get line "metric") metric) line))

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

  (defn #^ None turn [self [warm False]]
    "1 手番を頭から終いまで(水入れ = incarnate → 本文 → 畳み戻し = settle-record)。

     既定では手番のたびに器の session を落とす — この便の世界では次の手番がどの機体に落ちるか分からず、
     器は毎回作り直される(pod の世代交代・機体の移動)。生きた session が残っていると 2 手番目は
     送りになって水入れの腕(incarnate)を通らないので、検が水入れを 1 度しか撃てない。

     warm = True はその落としを**しない** = 温かい session への送り(NEXT-ARM-SEND)。claude の
     headless はそれでも手番の終わりに降りているので、器はこの送りで process を起こし直す
     (card acp:kanban-issue:ki-a40292ed30d9 の 4 つ目の腕)。"
    (setv self.warm warm)
    (when (not warm)
      (.clear self.sessions.views))
    (setv self.turns (+ self.turns 1))
    (setv job (.job-id self))
    (.put-row self.acp (row-of AGORA-KINDS-NAMESPACE MESSAGE-KIND f"m-{self.turns}"
                               {"id" f"m-{self.turns}" "body" "first"} {"state" "inbox"}))
    (.put-row self.acp (bound-job job [f"m-{self.turns}"]))
    (.tick self 0)
    ;; 手番の順は 水入れ(器が charter の file を置き場へ書く)→ 席の編集 → 畳み戻し。
    (.hydrate self)
    (.apply-pending self)
    (setv (get self.local.transcripts f"/events/{(.sid self)}.events.jsonl") (claude-events (.sid self) "hello"))
    (.tick self 1000)
    (.finish-turn self.sessions (.sid self) (+ self.local.now-ms 100))
    (.tick self 1000)
    None)

  (defn #^ dict charter [self]
    "この手番で器へ渡した charter(水入れが載せた memory_files はここに在る)。"
    (get self.sessions.launches -1))

  (defn #^ dict seat-memory-input [self]
    "この手番で器へ渡した『記憶の荷』。起こす腕は charter(launch params)・温かい腕は**送りの荷**
     (SessionSend.turn_charter — 器はこれで降りた process を起こし直す)。腕で口が変わるのは、
     行に残さない欄(policy.TURN-CARRIED-KEYS)だから: 行の写しでは 2 手番目に古い値で起きる。"
    (if self.warm
        (if self.sessions.send-turn-charters (get (get self.sessions.send-turn-charters -1) 1) {})
        (.charter self)))

  (defn #^ tuple hydrated [self]
    (tuple (.get (.seat-memory-input self) CHARTER-MEMORY-FILES-KEY #())))

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


(defn #^ AcpRow memory-row [#^ str name #^ str sha256 sha-seq version]
  "kind agent-memory の行 1 つ(claim check の 3 欄だけを動かす見本)。"
  (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{MEMORY-KIND}:mem-{name}"
          :kind MEMORY-KIND :resource-id f"mem-{name}" :version 1 :generation 1 :created-at-ms NOW
          :labels {} :payload {}
          :spec {"conversationId" CID "name" name MEMORY-SPEC-SHA256-KEY sha256
                 MEMORY-SPEC-RECORD-SEQ-KEY sha-seq MEMORY-SPEC-VERSION-KEY version}
          :status {"state" "current"}))


(deftest test-the-write-verdict-compares-the-home-the-row-and-the-baseline
  ;; 法 575b1e の撃ち分けは **3 点**(手元・行・基準)。2 点だけでは『席が書いた』と『席は触っていないが
  ;; 行が別の機体で動いた』が割れず、触っていない古い写しで行を巻き戻していた。
  (setv home (* "a" 64))   ; 置き場のいまの本文の digest
  (setv base (* "b" 64))   ; 手番の頭に置き場へ出した時の digest
  (setv moved (* "c" 64))  ; 行のいまの digest(別の機体が書いた)
  ;; 規則 1: 行と手元が一致 → 撃つ理由が無い(基準の在否に依らない)。
  (for [with-base [None (MemoryBaseline :name "a" :record-seq 7 :sha256 base :version 2)]]
    (<- one (| MemoryUnchanged MemoryAppend MemorySupersede MemoryUnbased)
        (memory-write-verdict (memory-row "a" home 7 2) home with-base))
    (assert (isinstance one MemoryUnchanged) #("規則 1" one))
    ;; 規則 1 は行が『手元の写しは行の今の版』を**証明**している拍 — 呼び手はここで基準を据える。
    (assert one.proven-by-row #("規則 1 が証明を名乗っていない — 呼び手が基準を据えられない" one)))
  ;; 規則 2a: 手元 == 基準 = **席は 1 字も触っていない**。行が先へ動いていても巻き戻さない(実弾の形)。
  (<- two-a (| MemoryUnchanged MemoryAppend MemorySupersede MemoryUnbased)
      (memory-write-verdict (memory-row "a" moved 9 3) home
                            (MemoryBaseline :name "a" :record-seq 7 :sha256 home :version 2)))
  (assert (isinstance two-a MemoryUnchanged) #("規則 2a — 触っていない写しで行を巻き戻した" two-a))
  ;; 2a は『席が触っていない』だけ。行は先へ動いているので**証明ではない** — ここで基準を据えると
  ;; 次の手番が 2c/2d へ落ち、古い写しで行を巻き戻す(裁定 (f) を 1 語に畳むと起きる誤実装)。
  (assert (not two-a.proven-by-row) #("規則 2a が証明を名乗った — 呼び手が基準を据えてしまう" two-a))
  ;; 規則 2b: 基準は在るが行が消えた → append(409 が『stream だけ残っている』の合図になる)。
  (<- two-b (| MemoryUnchanged MemoryAppend MemorySupersede MemoryUnbased)
      (memory-write-verdict None home (MemoryBaseline :name "a" :record-seq 7 :sha256 base :version 2)))
  (assert (isinstance two-b MemoryAppend) #("規則 2b" two-b))
  ;; 規則 2c: 席が編集し、行は動いていない → supersede(衝突ではない)。
  (<- two-c (| MemoryUnchanged MemoryAppend MemorySupersede MemoryUnbased)
      (memory-write-verdict (memory-row "a" base 7 2) home
                            (MemoryBaseline :name "a" :record-seq 7 :sha256 base :version 2)))
  (assert (isinstance two-c MemorySupersede) two-c)
  (assert (= two-c.record-seq 7) two-c)
  (assert (= two-c.version 2) two-c)
  (assert (= two-c.base-seq 7) two-c)
  (assert (not two-c.conflicted) #("動いていない行を衝突と名乗った" two-c))
  ;; 規則 2d: 席も編集し、行も手番の**間に**動いた → 断らず行の**今の**版へ重ね、衝突を名乗る。
  (<- two-d (| MemoryUnchanged MemoryAppend MemorySupersede MemoryUnbased)
      (memory-write-verdict (memory-row "a" moved 9 3) home
                            (MemoryBaseline :name "a" :record-seq 7 :sha256 base :version 2)))
  (assert (isinstance two-d MemorySupersede) two-d)
  (assert (= two-d.record-seq 9) #("行の今の recordSeq へ重ねる" two-d))
  (assert (= two-d.version 3) two-d)
  (assert (= two-d.base-seq 7) #("基準が指していた recordSeq を名乗る" two-d))
  (assert two-d.conflicted #("行が動いたのに衝突を名乗らない" two-d))
  ;; 規則 3a: 基準も行も無い = この会話で初めての冊 → append。
  (<- three-a (| MemoryUnchanged MemoryAppend MemorySupersede MemoryUnbased)
      (memory-write-verdict None home None))
  (assert (isinstance three-a MemoryAppend) #("規則 3a" three-a))
  ;; 規則 3b: 基準が無いのに行が在る → **撃たない**(手元が編集か古い写しか判らない)。
  (<- three-b (| MemoryUnchanged MemoryAppend MemorySupersede MemoryUnbased)
      (memory-write-verdict (memory-row "a" moved 9 3) home None))
  (assert (isinstance three-b MemoryUnbased) #("規則 3b — 基準の無い置き場で行を上書きした" three-b))
  ;; 行は在るが recordSeq / version が読めない(基準は在る)→ 今日どおり append に倒す。
  (setv unreadable (replace (memory-row "a" base 7 2) :spec {"conversationId" CID "name" "a" MEMORY-SPEC-SHA256-KEY base}))
  (<- fallback (| MemoryUnchanged MemoryAppend MemorySupersede MemoryUnbased)
      (memory-write-verdict unreadable home (MemoryBaseline :name "a" :record-seq 7 :sha256 base :version 2)))
  (assert (isinstance fallback MemoryAppend) #("recordSeq の無い行は append" fallback))
  ;; bool の門: Python では bool は int なので、True を版や recordSeq と読むと黙って supersede に落ちる。
  (for [broken [(memory-row "a" base True 2) (memory-row "a" base 7 True)]]
    (<- gated (| MemoryUnchanged MemoryAppend MemorySupersede MemoryUnbased)
        (memory-write-verdict broken home (MemoryBaseline :name "a" :record-seq 7 :sha256 base :version 2)))
    (assert (isinstance gated MemoryAppend) #("bool を int として読んだ" gated))))


(deftest test-the-baseline-file-round-trips-and-drops-what-it-cannot-read
  ;; 基準は置き場の side car で **正本ではない**: 本文を 1 字も持たず、行にも stream にも書かない。
  (setv baselines {"a-fact" (MemoryBaseline :name "a-fact" :record-seq 7 :sha256 (* "a" 64) :version 2)
                   "b-fact" (MemoryBaseline :name "b-fact" :record-seq 9 :sha256 (* "b" 64) :version 1)})
  (<- text str (memory-baseline-text-of baselines))
  (<- back dict (memory-baselines-of-text text))
  (assert (= back baselines) back)
  (for [spelling ["\"text\"" "\"body\"" "\"content\""]]
    (assert (not (in spelling text)) #("基準が本文を持っている" spelling text)))
  ;; file が丸ごと読めない形は空へ倒す(空 = 行の在る冊を 1 つも撃たない安全側)。
  (for [bad ["" "{" "[]" "null" "\"x\"" "{}" "{\"books\": []}" "{\"books\": null}"]]
    (<- none dict (memory-baselines-of-text bad))
    (assert (= none {}) #(bad none)))
  ;; 項の単位で落とす(1 項の腐りで全冊を捨てない)。
  (setv mixed (json.dumps {"books" {"ok" {"recordSeq" 3 "sha256" (* "c" 64) "version" 1}
                                    "no-seq" {"sha256" (* "d" 64) "version" 1}
                                    "no-sha" {"recordSeq" 4 "version" 1}
                                    "bool-version" {"recordSeq" 5 "sha256" (* "e" 64) "version" True}
                                    "bool-seq" {"recordSeq" True "sha256" (* "e" 64) "version" 1}
                                    "Bad Name" {"recordSeq" 6 "sha256" (* "f" 64) "version" 1}
                                    "not-an-object" "x"}}))
  (<- kept dict (memory-baselines-of-text mixed))
  (assert (= (sorted (.keys kept)) ["ok"]) kept)
  ;; 行 → 基準は claim check の 4 欄が**全部**読める行だけ(半端な基準で撃つより撃たない)。
  (<- from-row (| MemoryBaseline None) (memory-baseline-of-row (memory-row "a-fact" (* "a" 64) 7 2)))
  (assert (= from-row (MemoryBaseline :name "a-fact" :record-seq 7 :sha256 (* "a" 64) :version 2)) from-row)
  (<- no-row (| MemoryBaseline None) (memory-baseline-of-row None))
  (assert (is no-row None) no-row)
  (<- half (| MemoryBaseline None)
      (memory-baseline-of-row (replace (memory-row "a-fact" (* "a" 64) 7 2)
                                       :spec {"conversationId" CID "name" "a-fact" MEMORY-SPEC-SHA256-KEY (* "a" 64)})))
  (assert (is half None) half))


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
  (<- files tuple (memory-files-of books {}))
  (assert (= (len files) (+ (len books) 2)) files)
  (assert (= (get (get files -2) "name") MEMORY-INDEX-FILE) files)
  ;; 基準は最後(冊と同じ拍・同じ書き手で置く — 器は書けた冊へ絞ってからこれを書く)。
  (assert (= (get (get files -1) "name") MEMORY-BASE-FILE) files)
  ;; 冊が 0 でも索引と基準は書く(前の手番の腐った写しを残さない)。
  (<- empty tuple (memory-files-of #() {}))
  (assert (= (lfor f empty (get f "name")) [MEMORY-INDEX-FILE MEMORY-BASE-FILE]) empty))


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
  (assert (= (lfor f files (get f "name")) [MEMORY-INDEX-FILE MEMORY-BASE-FILE]) files))


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
  ;; 名簿は 冊 + 索引 + 畳み戻しの基準(基準は冊と同じ拍・同じ書き手で置く)。
  (assert (= (sorted (.keys by-name)) (sorted ["voice-origin-tag.md" MEMORY-INDEX-FILE MEMORY-BASE-FILE])) by-name)
  ;; 本文は逐語(記録の service から引いた本文そのもの)。
  (assert (= (get by-name "voice-origin-tag.md") text) by-name)
  ;; 索引は行から組み直され、冊を 1 つ数える(受入 4)。
  (setv items (lfor line (.splitlines (get by-name MEMORY-INDEX-FILE)) :if (.startswith line "- [") line))
  (assert (= items ["- [voice-origin-tag](voice-origin-tag.md) — 先頭が (voice) の指示は読み上げ向けに答える"]) items)
  ;; charter に載る欄は 1 つ(器の側はこれを書き出すだけ — 行も記録の service も知らない)。
  (assert (= CHARTER-MEMORY-FILES-KEY "memory_files")))


(deftest test-a-warm-turn-carries-the-home-and-the-books-to-the-seat-too
  ;; 4 つ目の腕(card acp:kanban-issue:ki-a40292ed30d9): 温かい session への手番は起こす腕
  ;; (incarnate)を通らず、送りだけで進む。ところが claude の headless は手番の終わりに必ず降りる
  ;; ので、器はこの送りで process を `--resume` から起こし直す ⇒ 置き場と冊がこの送りに載って
  ;; いないと、**会話の 2 手番目から**席は空の置き場を読む(2026-09-21 の実弾)。
  (setv world (MemoryWorld))
  (setv text (book-text "wait-protocol" "待ちの作法" "本文。[[voice-origin-tag]]"))
  (.put-file world "wait-protocol.md" text)
  (.turn world)                      ;; 1 手番目 = 起こす腕(席が 1 冊書き、畳み戻しが行にする)
  (setv world.local.files {})        ;; 機体が変わった体(置き場は空・行と記録だけが残る)
  (.turn world :warm True)           ;; 2 手番目 = 送りの腕(器の session は生きたまま)
  (setv carried (.seat-memory-input world))
  (assert (= (.get carried CHARTER-MEMORY-DIR-KEY) HOME)
          #("送りの腕が置き場を名乗らない" (sorted (.keys carried))))
  (setv by-name (dfor f (.hydrated world) (get f "name") (get f "text")))
  (assert (= (sorted (.keys by-name)) (sorted ["wait-protocol.md" MEMORY-INDEX-FILE MEMORY-BASE-FILE]))
          by-name)
  (assert (= (get by-name "wait-protocol.md") text) by-name))


(deftest test-a-record-service-that-will-not-take-the-book-does-not-fail-the-turn
  ;; 記憶が書けないことは手番の失敗ではない — condition を 1 つ立てて手番は続く(summary と同じ扱い)。
  (setv world (MemoryWorld))
  (.put-file world "a-fact.md" (book-text "a-fact" "要旨" "本文。"))
  (setv world.record.unreachable True)
  (.turn world)
  (setv conditions (lfor c (.job-conditions world) :if (= (get c "type") CONDITION-MEMORY-UNWRITABLE) c))
  (assert (= (len conditions) 1) (.job-conditions world))
  (assert (= (.memory-rows world) #()) "本文を積めていないのに行を作った"))


;; ---------------------------------------------------------------------------
;; 基準が在る世界の畳み戻し(fake の記録 + fake の ACP・腕は run-tick 越し)
;; ---------------------------------------------------------------------------

(deftest test-a-copy-the-seat-never-touched-does-not-roll-the-row-back
  ;; 実弾の形(2026-09-21): 手番の頭に置いた写しに席が 1 字も触らず、その間に別の機体が同じ冊へ
  ;; 濃い版を書いた。2 点比較は「手元 ≠ 行」だけを見て古い写しで supersede し、冊が痩せていた
  ;; (mail-hold-has-two-exits v2 6,503 → v3 4,620 byte)。3 点比較は規則 2a でここを閉じる。
  (setv world (MemoryWorld))
  (.put-file world "mail-hold-has-two-exits.md"
             (book-text "mail-hold-has-two-exits" "郵便の保留には出口が 2 つ" "1 手番目に席が書いた本文。"))
  (.turn world)
  (setv appends (len (.memory-appends world)))
  ;; 2 手番目: 水入れの**後**に別の機体が濃い版を書く。この会話の席は 1 字も編集しない。
  (.put-elsewhere world "mail-hold-has-two-exits"
                  (book-text "mail-hold-has-two-exits" "郵便の保留には出口が 2 つ"
                             "別の機体が書いた濃い本文(出口は解放と期限切れの 2 つ)。"))
  (.turn world)
  ;; 1 bit も撃たない。
  (assert (= (len (.memory-appends world)) appends) "触っていない写しで追記を撃っている")
  (assert (= world.record.supersedes []) "触っていない写しで行を巻き戻している")
  (setv folded (get (.metric-lines world "agent-memory-folded") -1))
  (assert (= (get folded "written") 0) folded)
  (assert (= (get folded "unchanged") 1) folded)
  (assert (= (get folded "conflicted") 0) folded)
  (assert (= (get folded "unbased") 0) folded)
  ;; 行は**濃い版**のまま(claim check が stream の今の本文を指している)。
  (setv events (.events-of world.record CID (run (memory-stream-id-of "mail-hold-has-two-exits"))))
  (assert (in "別の機体が書いた濃い本文" (get (get events 0) "text")) events)
  (setv spec (. (get (.memory-rows world) 0) spec))
  (assert (= (get spec MEMORY-SPEC-SHA256-KEY)
             (run (memory-sha256-of {"producerSeq" 0 "kind" "memory" "text" (get (get events 0) "text")})))
          spec))


(deftest test-a-home-without-a-baseline-writes-no-book-back
  ;; 規則 3b: 基準が無いのに行が在る = 手元が『席の編集』か『前の手番の古い写し』か判らない
  ;; (旧い agentd に起こされた温かい席が、直しの載った agentd に畳み戻される窓)。撃たずに名乗る。
  (setv world (MemoryWorld))
  (.put-file world "a-fact.md" (book-text "a-fact" "要旨" "1 手番目の本文。"))
  (.turn world)
  (setv before (. (get (.memory-rows world) 0) spec))
  (setv appends (len (.memory-appends world)))
  (.drop-file world MEMORY-BASE-FILE)
  (.put-file world "a-fact.md" (book-text "a-fact" "要旨" "基準の無い置き場で書き換えた本文。"))
  (.turn world)
  (assert (= (len (.memory-appends world)) appends) "基準の無い置き場で追記を撃っている")
  (assert (= world.record.supersedes []) "基準の無い置き場で行を上書きしている")
  (assert (= (. (get (.memory-rows world) 0) spec) before) "行の claim check が動いた")
  (setv folded (get (.metric-lines world "agent-memory-folded") -1))
  (assert (= (get folded "unbased") 1) folded)
  (assert (= (get folded "written") 0) folded)
  ;; 記憶が書けないことは手番の失敗ではない — 手番は落ちず、log が 1 行名乗る(黙って捨てない)。
  (assert (= (.job-conditions world) []) (.job-conditions world))
  (assert (any (gfor line world.local.logs (and (in "has no baseline" line) (in "a-fact" line))))
          world.local.logs))


(deftest test-a-book-that-moved-under-the-turn-supersedes-and-names-the-conflict
  ;; 規則 2d: 席も編集し、行も手番の**間に**動いた。断らない(席の編集を捨てない)— 行の今の版へ重ね、
  ;; 両方の版を鎖に残し、log と計器で名乗る。
  (setv world (MemoryWorld))
  (.put-file world "a-fact.md" (book-text "a-fact" "要旨" "1 手番目の本文。"))
  (.turn world)
  (.put-elsewhere world "a-fact" (book-text "a-fact" "要旨" "別の機体が手番の途中で書いた本文。"))
  (.put-file world "a-fact.md" (book-text "a-fact" "要旨" "席がこの手番で書いた本文。"))
  (.turn world)
  (setv folded (get (.metric-lines world "agent-memory-folded") -1))
  (assert (= (get folded "written") 1) folded)
  (assert (= (get folded "conflicted") 1) folded)
  (assert (= (get folded "unbased") 0) folded)
  (assert (= (len world.record.supersedes) 1) world.record.supersedes)
  ;; 行は席の版を指す。
  (setv events (.events-of world.record CID (run (memory-stream-id-of "a-fact"))))
  (assert (in "席がこの手番で書いた本文" (get (get events 0) "text")) events)
  ;; ⇒ 別の機体の版も 1 手番目の版も鎖に残る(どちらも消さない)。
  (setv chain (get (list (.values world.record.superseded)) 0))
  (assert (= (len chain) 2) chain)
  (assert (in "1 手番目の本文" (get (get chain 0) "text")) chain)
  (assert (in "別の機体が手番の途中で書いた本文" (get (get chain 1) "text")) chain)
  ;; log は 冊の名・基準の recordSeq・行の recordSeq を運ぶ(後から実弾を数えられる形)。
  (setv named (lfor line world.local.logs :if (in "moved under the turn" line) line))
  (assert (= (len named) 1) world.local.logs)
  (assert (in "a-fact" (get named 0)) named)
  (assert (in "baseline recordSeq" (get named 0)) named)
  (assert (in "row recordSeq" (get named 0)) named))


(deftest test-the-hydrated-home-folds-back-unchanged-in-the-same-turn
  ;; 受入 3: 水入れが冊+索引+基準を同じ拍で置き、その手番の畳み戻しが 1 bit も撃たない(往復)。
  ;; 受入 4: 予約名(MEMORY.md / MEMORY.base.json)は冊でも誤りでもない — 黙って除く。
  (setv world (MemoryWorld))
  (.put-file world "voice-origin-tag.md" (book-text "voice-origin-tag" "読み上げ向けに答える" "平易文体を既定にする。"))
  (.turn world)
  (setv appends (len (.memory-appends world)))
  ;; 機体が変わった体(置き場は空)— 水入れが行から置き直し、席は 1 字も触らない。
  (setv world.local.files {})
  (.turn world)
  (assert (= (lfor f (.hydrated world) (get f "name"))
             ["voice-origin-tag.md" MEMORY-INDEX-FILE MEMORY-BASE-FILE])
          (.hydrated world))
  (assert (= (len (.memory-appends world)) appends) "往復しただけで追記を撃っている")
  (assert (= world.record.supersedes []) "往復しただけで置き換えを撃っている")
  (setv folded (get (.metric-lines world "agent-memory-folded") -1))
  (assert (= (get folded "books") 1) folded)
  (assert (= (get folded "written") 0) folded)
  (assert (= (get folded "unchanged") 1) folded)
  (assert (= (get folded "unbased") 0) folded)
  (assert (= (get folded "conflicted") 0) folded)
  ;; 予約名を冊として数えない ⇒ 正常な手番の unreadable は **0**(旧: 索引 1 つ分で常に 1)。
  (assert (= (get folded "unreadable") 0) folded)
  (assert (= (lfor line world.local.logs :if (in "was not written back" line) line) []) world.local.logs)
  ;; 行は 1 版のまま。置き場の基準は行の claim check と 3 欄とも一致している。
  (setv spec (. (get (.memory-rows world) 0) spec))
  (assert (= (get spec MEMORY-SPEC-VERSION-KEY) 1) spec)
  (setv base (get (.baseline-of-home world) "voice-origin-tag"))
  (assert (= base.record-seq (get spec MEMORY-SPEC-RECORD-SEQ-KEY)) base)
  (assert (= base.sha256 (get spec MEMORY-SPEC-SHA256-KEY)) base)
  (assert (= base.version (get spec MEMORY-SPEC-VERSION-KEY)) base)
  ;; 水入れは「基準を持って出した冊」を名乗る(agent-memory-hydrated の based)。
  (setv hydrated (get (.metric-lines world "agent-memory-hydrated") -1))
  (assert (= (get hydrated "based") 1) hydrated)
  (assert (= (get hydrated "books") 1) hydrated))


(deftest test-a-retired-row-with-a-file-and-no-baseline-is-not-written-back
  ;; 退役の skip は 3 点比較より**前**(順序を入れ替えない)。基準も無い形で二重に守られる:
  ;; もし退役の判断を比較の後ろへ動かしたら、この検は unbased=1 か書き込みで赤くなる。
  (setv world (MemoryWorld))
  (.put-file world "stale.md" (book-text "stale" "もう要らない" "古い事実。"))
  (.turn world)
  (setv row (get (.memory-rows world) 0))
  (setv (get world.acp.rows row.key) (replace row :status {"state" "retired"}))
  (setv appends (len (.memory-appends world)))
  (.drop-file world MEMORY-BASE-FILE)
  (.put-file world "stale.md" (book-text "stale" "もう要らない" "退役した後に書き換えた本文。"))
  (.turn world)
  (assert (= (len (.memory-appends world)) appends) "退役した冊を追記している")
  (assert (= world.record.supersedes []) "退役した冊を置き換えている")
  (assert (= (. (get (.memory-rows world) 0) status) {"state" "retired"}) "退役が取り消された")
  (setv folded (get (.metric-lines world "agent-memory-folded") -1))
  (assert (= (get folded "written") 0) folded)
  (assert (= (get folded "unbased") 0) #("退役した冊が 3 点比較まで進んだ" folded)))


(deftest test-a-book-that-vanished-from-the-home-does-not-touch-the-row
  ;; 基準に在って置き場から消えた冊を「削除」と読まない(退役は operator の側の 2 拍の判断)。
  ;; 畳み戻しが回すのは**置き場に在る冊ちょうど**で、行にしか無い冊は 1 bit も動かさない。
  (setv world (MemoryWorld))
  (.put-file world "a-fact.md" (book-text "a-fact" "要旨" "1 手番目の本文。"))
  (.turn world)
  (setv before (get (.memory-rows world) 0))
  (setv appends (len (.memory-appends world)))
  (.drop-file world "a-fact.md")
  (.turn world)
  (assert (= (len (.memory-appends world)) appends) before)
  (assert (= world.record.supersedes []) world.record.supersedes)
  (setv after (get (.memory-rows world) 0))
  (assert (= after.spec before.spec) #("置き場から消えた冊で行が動いた" after.spec before.spec))
  (assert (= (get after.status "state") "current") after.status))


;; ---------------------------------------------------------------------------
;; 裁定 (f)(依頼者 2026-09-21): 基準を持てない席の窓を、行が証明した拍で閉じる。
;; ---------------------------------------------------------------------------

(deftest test-a-home-an-old-seat-left-without-a-baseline-is-founded-by-the-row-that-proves-it
  ;; 3 点比較だけを入れると、**旧い版の器に起こされて走り続けている席**は基準を持てないまま規則 3b に
  ;; 落ち続け、席の**本物の編集**がその席の寿命ぶん黙って捨てられる(今日より悪い退行)。裁定 (f) =
  ;; 規則 1 の拍 — 行の sha == 手元 = 行が『置き場の写しは行の今の版そのもの』を**証明**している拍 —
  ;; で基準を据える。据える値は証明つきで正しいので、次の手番の編集が書ける。
  (setv world (MemoryWorld))
  (.put-file world "a-fact.md" (book-text "a-fact" "要旨" "1 手番目の本文。"))
  (.turn world)
  (setv seq (get (. (get (.memory-rows world) 0) spec) MEMORY-SPEC-RECORD-SEQ-KEY))
  (setv appends (len (.memory-appends world)))
  ;; ここから器は旧い版(置き場へ 1 file も書かない)+ 置き場の基準を落とす = (f) の窓。
  (setv world.stale-seat True)
  (.drop-file world MEMORY-BASE-FILE)
  ;; 手番 2: 席は 1 字も編集していない ⇒ 手元 == 行 = 規則 1。撃たずに**基準だけ**据わる。
  (.turn world)
  (assert (= (len (.memory-appends world)) appends) "撃つ理由の無い拍で追記を撃っている")
  (assert (= world.record.supersedes []) "撃つ理由の無い拍で行を上書きしている")
  (setv folded (get (.metric-lines world "agent-memory-folded") -1))
  (assert (= (get folded "written") 0) folded)
  (assert (= (get folded "unchanged") 1) folded)
  (assert (= (get folded "unbased") 0) #("基準を据えずに 3b へ落ちた — 窓が開いたまま" folded))
  (assert (= (get folded "founded") 1) #("規則 1 の拍で基準を据えていない" folded))
  (setv based (.baseline-of-home world))
  (assert (= (sorted (.keys based)) ["a-fact"]) based)
  (assert (= (. (get based "a-fact") record-seq) seq) #("据えた基準が行の今の版を指していない" based))
  ;; 手番 3: 同じ(旧い版の器の)席が**本当に**編集する → 窓は閉じていて書ける。
  (.put-file world "a-fact.md" (book-text "a-fact" "要旨" "温かい席が 3 手番目に書いた本文。"))
  (.turn world)
  (setv folded (get (.metric-lines world "agent-memory-folded") -1))
  (assert (= (get folded "written") 1) #("基準が据わったのに席の編集が捨てられた" folded))
  (assert (= (get folded "unbased") 0) folded)
  (assert (= (get folded "conflicted") 0) #("動いていない行を衝突と名乗った" folded))
  (setv events (.events-of world.record CID (run (memory-stream-id-of "a-fact"))))
  (assert (in "温かい席が 3 手番目に書いた本文" (get (get events 0) "text")) events))


(deftest test-the-baseline-does-not-move-on-the-beat-the-seat-never-touched-the-book
  ;; 裁定 (f) を「Unchanged なら基準を進める」と 1 語で書くと**規則 2a にも効く**(判定は規則 1 と 2a の
  ;; 両方が Unchanged で返る)。2a の姿は『手元 == 基準・行は先へ動いている』なので、そこで基準を行の
  ;; 今の版へ進めると、次の手番が 2c/2d へ落ちて席が触っていない古い写しで行を巻き戻す = この族が
  ;; 直した実弾の形(mail-hold-has-two-exits v2 6,503 → v3 4,620 byte)がそのまま戻る。
  ;; ⚠ 既存の test-a-copy-the-seat-never-touched-does-not-roll-the-row-back は 1 手番ぶんの unchanged しか
  ;;    見ないので、その誤実装を**緑で通す** — だから基準の byte と次の手番をここで留める。
  (setv world (MemoryWorld))
  (.put-file world "mail-hold-has-two-exits.md"
             (book-text "mail-hold-has-two-exits" "郵便の保留には出口が 2 つ" "1 手番目に席が書いた薄い本文。"))
  (.turn world)
  ;; 器を旧い版にして置き場を凍らせる(手番の頭に写しが入れ替わると、席が『触っていない』形が作れない)。
  (setv world.stale-seat True)
  (setv before (get world.local.files f"{HOME}/{MEMORY-BASE-FILE}"))
  (setv before-seq (. (get (run (memory-baselines-of-text before)) "mail-hold-has-two-exits") record-seq))
  ;; 手番 2: 席は 1 字も触らず、別の機体が同じ冊へ濃い版を書く(= 規則 2a)。
  (.put-elsewhere world "mail-hold-has-two-exits"
                  (book-text "mail-hold-has-two-exits" "郵便の保留には出口が 2 つ"
                             "別の機体が書いた濃い本文(出口は解放と期限切れの 2 つ)。"))
  (.turn world)
  (setv folded (get (.metric-lines world "agent-memory-folded") -1))
  (assert (= (get folded "unchanged") 1) folded)
  (assert (= (get folded "founded") 0) #("2a の拍で基準を据えた — 次の手番が古い写しで行を巻き戻す" folded))
  ;; 行は先へ動いている(この検が意味を持つ前提 — 動いていなければ 2a を撃てていない)。
  (setv row-seq (get (. (get (.memory-rows world) 0) spec) MEMORY-SPEC-RECORD-SEQ-KEY))
  (assert (> row-seq before-seq) #("行が動いていない = 規則 2a の形になっていない" row-seq before-seq))
  ;; 基準は 1 byte も動かない。
  (assert (= (get world.local.files f"{HOME}/{MEMORY-BASE-FILE}") before)
          #("2a の拍で基準が行の今の版へ進んだ" (get world.local.files f"{HOME}/{MEMORY-BASE-FILE}") before))
  ;; 手番 3: 席はまだ 1 字も触っていない。基準が進んでいたらここで 2c に落ちて薄い本文が行を巻き戻す。
  (.turn world)
  (assert (= world.record.supersedes []) "触っていない古い写しで行を巻き戻している")
  (setv events (.events-of world.record CID (run (memory-stream-id-of "mail-hold-has-two-exits"))))
  (assert (in "別の機体が書いた濃い本文" (get (get events 0) "text")) #("濃い版が薄い版に巻き戻された" events)))
