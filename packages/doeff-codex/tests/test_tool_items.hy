;; 道具の item(commandExecution・fileChange・webSearch と、mcpToolCall・dynamicToolCall・imageView・collabAgentToolCall)の行の分類の検 — 版を固定した本物の codex(0.162.1)が、偽の上流の道具の
;; 呼びに答えて命令を走らせ、ファイルを書いた行を録った物(tests/recorded/codex-0.162.1/tool-calls・録り方は
;; scripts/record_app_server_lines.py の筋書き tool-calls)で確かめる(ki-77e76c7c07ce)。
;;
;; 守る事: 道具の item の始まりと終わりが、命令と出力と終了 code(commandExecution)・変えたファイル(fileChange)・検索の語(webSearch)を
;; 型つきの記録で運ぶ。崩れると上の層(doeff-agents の adapter)は codex の道具の呼びと結果を AgentToolUseEvent・AgentToolResultEvent
;; へ写せず、その上の画面の道具カードに codex の道具が出ない。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest defk <-])
(import json)
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.frozen [freeze-json])
(import doeff_codex [ItemStarted ItemDone ToolStatus CommandRun FilePatch WebSearch McpCall DynamicCall AgentCall ImageView]
        doeff_codex._lines [classify-line TurnStarted Unparsed UnknownValue AgentState])
(import tests.test_lines [recorded-records])


