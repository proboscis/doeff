;;; claude の print mode(stream-json の入出力)の替え玉 — 本番の handler を API を撃たずに回すための CLI。
;;;
;;; 実物(claude 2.1.282・実測 = #602 の layer2-cli-capabilities.md)と同じ形の行だけを出す:
;;; - 会話の id: --session-id(既に transcript が在れば "Session ID … is already in use." で rc 1)/ --resume(無ければ
;;;   transcript が無い旨の 1 行で rc 1 — 文言は handler が読まないので実物と同じにしない)/ --resume --fork-session(新しい id を自分で決め、親の transcript を写す)。
;;;   transcript は <CLAUDE_CONFIG_DIR>/projects/<cwd の英数字以外を - に>/<id>.jsonl(1 行 = 1 つの入力)。
;;; - stdin の user の行 1 つ = 1 手番: command_lifecycle(queued → started)→ system/init(capabilities に msg_lifecycle_v1 と
;;;   interrupt_receipt_v1)→(道具)→ assistant → result → command_lifecycle(completed)。返事は tests/scenario_rules.hy の規則。
;;; - 道具の途中の user の行 = 注入(queued のまま・道具の境界で started)。道具の途中の control_request interrupt =
;;;   control_response(still_queued)→ result(error_during_execution・aborted_tools)→ 生き残った注入を次の手番として走らせる。
;;; - SIGINT = result(error_during_execution・terminal_reason aborted_streaming)を出して rc 0 で降りる(2.1.282 の形)。
;;; - --permission-prompt-tool stdio の時は道具の前に control_request can_use_tool を出し、control_response を待つ(allow なら
;;;   touch を本当に撃つ)。
;;; - 規則の compact(scenario_rules.hy の COMPACT-PHRASE)の手番は、init の後・要求の前に会話の自動の圧縮の 2 行(status compacting →
;;;   system/compact_boundary)を出す(#4189)。
;;; - stdin の EOF で降りる。stdin を開いたまま result の後も生きる(温かい — 降ろすのは host の仕事)。
;;; - 入力の前は transcript を作らない(実物と同じ — 最初の入力で作る)。入力を 1 つも受けずに終了した新しい会話は記録を残さない。
;;; - テスト用の env STUB_CLI_AT_START が在れば、起動の直後(入力の前)にその行を出す(main のコメント)。
;;; - --input-format の無い -p <prompt>(冷えた続きの前の 1 回きりの命令)は transcript に印を 1 行足して rc 0。
;;; - 額(実測 2.1.283・#883): result の行の total_cost_usd は会話の累積(CLI の手番 1 回 = TURN-COST)で、usage は
;;;   その手番の分だけ。stdin の EOF・SIGINT で降りる時に累積の額を transcript に実物と同じ形の 1 行で記し
;;;   ({"type":"cost-state","sessionId":…,"totalCostUSD":…} — handler が続きの起点として読む)、--resume の process と
;;;   --fork-session の枝は最後に記した額から数え続ける。SIGKILL で消えた process は額を記さない(実物と同じ — 次の process は
;;;   前に記した額から数える)。
(import json)
(import os)
(import os.path)
(import pathlib [Path])
(import re)
(import select)
(import sys)
(import uuid)

(.insert sys.path 0 (str (. (Path __file__) (resolve) parent parent)))
(import scenario_rules [reply-for REJECTED-ANSWER COMPACT-METADATA advice-stdout IMAGE-MARK DOCUMENT-MARK])

(setv CAPABILITIES ["msg_lifecycle_v1" "interrupt_receipt_v1"])
;; CLI の手番 1 回の額(USD — 2 進で割り切れる値にして、累積の差が検の比べで端数を出さないようにする)。
(setv TURN-COST 0.25)
;; model の名・API の呼びごとの usage・result の modelUsage の窓(#3744 — 実物と同じ欄の名)。本体の会話の assistant の行は
;; parent_tool_use_id が null、道具の途中の subagent の行は道具の呼びの id を持つ。道具の手番は、道具の呼びの行と最後の本文の行で
;; 別の呼びの usage を名乗る(最後の呼びは最後の本文の行)。
(setv MODEL "claude-stub" SUBAGENT-MODEL "claude-stub-sub")
(setv TOOL-CALL-USAGE {"input_tokens" 3 "cache_creation_input_tokens" 0 "cache_read_input_tokens" 1000 "output_tokens" 5})
(setv FINAL-CALL-USAGE {"input_tokens" 4 "cache_creation_input_tokens" 20 "cache_read_input_tokens" 1010 "output_tokens" 1})
(setv SUBAGENT-USAGE {"input_tokens" 1 "cache_read_input_tokens" 50000 "output_tokens" 900})
(setv MAIN-WINDOW {"inputTokens" 7 "outputTokens" 6 "contextWindow" 200000 "maxOutputTokens" 32000})
(setv SUBAGENT-WINDOW {"inputTokens" 1 "contextWindow" 100000})
;; 計時の欄(実測 2.1.292・#3855 — 実物と同じ欄の名): message_start の行は ttft_ms(要求を送ってから message_start まで — 替え玉は
;; API を呼ばないので決まった値)を名乗り、result の行は手番の時計(入力を読んだ刻)からの time_to_request_ms などを名乗る。env
;; CLAUDE_CODE_EMIT_STARTUP_TIMING が在る process だけ、最初の init の行に起動の区間(startup_timing)を、result の行に要求までの区間
;; (time_to_request_phases_ms — 入力ごとの hook の秒が input_hooks)を足す。
(setv STARTUP-TIMING-ENV "CLAUDE_CODE_EMIT_STARTUP_TIMING")
(setv STUB-TTFT-MS 7)
(setv STUB-STARTUP {"phases" {"node_boot_ms" 30 "hooks_init_ms" 20 "input_ready_ms" 60}
                    "phase_start_ms" {"node_boot_ms" 0 "hooks_init_ms" 30 "input_ready_ms" 0}
                    "time_origin_ms" 1791385479741.7954})


(defclass Stop [Exception] "stdin の EOF(降りる)。")


(defn emit [#^ dict record]
  (.write sys.stdout (+ (json.dumps record :ensure-ascii False) "\n"))
  (.flush sys.stdout))

(defn #^ dict assistant-line [#^ str session-id #^ list content #^ dict usage [model MODEL] [parent None]]
  "assistant の行(実物と同じ形 — message の model・usage と最上位の parent_tool_use_id を持つ)。"
  {"type" "assistant" "session_id" session-id "parent_tool_use_id" parent
   "message" {"role" "assistant" "model" model "content" content "usage" usage}})

(defn stream-block [#^ str session-id #^ dict block #^ dict delta #^ int pieces]
  "1 つの content block を pieces 片の差分(--include-partial-messages の stream_event)で流す — 実物と同じく content_block_start と
   content_block_stop で挟む。片の中身は検が読まないので、どの片も同じ delta(#3746 (a) — 考えている間・道具の命令の差分の数を作るため)。"
  (emit {"type" "stream_event" "session_id" session-id "parent_tool_use_id" None
         "event" {"type" "content_block_start" "index" 0 "content_block" block}})
  (for [_ (range pieces)]
    (emit {"type" "stream_event" "session_id" session-id "parent_tool_use_id" None
           "event" {"type" "content_block_delta" "index" 0 "delta" delta}}))
  (emit {"type" "stream_event" "session_id" session-id "parent_tool_use_id" None
         "event" {"type" "content_block_stop" "index" 0}}))

(defn #^ dict model-usage [#^ bool subagent]
  "result の行の modelUsage(本体の model と、subagent を走らせた手番は subagent の model)。"
  (if subagent {MODEL MAIN-WINDOW SUBAGENT-MODEL SUBAGENT-WINDOW} {MODEL MAIN-WINDOW}))

(defn option [#^ list args #^ str name]
  (if (in name args) (get args (+ (.index args name) 1)) None))

(defn transcript-path [#^ str session-id]
  (setv config (get os.environ "CLAUDE_CONFIG_DIR"))
  (setv mangled (re.sub "[^A-Za-z0-9]" "-" (os.path.realpath (os.getcwd))))
  (.format "{}/projects/{}/{}.jsonl" config mangled session-id))

(defn memory-of [#^ str path]
  (if (os.path.exists path)
      (tuple (gfor line (.splitlines (.read-text (Path path) :encoding "utf-8")) :if (.strip line)
                   :setv record (json.loads line) :if (= (.get record "type") "user")
                   (.get record "text" "")))
      #()))

(defn remember [#^ str path #^ str text]
  (os.makedirs (os.path.dirname path) :exist-ok True)
  (with [handle (open path "a" :encoding "utf-8")]
    (.write handle (+ (json.dumps {"type" "user" "text" text} :ensure-ascii False) "\n"))))

(defn user-text [#^ dict record]
  (setv content (.get (.get record "message" {}) "content"))
  ;; image の block は 1 つにつき印 IMAGE-MARK を、document の block は DOCUMENT-MARK を 1 つ本文へ写す(添付が CLI の入力に入ったかを
  ;; 規則が数える — ki-0faa366b76c0・ki-48d236f200ed)。
  (if (isinstance content list)
      (.join "" (gfor part content :if (isinstance part dict)
                      (.get {"image" IMAGE-MARK "document" DOCUMENT-MARK} (.get part "type") (.get part "text" ""))))
      (str (or content ""))))


(defclass Session []
  (defn __init__ [self #^ str session-id #^ str path #^ bool ask]
    (setv self.session-id session-id self.path path self.ask ask
          self.pending b""
          ;; 計時の欄の材料(手番の時計の起点・要求を送るまで・そのうち入力ごとの hook)と、起動の区間を名乗ったか。
          self.turn-started 0.0 self.request-ms 0 self.hook-ms 0 self.startup-told False)
    ;; 会話の累積の額は transcript に最後に記した額から数え続ける(無ければ 0)。
    (setv records (if (os.path.exists path)
                      (lfor line (.splitlines (.read-text (Path path) :encoding "utf-8")) :if (.strip line) (json.loads line))
                      []))
    (setv self.cost (next (gfor record (reversed records) :if (= (.get record "type") "cost-state")
                                (.get record "totalCostUSD"))
                          0.0)))

  (defn lifecycle [self uuid state]
    (when uuid
      (emit {"type" "command_lifecycle" "command_uuid" uuid "state" state "session_id" self.session-id})))

  (defn read-record [self timeout]
    "stdin の次の object(timeout 秒の内に無ければ None・EOF は Stop)。stdin は自分で行に切る(TextIO の buffer に
     2 行目が残ると select が起きない)。"
    (import time)
    (setv deadline (+ (time.monotonic) timeout))
    (while (not-in b"\n" self.pending)
      (setv left (- deadline (time.monotonic)))
      (when (<= left 0) (return None))
      (setv [ready _ _] (select.select [0] [] [] left))
      (when ready
        (setv chunk (os.read 0 65536))
        (when (not chunk) (raise (Stop)))
        (setv self.pending (+ self.pending chunk))))
    (setv [line self.pending] (.split self.pending b"\n" 1))
    (try
      (setv record (json.loads (.decode line "utf-8")))
      (except [ValueError] (return None)))
    (if (isinstance record dict) record None))

  (defn take-injection [self #^ dict record #^ list injections]
    "道具の途中の user の行 = 注入(queued のまま)。"
    (setv ref (.get record "uuid"))
    (.lifecycle self ref "queued")
    (.append injections record))

  (defn wait-tool [self #^ float seconds #^ list injections]
    "道具の秒数だけ stdin を読みながら待つ。答え = 止める合図の request_id(来なければ None)。"
    (import time)
    (setv deadline (+ (time.monotonic) seconds))
    (while True
      (setv left (- deadline (time.monotonic)))
      (when (<= left 0) (return None))
      (setv record (.read-record self left))
      (cond
        (is record None) None
        (= (.get record "type") "user") (.take-injection self record injections)
        (and (= (.get record "type") "control_request")
             (= (.get (.get record "request" {}) "subtype") "interrupt"))
          (return (.get record "request_id")))))

  (defn wait-permission [self #^ str request-id #^ list injections]
    (while True
      (setv record (.read-record self 1.0))
      (cond
        (is record None) None
        (= (.get record "type") "user") (.take-injection self record injections)
        (and (= (.get record "type") "control_response")
             (= (.get (.get record "response" {}) "request_id") request-id))
          (return (.get (.get (.get record "response" {}) "response" {}) "behavior")))))

  (defn result [self #^ str text #^ list refs #^ int deltas #^ float think-seconds #^ bool subagent]
    ;; deltas > 0 なら、確定の本文の前に本文を deltas 片の差分(--include-partial-messages の stream_event の text_delta)で出す。
    ;; 片は字数でほぼ等分(片の連結 = 本文 — fake の FakeReply.deltas と同じ分け方)。実物と同じく差分の列を content_block_start と
    ;; content_block_stop(text_delta でない stream_event)で挟む。think-seconds > 0 なら、実物が考える時と同じく、先に message_start
    ;; (text_delta でない stream_event)を出し、その秒だけ待ってから本文を出す(#3696)。
    (import time)
    (setv stream-ms None)
    (when (> think-seconds 0)
      (setv stream-ms (.elapsed-ms self))
      (emit {"type" "stream_event" "session_id" self.session-id "parent_tool_use_id" None "ttft_ms" STUB-TTFT-MS
             "event" {"type" "message_start" "message" {"role" "assistant" "content" []}}})
      (time.sleep think-seconds))
    (when (> deltas 0)
      (emit {"type" "stream_event" "session_id" self.session-id "parent_tool_use_id" None
             "event" {"type" "content_block_start" "index" 0 "content_block" {"type" "text" "text" ""}}})
      (for [piece (range deltas)]
        (emit {"type" "stream_event" "session_id" self.session-id "parent_tool_use_id" None
               "event" {"type" "content_block_delta" "index" 0
                        "delta" {"type" "text_delta"
                                 "text" (cut text (// (* piece (len text)) deltas) (// (* (+ piece 1) (len text)) deltas))}}}))
      (emit {"type" "stream_event" "session_id" self.session-id "parent_tool_use_id" None
             "event" {"type" "content_block_stop" "index" 0}}))
    (emit (assistant-line self.session-id [{"type" "text" "text" text}] FINAL-CALL-USAGE))
    (+= self.cost TURN-COST)
    (emit (| {"type" "result" "subtype" "success" "is_error" False "result" text "terminal_reason" "completed"
              "session_id" self.session-id "total_cost_usd" self.cost "usage" {"input_tokens" 1 "output_tokens" 1}
              "user_message_uuids" refs "modelUsage" (model-usage subagent)}
             (.timing-fields self stream-ms))))

  (defn elapsed-ms [self]
    "手番の時計(入力を読んだ刻)から今までのミリ秒 — 計時の欄を実物と同じ起点で名乗るため。"
    (import time)
    (int (* 1000 (- (time.monotonic) self.turn-started))))

  (defn timing-fields [self stream-ms]
    "result の行の計時の欄(実物と同じ名)。stream-ms = message_start を出した刻(出していなければ None — 実物の欄の無い形と同じく欄を出さない)。"
    (setv done (.elapsed-ms self))
    (| {"time_to_request_ms" self.request-ms "duration_ms" done "duration_api_ms" (- done self.request-ms) "ttft_ms" done}
       (if (is stream-ms None) {} {"ttft_stream_ms" stream-ms "first_content_frame_ms" stream-ms})
       (if (in STARTUP-TIMING-ENV os.environ)
           {"time_to_request_phases_ms" {"input_hooks" self.hook-ms "other" (- self.request-ms self.hook-ms)}}
           {})))

  (defn hook-lines [self #^ str event #^ str name #^ str stdout #^ str stderr #^ int exit-code #^ str outcome]
    "hook 1 回の開始と応答の 2 行を、実物(CLI 2.1.292 の --include-hook-events)と同じ形で出す(ki-d8b473480303)。"
    (setv hook-id (str (uuid.uuid4)))
    (emit {"type" "system" "subtype" "hook_started" "hook_id" hook-id "hook_name" name "hook_event" event
           "uuid" (str (uuid.uuid4)) "session_id" self.session-id})
    (emit {"type" "system" "subtype" "hook_response" "hook_id" hook-id "hook_name" name "hook_event" event
           "output" (+ stdout stderr) "stdout" stdout "stderr" stderr "exit_code" exit-code "outcome" outcome
           "uuid" (str (uuid.uuid4)) "session_id" self.session-id}))
  (defn run-turn [self #^ list records]
    "1 手番(records = この手番の入力の行 — 普通は 1 つ・生き残った注入の手番は複数)。"
    (import time)
    (setv self.turn-started (time.monotonic))
    (setv refs (lfor record records :if (.get record "uuid") (.get record "uuid")))
    (for [ref refs] (.lifecycle self ref "started"))
    (emit (| {"type" "system" "subtype" "init" "session_id" self.session-id "capabilities" CAPABILITIES
              "model" "stub" "permissionMode" (if self.ask "default" "bypassPermissions") "mcp_servers" []}
             (if (and (in STARTUP-TIMING-ENV os.environ) (not self.startup-told)) {"startup_timing" STUB-STARTUP} {})))
    (setv self.startup-told True)
    (setv text (.join "\n" (gfor record records (user-text record))))
    (setv rule (reply-for text (memory-of self.path)))
    (setv hook-started (.elapsed-ms self))
    (when (> (get rule "hook_seconds") 0)
      ;; 実物と同じく、init の直後に入力ごとの hook の知らせ(stream でない system の行)を出し、hook の秒だけ待ってから答え始める(#3696 の直し)。
      (emit {"type" "system" "subtype" "hook_response" "session_id" self.session-id "hook_event" "UserPromptSubmit"})
      (time.sleep (get rule "hook_seconds")))
    (when (get rule "advice")
      ;; 条件つきルールの助言を返す UserPromptSubmit の hook(--include-hook-events の CLI の開始と応答の 2 行 — stdout は hook の書いた
      ;; JSON の文字列・ki-d8b473480303)。
      (.hook-lines self "UserPromptSubmit" "UserPromptSubmit" (advice-stdout (get rule "advice")) "" 0 "success"))
    (when (get rule "compact")
      ;; 要求の前に会話を自動で圧縮する(#4189)— 実物(CLI 2.1.294 の stream-json の書き手)と同じ形の 2 行: 圧縮中の
      ;; status の行 → system/compact_boundary(compact_metadata は会話の記録の compactMetadata を snake の名へ写した物・
      ;; preserved_segment は残した区間の uuid の組)。
      (emit {"type" "system" "subtype" "status" "status" "compacting" "session_id" self.session-id})
      (emit {"type" "system" "subtype" "compact_boundary" "session_id" self.session-id "uuid" (str (uuid.uuid4))
             "compact_metadata" (| COMPACT-METADATA {"preserved_segment" {"head_uuid" (str (uuid.uuid4)) "anchor_uuid" (str (uuid.uuid4))
                                                                          "tail_uuid" (str (uuid.uuid4))}})}))
    ;; 要求を送るまで(実物の time_to_request_ms)= 入力ごとの hook の後。
    (setv self.request-ms (.elapsed-ms self) self.hook-ms (- self.request-ms hook-started))
    (when (> (get rule "thinking_deltas") 0)
      ;; 答えの前に考えている間の差分(thinking の block の thinking_delta — #3746 (a))。
      (stream-block self.session-id {"type" "thinking" "thinking" ""} {"type" "thinking_delta" "thinking" "..."}
                    (get rule "thinking_deltas")))
    (remember self.path text)
    (setv injections [])
    (setv words [(get rule "text")])
    (when (and (get rule "permission") self.ask)
      (setv request-id (str (uuid.uuid4)))
      (emit (assistant-line self.session-id [{"type" "tool_use" "id" "toolu_stub" "name" "Bash"
                                               "input" {"command" (.format "touch {}" (get rule "touch"))}}]
                            TOOL-CALL-USAGE))
      (emit {"type" "control_request" "request_id" request-id
             "request" {"subtype" "can_use_tool" "tool_name" "Bash"
                        "input" {"command" (.format "touch {}" (get rule "touch"))} "permission_suggestions" []}})
      (when (!= (.wait-permission self request-id injections) "allow")
        (setv (get rule "touch") None words ["DENIED"])))
    (when (and (get rule "permission") (get rule "touch"))
      (.touch (Path (get rule "touch"))))
    (when (> (get rule "tool_seconds") 0)
      ;; 道具の呼びの input は prompt の命令(実物の Bash の tool_use と同じ欄 command)・結果の content はその命令の出力(実物の Bash の
      ;; tool_result と同じく文字列 — #3744)。呼びの行の前に、命令を書いている間の差分(tool_use の block の input_json_delta —
      ;; #3746 (a))。
      (when (> (get rule "tool_input_deltas") 0)
        (stream-block self.session-id {"type" "tool_use" "id" "toolu_stub" "name" "Bash" "input" {}}
                      {"type" "input_json_delta" "partial_json" "{\"c"} (get rule "tool_input_deltas")))
      (emit (assistant-line self.session-id [{"type" "tool_use" "id" "toolu_stub" "name" "Bash"
                                               "input" (if (get rule "tool_command") {"command" (get rule "tool_command")} {})}]
                            TOOL-CALL-USAGE))
      (emit {"type" "system" "subtype" "task_started" "task_id" "stub-task" "session_id" self.session-id})
      ;; 道具の途中の subagent の行(親 = 道具の呼び・別の model と usage — 本体の最後の呼びに数えない行・#3744)。中身は thinking の
      ;; block だけにする — 本文も道具の呼びも持たないので上の層の出来事は増えない(替え玉を使う上の層の検の出来事の列を変えない)。
      (emit (assistant-line self.session-id [{"type" "thinking" "thinking" "subagent"}] SUBAGENT-USAGE SUBAGENT-MODEL "toolu_stub"))
      (setv stop (.wait-tool self (get rule "tool_seconds") injections))
      (when (is-not stop None)
        (setv queued (lfor record injections :if (.get record "uuid") (.get record "uuid")))
        (emit {"type" "control_response"
               "response" {"subtype" "success" "request_id" stop "response" {"still_queued" queued}}})
        (+= self.cost TURN-COST)
        (emit {"type" "result" "subtype" "error_during_execution" "is_error" True "terminal_reason" "aborted_tools"
               "session_id" self.session-id "total_cost_usd" self.cost "user_message_uuids" refs
               "modelUsage" (model-usage True)})
        (for [ref refs] (.lifecycle self ref "cancelled"))
        (when injections (.run-turn self injections))
        (return None))
      (emit {"type" "user" "session_id" self.session-id
             "message" {"role" "user" "content" [{"type" "tool_result" "tool_use_id" "toolu_stub" "content" (get rule "tool_output")
                                                   "is_error" False}]}}))
    (for [record injections]
      (.lifecycle self (.get record "uuid") "started")
      (.append words (get (reply-for (user-text record) (memory-of self.path)) "text")))
    (when (get rule "hook_feedback")
      ;; Stop hook が最初の答えを差し戻す(#4020)— 実物(CLI 2.1.292・旗なし)と同じ 3 行: 差し戻される答えの assistant の行 →
      ;; isSynthetic の user の行(本文「Stop hook feedback:\n<理由>」)→ system/notification(key stop-hook-error)。その後に答え直す。
      (emit (assistant-line self.session-id [{"type" "text" "text" REJECTED-ANSWER}] FINAL-CALL-USAGE))
      ;; --include-hook-events の CLI は差し戻しの前に Stop の hook の開始と応答の行を出す(exit code 2 で差し戻す hook — 理由は stderr・
      ;; ki-d8b473480303)。
      (.hook-lines self "Stop" "Stop" "" (get rule "hook_feedback") 2 "error")
      (emit {"type" "user" "session_id" self.session-id "parent_tool_use_id" None "isSynthetic" True "uuid" (str (uuid.uuid4))
             "message" {"role" "user" "content" [{"type" "text" "text" (+ "Stop hook feedback:\n" (get rule "hook_feedback"))}]}})
      (emit {"type" "system" "subtype" "notification" "key" "stop-hook-error" "text" "Stop hook error occurred · ctrl+o to see"
             "priority" "immediate" "session_id" self.session-id}))
    (.result self (.join " " words) (+ refs (lfor record injections :if (.get record "uuid") (.get record "uuid")))
             (get rule "deltas") (get rule "think_seconds") (> (get rule "tool_seconds") 0))
    (for [ref (+ refs (lfor record injections :if (.get record "uuid") (.get record "uuid")))]
      (.lifecycle self ref "completed")))

  (defn serve [self]
    (while True
      (setv record (.read-record self 60.0))
      (cond
        (and (is-not record None) (= (.get record "type") "user"))
          (do
            (.lifecycle self (.get record "uuid") "queued")
            (.run-turn self [record]))
        ;; 検だけの行(本物の claude には書かない — faults.ClaudeEmitOutsideTurn): 手番の外で本文を 1 行出す。背景の仕事の完了で
        ;; result の後の CLI が手番の外に動いた形(#517 の事故・#3672 の守り)を模す。
        (and (is-not record None) (= (.get record "type") "stub_emit_outside"))
          (emit (assistant-line self.session-id [{"type" "text" "text" "outside the turn"}] FINAL-CALL-USAGE))))))


(defn open-session [#^ list args]
  "argv → Session(断る時は stderr に実物の文を出して rc 1)。"
  (setv fresh (option args "--session-id") resumed (option args "--resume"))
  (cond
    fresh
      (do
        (when (os.path.exists (transcript-path fresh))
          (.write sys.stderr (.format "Error: Session ID {} is already in use.\n" fresh))
          (sys.exit 1))
        ;; transcript は最初の入力で作る(remember — 実物も入力の前に会話の記録を作らない)。
        (Session fresh (transcript-path fresh) (in "--permission-prompt-tool" args)))
    resumed
      (do
        (when (not (os.path.exists (transcript-path resumed)))
          (.write sys.stderr (.format "No transcript found for session ID: {}\n" resumed))
          (sys.exit 1))
        (if (in "--fork-session" args)
            (do
              (setv child (str (uuid.uuid4)))
              (setv path (transcript-path child))
              (.write-text (Path path) (.read-text (Path (transcript-path resumed)) :encoding "utf-8") :encoding "utf-8")
              (Session child path (in "--permission-prompt-tool" args)))
            (Session resumed (transcript-path resumed) (in "--permission-prompt-tool" args))))
    True
      (do
        (setv session-id (str (uuid.uuid4)))
        (Session session-id (transcript-path session-id) (in "--permission-prompt-tool" args)))))


(defn main []
  (setv args (cut sys.argv 1 None))
  (when (not-in "--input-format" args)
    ;; 冷えた続きの前の 1 回きりの命令: transcript に印を足して降りる。
    (remember (transcript-path (option args "--resume")) (.format "one-shot: {}" (option args "-p")))
    (sys.exit 0))
  (setv session (open-session args))
  ;; テスト用の env STUB_CLI_AT_START(JSON の object — lines・marker)が在れば、起動の直後(stdin を読む前)に lines の行をこの会話の
  ;; id を添えて stdout へ出し、出し終えたマークとして marker の file を作る(入力なしで起動した process が最初の入力の前に出す行を作る
  ;; テストのため)。
  (setv at-start (json.loads (.get os.environ "STUB_CLI_AT_START" "null")))
  (when at-start
    (for [record (get at-start "lines")]
      (emit (| record {"session_id" session.session-id})))
    (.touch (Path (get at-start "marker"))))
  (try
    (.serve session)
    (except [Stop] (sys.exit 0))
    (except [KeyboardInterrupt]
      (+= session.cost TURN-COST)
      (emit {"type" "result" "subtype" "error_during_execution" "is_error" True "terminal_reason" "aborted_streaming"
             "session_id" session.session-id "total_cost_usd" session.cost "modelUsage" (model-usage False)})
      (sys.exit 0))
    (finally
      ;; 終了する時に累積の額を transcript に記録する(SIGKILL では実行されない — 実物と同じ)。入力を 1 つも受けずに終了した新しい
      ;; 会話は transcript が無いので記録しない(記録を作らない — 同じ id の --session-id で再起動できる)。
      (when (os.path.exists session.path)
        (with [handle (open session.path "a" :encoding "utf-8")]
          (.write handle (+ (json.dumps {"type" "cost-state" "sessionId" session.session-id "totalCostUSD" session.cost}
                                        :separators #("," ":"))
                            "\n")))))))

(when (= __name__ "__main__")
  (main))
