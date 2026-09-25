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
;;; - stdin の EOF で降りる。stdin を開いたまま result の後も生きる(温かい — 降ろすのは host の仕事)。
;;; - --input-format の無い -p <prompt>(冷えた続きの前の 1 回きりの命令)は transcript に印を 1 行足して rc 0。
(import json)
(import os)
(import os.path)
(import pathlib [Path])
(import re)
(import select)
(import sys)
(import uuid)

(.insert sys.path 0 (str (. (Path __file__) (resolve) parent parent)))
(import scenario_rules [reply-for])

(setv CAPABILITIES ["msg_lifecycle_v1" "interrupt_receipt_v1"])


(defclass Stop [Exception] "stdin の EOF(降りる)。")


(defn emit [#^ dict record]
  (.write sys.stdout (+ (json.dumps record :ensure-ascii False) "\n"))
  (.flush sys.stdout))

(defn option [#^ list args #^ str name]
  (if (in name args) (get args (+ (.index args name) 1)) None))

(defn transcript-path [#^ str session-id]
  (setv config (get os.environ "CLAUDE_CONFIG_DIR"))
  (setv mangled (re.sub "[^A-Za-z0-9]" "-" (os.path.realpath (os.getcwd))))
  (.format "{}/projects/{}/{}.jsonl" config mangled session-id))

(defn memory-of [#^ str path]
  (if (os.path.exists path)
      (tuple (gfor line (.splitlines (.read-text (Path path) :encoding "utf-8")) :if (.strip line)
                   (.get (json.loads line) "text" "")))
      #()))

(defn remember [#^ str path #^ str text]
  (os.makedirs (os.path.dirname path) :exist-ok True)
  (with [handle (open path "a" :encoding "utf-8")]
    (.write handle (+ (json.dumps {"type" "user" "text" text} :ensure-ascii False) "\n"))))

(defn user-text [#^ dict record]
  (setv content (.get (.get record "message" {}) "content"))
  (if (isinstance content list)
      (.join "" (gfor part content :if (isinstance part dict) (.get part "text" "")))
      (str (or content ""))))


(defclass Session []
  (defn __init__ [self #^ str session-id #^ str path #^ bool ask]
    (setv self.session-id session-id self.path path self.ask ask
          self.pending b""))

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

  (defn result [self #^ str text #^ list refs]
    (emit {"type" "assistant" "session_id" self.session-id
           "message" {"role" "assistant" "content" [{"type" "text" "text" text}]}})
    (emit {"type" "result" "subtype" "success" "is_error" False "result" text "terminal_reason" "completed"
           "session_id" self.session-id "total_cost_usd" 0.0 "usage" {"input_tokens" 1 "output_tokens" 1}
           "user_message_uuids" refs}))

  (defn run-turn [self #^ list records]
    "1 手番(records = この手番の入力の行 — 普通は 1 つ・生き残った注入の手番は複数)。"
    (setv refs (lfor record records :if (.get record "uuid") (.get record "uuid")))
    (for [ref refs] (.lifecycle self ref "started"))
    (emit {"type" "system" "subtype" "init" "session_id" self.session-id "capabilities" CAPABILITIES
           "model" "stub" "permissionMode" (if self.ask "default" "bypassPermissions") "mcp_servers" []})
    (setv text (.join "\n" (gfor record records (user-text record))))
    (setv rule (reply-for text (memory-of self.path)))
    (remember self.path text)
    (setv injections [])
    (setv words [(get rule "text")])
    (when (and (get rule "permission") self.ask)
      (setv request-id (str (uuid.uuid4)))
      (emit {"type" "assistant" "session_id" self.session-id
             "message" {"role" "assistant" "content" [{"type" "tool_use" "id" "toolu_stub" "name" "Bash"
                                                        "input" {"command" (.format "touch {}" (get rule "touch"))}}]}})
      (emit {"type" "control_request" "request_id" request-id
             "request" {"subtype" "can_use_tool" "tool_name" "Bash"
                        "input" {"command" (.format "touch {}" (get rule "touch"))} "permission_suggestions" []}})
      (when (!= (.wait-permission self request-id injections) "allow")
        (setv (get rule "touch") None words ["DENIED"])))
    (when (and (get rule "permission") (get rule "touch"))
      (.touch (Path (get rule "touch"))))
    (when (> (get rule "tool_seconds") 0)
      (emit {"type" "assistant" "session_id" self.session-id
             "message" {"role" "assistant" "content" [{"type" "tool_use" "id" "toolu_stub" "name" "Bash" "input" {}}]}})
      (emit {"type" "system" "subtype" "task_started" "task_id" "stub-task" "session_id" self.session-id})
      (setv stop (.wait-tool self (get rule "tool_seconds") injections))
      (when (is-not stop None)
        (setv queued (lfor record injections :if (.get record "uuid") (.get record "uuid")))
        (emit {"type" "control_response"
               "response" {"subtype" "success" "request_id" stop "response" {"still_queued" queued}}})
        (emit {"type" "result" "subtype" "error_during_execution" "is_error" True "terminal_reason" "aborted_tools"
               "session_id" self.session-id "user_message_uuids" refs})
        (for [ref refs] (.lifecycle self ref "cancelled"))
        (when injections (.run-turn self injections))
        (return None))
      (emit {"type" "user" "session_id" self.session-id
             "message" {"role" "user" "content" [{"type" "tool_result" "tool_use_id" "toolu_stub" "content" ""}]}}))
    (for [record injections]
      (.lifecycle self (.get record "uuid") "started")
      (.append words (get (reply-for (user-text record) (memory-of self.path)) "text")))
    (.result self (.join " " words) (+ refs (lfor record injections :if (.get record "uuid") (.get record "uuid"))))
    (for [ref (+ refs (lfor record injections :if (.get record "uuid") (.get record "uuid")))]
      (.lifecycle self ref "completed")))

  (defn serve [self]
    (while True
      (setv record (.read-record self 60.0))
      (when (and (is-not record None) (= (.get record "type") "user"))
        (.lifecycle self (.get record "uuid") "queued")
        (.run-turn self [record])))))


(defn open-session [#^ list args]
  "argv → Session(断る時は stderr に実物の文を出して rc 1)。"
  (setv fresh (option args "--session-id") resumed (option args "--resume"))
  (cond
    fresh
      (do
        (when (os.path.exists (transcript-path fresh))
          (.write sys.stderr (.format "Error: Session ID {} is already in use.\n" fresh))
          (sys.exit 1))
        (setv path (transcript-path fresh))
        (os.makedirs (os.path.dirname path) :exist-ok True)
        (.touch (Path path))
        (Session fresh path (in "--permission-prompt-tool" args)))
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
  (try
    (.serve session)
    (except [Stop] (sys.exit 0))
    (except [KeyboardInterrupt]
      (emit {"type" "result" "subtype" "error_during_execution" "is_error" True "terminal_reason" "aborted_streaming"
             "session_id" session.session-id})
      (sys.exit 0))))

(when (= __name__ "__main__")
  (main))