(defk tool-items [#^ tuple records #^ type kind]
  {:pre [(: records tuple) (: kind type)] :post [(: % tuple)] :tags {:context "codex-test" :role "foundation"}}
  "行の記録の列から、道具の中身を持つ item の始まり(ItemStarted)か終わり(ItemDone)を順に取り出すため。"
  (tuple (gfor record records :if (and (isinstance record kind) (is-not record.tool None)) record)))


(deftest test-tool-items-carry-the-command-the-patch-and-the-search
  (<- records (recorded-records "tool-calls"))
  ;; 録った行は、どれも読めない行にならない(道具の item の形がこの版の型に合う)。
  (assert (not (any (gfor record records (isinstance record Unparsed)))) records)
  (<- started (tool-items records ItemStarted))
  (<- done (tool-items records ItemDone))
  (val order #(#("call_0" "commandExecution") #("call_1" "commandExecution") #("call_2" "fileChange") #("ws_1" "webSearch")))
  (assert (= (tuple (gfor record started #(record.item-id record.item-type))) order) started)
  (assert (= (tuple (gfor record done #(record.item-id record.item-type))) order) done)
  ;; どの道具の item も、始まりが終わりより前に届く。
  (for [#(item-id _) order]
    (val begun (next (gfor #(index record) (enumerate records) :if (and (isinstance record ItemStarted) (= record.item-id item-id)) index)))
    (val ended (next (gfor #(index record) (enumerate records) :if (and (isinstance record ItemDone) (= record.item-id item-id)) index)))
    (assert (< begun ended) #(item-id begun ended)))
  (val echo-start (. (get started 0) tool))
  (val fail-start (. (get started 1) tool))
  (val patch-start (. (get started 2) tool))
  (val search-start (. (get started 3) tool))
  (val echo-done (. (get done 0) tool))
  (val fail-done (. (get done 1) tool))
  (val patch-done (. (get done 2) tool))
  (val search-done (. (get done 3) tool))
  ;; 命令: 始まりは命令の文(codex が包んだ形のまま)と dir を持ち、出力と終了 code は終わりで届く。
  (assert (isinstance echo-start CommandRun) echo-start)
  (assert (= #(echo-start.command echo-start.status echo-start.output echo-start.exit-code)
             #("/bin/bash -lc 'echo tool-out'" ToolStatus.IN-PROGRESS None None))
          echo-start)
  (assert (.endswith echo-start.cwd "/cwd") echo-start)
  (assert (= #(echo-done.command echo-done.cwd) #(echo-start.command echo-start.cwd)) echo-done)
  (assert (= #(echo-done.status echo-done.output echo-done.exit-code) #(ToolStatus.COMPLETED "tool-out\n" 0)) echo-done)
  ;; 失敗した命令(終了 code 2)は状態 failed で終わり、標準誤りの出力を運ぶ。
  (assert (= fail-start.command "/bin/bash -lc 'ls no-such-file'") fail-start)
  (assert (= #(fail-done.status fail-done.exit-code fail-done.output)
             #(ToolStatus.FAILED 2 "ls: cannot access 'no-such-file': No such file or directory\n"))
          fail-done)
  ;; ファイルの変更(apply_patch): 始まりから変えるファイルと中身を持つ。
  (assert (and (isinstance patch-start FilePatch) (isinstance patch-done FilePatch)) #(patch-start patch-done))
  (assert (= #(patch-start.status patch-done.status) #(ToolStatus.IN-PROGRESS ToolStatus.COMPLETED)) #(patch-start patch-done))
  (assert (= patch-start.changes patch-done.changes) #(patch-start patch-done))
  (assert (= (len patch-done.changes) 1) patch-done)
  (val edit (get patch-done.changes 0))
  (assert (= #(edit.kind edit.diff edit.move-path) #("add" "hello\n" None)) edit)
  (assert (.endswith edit.path "/cwd/note.txt") edit)
  ;; web 検索: 始まりは語を持たず(上流は語を検索の終わりで名乗る)、終わりが語と動きの種類を持つ。
  (assert (and (isinstance search-start WebSearch) (isinstance search-done WebSearch)) #(search-start search-done))
  (assert (= #(search-start.query search-start.action) #("" "other")) search-start)
  (assert (= #(search-done.query search-done.action search-done.url search-done.pattern) #("doeff effects" "search" None None))
          search-done)
  ;; 道具でない item(利用者の発言・答え)は道具の中身を持たない。
  (val plain (tuple (gfor record records :if (and (isinstance record ItemStarted) (is record.tool None)) record.item-type)))
  (assert (= plain #("userMessage" "agentMessage")) plain))


(defk command-item-line [#^ str method #^ dict item]
  {:pre [(: method str) (: item dict)] :post [(: % str)] :tags {:context "codex-test" :role "foundation"}}
  "item の通知の 1 行を作るため(録った行に無い形 — 版で増えた状態の綴り・欠けた欄 — を当てる)。"
  (json.dumps {"method" method "params" {"threadId" "thread-1" "turnId" "turn-1" "item" item}}))


(deftest test-a-tool-item-keeps-an-unknown-status-and-refuses-a-missing-field
  ;; 版で増えた状態の綴りは UnknownValue で運ぶ(既知の状態に読み替えない・行も落とさない)。
  (<- line (command-item-line "item/completed" {"type" "commandExecution" "id" "call_9" "command" "ls" "cwd" "/w" "status" "paused"
                                                 "commandActions" [] "aggregatedOutput" "" "exitCode" None}))
  (<- record (classify-line line))
  (assert (isinstance record ItemDone) record)
  (val run record.tool)
  (assert (isinstance run CommandRun) run)
  (assert (= run.status (UnknownValue :value (OpaqueJson.of "paused"))) run)
  ;; 使う欄(command)の無い道具の item は、method と訳を持つ読めない行(黙って道具の中身の無い item にしない)。
  (<- broken (command-item-line "item/started" {"type" "commandExecution" "id" "call_9" "cwd" "/w" "status" "inProgress"
                                                 "commandActions" []}))
  (<- unparsed (classify-line broken))
  (assert (isinstance unparsed Unparsed) unparsed)
  (assert (= unparsed.method "item/started") unparsed)
  (assert (in "command" unparsed.reason) unparsed))


(deftest test-other-tool-items-carry-the-mcp-call-the-dynamic-call-the-image-and-the-agent-calls
  ;; 失敗ケース(card ki-c09b93b72bff): 版を固定した本物の codex(0.162.1)が、偽の上流の呼びに答えて録りの MCP の server の道具・
  ;; thread に宣言した動的な道具・画像を見る道具・子の agent を始めて待つ道具を走らせた行(tests/recorded/codex-0.162.1/other-tools・
  ;; 録り方は scripts/record_app_server_lines.py の筋書き other-tools)。どの item も道具の中身を型つきの記録で運ぶ(崩れると上の層は
  ;; この 4 種の呼びと結果を道具の出来事へ写せず、画面の道具カードに出ない)。
  (<- records (recorded-records "other-tools"))
  (assert (not (any (gfor record records (isinstance record Unparsed)))) records)
  (<- started (tool-items records ItemStarted))
  (<- done (tool-items records ItemDone))
  (val order #(#("call_other_0" "mcpToolCall") #("call_other_1" "dynamicToolCall") #("call_other_2" "imageView")
               #("call_other_3" "collabAgentToolCall") #("call_other_4" "collabAgentToolCall")))
  (assert (= (tuple (gfor record started #(record.item-id record.item-type))) order) started)
  (assert (= (tuple (gfor record done #(record.item-id record.item-type))) order) done)
  (val mcp-start (. (get started 0) tool))
  (val dynamic-start (. (get started 1) tool))
  (val image-start (. (get started 2) tool))
  (val spawn-start (. (get started 3) tool))
  (val wait-start (. (get started 4) tool))
  (val mcp-done (. (get done 0) tool))
  (val dynamic-done (. (get done 1) tool))
  (val image-done (. (get done 2) tool))
  (val spawn-done (. (get done 3) tool))
  (val wait-done (. (get done 4) tool))
  ;; MCP の道具: server と道具の名と引数(凍らせた JSON のまま)は始まりから・結果の文は終わりで届く。
  (assert (= mcp-start (McpCall :server "recorder" :tool "echo" :arguments (freeze-json {"text" "hi"}) :status ToolStatus.IN-PROGRESS))
          mcp-start)
  (assert (= mcp-done (McpCall :server "recorder" :tool "echo" :arguments (freeze-json {"text" "hi"}) :status ToolStatus.COMPLETED
                               :output "echo: hi"))
          mcp-done)
  ;; 動的な道具: 答え手(録りでは録りの手順 — item/tool/call の要求に答えた)の答えの文と成否は終わりで届く。
  (assert (= dynamic-start (DynamicCall :tool "lookup_ticket" :arguments (freeze-json {"id" "T-1"}) :status ToolStatus.IN-PROGRESS))
          dynamic-start)
  (assert (= dynamic-done (DynamicCall :tool "lookup_ticket" :arguments (freeze-json {"id" "T-1"}) :status ToolStatus.COMPLETED
                                       :output "ticket T-1: open" :success True))
          dynamic-done)
  ;; 画像を見る道具: 始まりも終わりも画像の path だけ(codex 0.162.1 の item は状態も結果も持たない)。
  (assert (and (isinstance image-start ImageView) (= image-start image-done)) #(image-start image-done))
  (assert (.endswith image-start.path "/cwd/pixel.png") image-start)
  ;; 子の agent を始める: 頼みは始まりから・子の thread の id と model と子の状態は終わりで届く(始まりの model の空の綴りは None)。
  (assert (= spawn-start (AgentCall :tool "spawnAgent" :status ToolStatus.IN-PROGRESS :prompt "SUB-AGENT say hi")) spawn-start)
  (val child (get spawn-done.receivers 0))
  (assert (= spawn-done (AgentCall :tool "spawnAgent" :status ToolStatus.COMPLETED :receivers #(child) :prompt "SUB-AGENT say hi"
                                   :model "gpt-mock" :states #((AgentState :thread-id child :status "pendingInit"))))
          spawn-done)
  ;; 子を待つ: 待つ相手は始まりから・子の最後の答えは終わりで届く。
  (assert (= wait-start (AgentCall :tool "wait" :status ToolStatus.IN-PROGRESS :receivers #(child))) wait-start)
  (assert (= wait-done (AgentCall :tool "wait" :status ToolStatus.COMPLETED :receivers #(child)
                                  :states #((AgentState :thread-id child :status "completed" :message "Sub done."))))
          wait-done)
  ;; 子の agent のターンは子の thread を名乗って同じ stdout に混ざる(handler が親のターンと分ける — test_sub_agent_threads.hy)。
  (val child-turns (tuple (gfor record records :if (and (isinstance record TurnStarted) (= record.thread-id child)) record.turn-id)))
  (assert (= (len child-turns) 1) child-turns))


(deftest test-an-agent-call-keeps-the-interrupted-status
  ;; 子の agent の道具の状態 interrupted(codex 0.162.1 の CollabAgentToolCallStatus — 命令・ファイルの変更の状態には無い)は、
  ;; 道具の状態の閉じた語彙で読む(この版に無い綴りの UnknownValue にしない)。
  (<- line (command-item-line "item/completed" {"type" "collabAgentToolCall" "id" "call_9" "tool" "wait" "status" "interrupted"
                                                 "senderThreadId" "thread-1" "receiverThreadIds" ["child-1"] "prompt" None
                                                 "model" None "reasoningEffort" None "agentsStates" {}}))
  (<- record (classify-line line))
  (assert (isinstance record ItemDone) record)
  (assert (= record.tool (AgentCall :tool "wait" :status ToolStatus.INTERRUPTED :receivers #("child-1"))) record))
