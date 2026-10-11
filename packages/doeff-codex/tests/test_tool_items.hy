;; 道具の item(commandExecution・fileChange・webSearch)の行の分類の検 — 版を固定した本物の codex(0.162.1)が、偽の上流の道具の
;; 呼びに答えて命令を走らせ、ファイルを書いた行を録った物(tests/recorded/codex-0.162.1/tool-calls・録り方は
;; scripts/record_app_server_lines.py の筋書き tool-calls)で確かめる(card acp:kanban-issue:ki-77e76c7c07ce)。
;;
;; 守る事: 道具の item の始まりと終わりが、命令と出力と終了 code(commandExecution)・変えたファイル(fileChange)・検索の語(webSearch)を
;; 型つきの記録で運ぶ。崩れると上の層(doeff-agents の adapter)は codex の道具の呼びと結果を AgentToolUseEvent・AgentToolResultEvent
;; へ写せず、agora の画面の道具カードに codex の会話の道具が出ない。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest defk <-])
(import json)
(import doeff_hy.json_value [OpaqueJson])
(import doeff_codex.lines [classify-line ItemStarted ItemDone Unparsed UnknownValue ToolStatus CommandRun FilePatch WebSearch])
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
  (assert (= record.tool.status (UnknownValue :value (OpaqueJson.of "paused"))) record)
  ;; 使う欄(command)の無い道具の item は、method と訳を持つ読めない行(黙って道具の中身の無い item にしない)。
  (<- broken (command-item-line "item/started" {"type" "commandExecution" "id" "call_9" "cwd" "/w" "status" "inProgress"
                                                 "commandActions" []}))
  (<- unparsed (classify-line broken))
  (assert (isinstance unparsed Unparsed) unparsed)
  (assert (= unparsed.method "item/started") unparsed)
  (assert (in "command" unparsed.reason) unparsed))
