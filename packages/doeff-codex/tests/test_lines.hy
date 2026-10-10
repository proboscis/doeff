;; app-server の stdout の 1 行の分類の検 — 版を固定した本物の codex(0.162.1)が手元の偽の上流(Responses API の SSE)に答えた行を
;; 録った物(tests/recorded/codex-0.162.1・録り方は scripts/record_app_server_lines.py)で確かめる。
;;
;; 守る事: 答えの文字の途中(item/agentMessage/delta)が型つきの記録 TextDelta になり、ターンごとに連ねると答えの全文
;; (item/completed の agentMessage)と同じ文字になる。これが崩れると、上の層は答えの途中を画面へ流せない(決まりの元 =
;; 利用者 2026-09-10「どの会話も、文字が届くたびに 1 文字ずつ更新されない」・2026-10-10 21:27 の答え A)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest defk <- var])
(import json)
(import pathlib [Path])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_codex.lines [classify-line TextDelta AgentMessageDone ReasoningDelta TurnStarted TurnEnded TurnStatus TurnError
                           ThreadStarted TokenUsage Response ErrorResponse ServerRequest Other Unparsed ItemStarted ItemDone
                           UnknownValue])

(val RECORDED (/ (. (Path __file__) parent) "recorded" "codex-0.162.1"))


(defk recorded-records [#^ str name]
  {:pre [(: name str)] :post [(: % tuple)] :tags {:context "codex-test" :role "foundation"}}
  "録った 1 本の stdout の行を、空の行を除いて順に分類した列(検が実物の行の並びのまま読むため)。"
  (var records #())
  (for [line (.splitlines (.read-text (/ RECORDED f"{name}.stdout.jsonl") :encoding "utf-8"))]
    (when (.strip line)
      (<- record (classify-line line))
      (:= records (+ records #(record)))))
  records)


(deftest test-the-text-deltas-of-each-turn-spell-its-answer
  ;; 1 つの app-server の process で走った 2 つのターンの、答えの文字の途中が TextDelta になり、ターンごとに連ねると答えの全文になる。
  (<- records (recorded-records "two-turns"))
  (val deltas (tuple (gfor record records :if (isinstance record TextDelta) record)))
  (val answers (tuple (gfor record records :if (isinstance record AgentMessageDone) record)))
  (val turns (tuple (gfor record records :if (isinstance record TurnStarted) record.turn-id)))
  (assert (= (len turns) 2) turns)
  (assert (= (len answers) 2) answers)
  (for [answer answers]
    (val pieces (tuple (gfor delta deltas :if (= delta.turn-id answer.turn-id) delta.text)))
    (assert (= pieces #("Hel" "lo, " "wor" "ld.")) pieces)
    (assert (= (.join "" pieces) answer.text "Hello, world.") answer))
  ;; 途中の文字は、そのターンの終わりより前に届く(上の層が終わりを待たずに流せる)。
  (for [turn-id turns]
    (val positions (tuple (gfor #(index record) (enumerate records)
                                :if (and (isinstance record TextDelta) (= record.turn-id turn-id)) index)))
    (val ended (next (gfor #(index record) (enumerate records)
                           :if (and (isinstance record TurnEnded) (= record.turn-id turn-id)) index)))
    (assert (< (max positions) ended) #(positions ended))))


(deftest test-a-turn-ends-once-with-its-status
  ;; ターンの終わり(turn/completed)はターンごとにちょうど 1 つで、状態を codex の綴りの型で持つ。
  (<- two-turns (recorded-records "two-turns"))
  (val completed (tuple (gfor record two-turns :if (isinstance record TurnEnded) record)))
  (assert (= (tuple (gfor end completed end.status)) #(TurnStatus.COMPLETED TurnStatus.COMPLETED)) completed)
  (assert (all (gfor end completed (is end.error-message None))) completed)
  ;; 止めたターン(turn/interrupt)は INTERRUPTED で終わり、止める前の途中の文字は届いている。
  (<- interrupted (recorded-records "interrupt"))
  (val ends (tuple (gfor record interrupted :if (isinstance record TurnEnded) record)))
  (assert (= (tuple (gfor end ends end.status)) #(TurnStatus.INTERRUPTED)) ends)
  (assert (any (gfor record interrupted (and (isinstance record TextDelta) (= record.turn-id (. (get ends 0) turn-id))))))
  ;; 上流が 429 で断ったターンは、誤りの通知(error)の後に FAILED で終わり、HTTP の status と誤りの文を持つ。
  (<- limited (recorded-records "rate-limit"))
  (val errors (tuple (gfor record limited :if (isinstance record TurnError) record)))
  (assert (= (len errors) 1) errors)
  (assert (= (. (get errors 0) http-status) 429) errors)
  (assert (= (. (get errors 0) error-kind) "responseTooManyFailedAttempts") errors)
  (assert (is (. (get errors 0) will-retry) False) errors)
  (val failed (tuple (gfor record limited :if (isinstance record TurnEnded) record)))
  (assert (= (tuple (gfor end failed end.status)) #(TurnStatus.FAILED)) failed)
  (assert (in "429" (. (get failed 0) error-message)) failed)
  (assert (= (. (get failed 0) http-status) 429) failed))


(defk recorded-line [#^ str name #^ str method]
  {:pre [(: name str) (: method str)] :post [(: % dict)] :tags {:context "codex-test" :role "foundation"}}
  "録った 1 本の stdout から、method の最初の行を JSON の object で(版で増える値を足した行を作る元にするため)。"
  (next (gfor line (.splitlines (.read-text (/ RECORDED f"{name}.stdout.jsonl") :encoding "utf-8"))
              :if (.strip line)
              :setv message (json.loads line)
              :if (= (.get message "method") method)
              message)))


(deftest test-a-turn-ends-even-when-its-status-or-error-kind-is-unknown
  ;; 版で増えた状態・誤りの種類が来ても、ターンの終わり(TurnEnded)は作る — 落とすと上の層はターンの終わりを待ち続ける。
  ;; 知らない値は UnknownValue で中を読まずに運ぶ(既知の値や None に読み替えない)。元は録った 429 の turn/completed の行。
  (<- ended-line (recorded-line "rate-limit" "turn/completed"))
  (val turn (get ended-line "params" "turn"))
  (val drifted {#** ended-line
                "params" {#** (get ended-line "params")
                          "turn" {#** turn
                                  "status" "cancelled"
                                  "error" {#** (get turn "error") "codexErrorInfo" {"someFutureKind" {"httpStatusCode" 503}}}}}})
  (<- ended (classify-line (json.dumps drifted)))
  (assert (isinstance ended TurnEnded) ended)
  (assert (= ended.turn-id (get turn "id")) ended)
  (assert (= ended.status (UnknownValue :value (OpaqueJson.of "cancelled"))) ended)
  (assert (= ended.error-kind (UnknownValue :value (OpaqueJson.of {"someFutureKind" {"httpStatusCode" 503}}))) ended)
  (assert (is ended.http-status None) ended)
  ;; 文字列で名乗る種類はそのまま名で読む。
  (<- error-line (recorded-line "rate-limit" "error"))
  (val named {#** error-line
              "params" {#** (get error-line "params")
                        "error" {#** (get error-line "params" "error") "codexErrorInfo" "usageLimitExceeded"}}})
  (<- refused (classify-line (json.dumps named)))
  (assert (and (isinstance refused TurnError) (= refused.error-kind "usageLimitExceeded") (is refused.http-status None)) refused))


(deftest test-items-start-and-end-with-their-kind
  ;; item/started はどの item も種類と id を名乗る(上の層が「考えている」「道具を呼んでいる」を出す材料)。答えの全文でない item の
  ;; 終わりは ItemDone。全文の無い agentMessage は読めない行。
  (<- records (recorded-records "two-turns"))
  (val started (tuple (gfor record records :if (isinstance record ItemStarted) record.item-type)))
  (assert (= started #("userMessage" "agentMessage" "userMessage" "agentMessage")) started)
  (val done (tuple (gfor record records :if (isinstance record ItemDone) record.item-type)))
  (assert (= done #("userMessage" "userMessage")) done)
  (<- textless (classify-line (json.dumps {"method" "item/completed"
                                           "params" {"threadId" "t" "turnId" "u" "item" {"type" "agentMessage" "id" "i"}}})))
  (assert (and (isinstance textless Unparsed) (= textless.method "item/completed")) textless))


(deftest test-responses-name-the-thread-and-the-turn
  ;; 要求への答え(id の在る行)は要求の id を持ち、thread/start の答えは thread の id を、turn/start の答えはターンの id を名乗る —
  ;; 上の層が続き(thread/resume)と止め(turn/interrupt)に使う id を答えから読めるように。
  (<- records (recorded-records "two-turns"))
  (val responses (dfor record records :if (isinstance record Response) record.id record))
  (assert (= (sorted responses) [1 2 3 4]) responses)
  (val started (next (gfor record records :if (isinstance record ThreadStarted) record)))
  (assert (= (. (get responses 2) thread-id) started.thread-id) responses)
  (val turn-ids (tuple (gfor record records :if (isinstance record TurnStarted) record.turn-id)))
  (assert (= #((. (get responses 3) turn-id) (. (get responses 4) turn-id)) turn-ids) responses)
  ;; turn/interrupt の答えは空の result(id だけ)。
  (<- interrupted (recorded-records "interrupt"))
  (val interrupt-answer (next (gfor record interrupted :if (and (isinstance record Response) (= record.id 4)) record)))
  (assert (= #(interrupt-answer.thread-id interrupt-answer.turn-id) #(None None)) interrupt-answer))


(deftest test-token-usage-is-read-per-call-and-per-thread
  ;; thread/tokenUsage/updated は、この呼びの分(last)と thread の累積(total)を分けて名乗る。2 つ目のターンの累積は 1 つ目の 2 倍。
  (<- records (recorded-records "two-turns"))
  (val usages (tuple (gfor record records :if (isinstance record TokenUsage) record)))
  (assert (= (tuple (gfor usage usages usage.last.total-tokens)) #(14 14)) usages)
  (assert (= (tuple (gfor usage usages usage.total.total-tokens)) #(14 28)) usages)
  (assert (= (. (get usages 0) last input-tokens) 10) usages)
  (assert (= (. (get usages 0) last output-tokens) 4) usages)
  (assert (= (. (get usages 0) model-context-window) 258400) usages))


(deftest test-lines-outside-the-vocabulary-keep-their-method
  ;; 語彙の外の通知は捨てずに method の名だけを持つ(発明しない)。
  (<- records (recorded-records "two-turns"))
  (val others (tuple (gfor record records :if (isinstance record Other) record.method)))
  (assert (in "remoteControl/status/changed" others) others)
  (assert (in "account/rateLimits/updated" others) others)
  ;; 読めない行は Unparsed で、訳を持つ: 壊れた行・object でない行は method が空、語彙の中の method で形の合わない行は method を名乗る
  ;; (版の食い違いを黙って Other に混ぜない)。
  (<- broken (classify-line "not json"))
  (assert (and (isinstance broken Unparsed) (= broken.method "") broken.reason) broken)
  (<- listed (classify-line "[1, 2]"))
  (assert (and (isinstance listed Unparsed) (= listed.method "")) listed)
  (<- drifted (classify-line (json.dumps {"method" "item/agentMessage/delta" "params" {"threadId" "t" "turnId" "u" "itemId" "i"}})))
  (assert (and (isinstance drifted Unparsed) (= drifted.method "item/agentMessage/delta") (in "delta" drifted.reason)) drifted)
  ;; codex が問いを返す要求(id と method の両方が在る行 — 道具の許可など)は ServerRequest で、答えに使う id を持つ。
  ;; 中身は method ごとに形が違うので、中を読まずに運ぶ(答える側が解く)。
  (<- asked (classify-line (json.dumps {"id" 7 "method" "item/commandExecution/requestApproval" "params" {"threadId" "t"}})))
  (assert (= asked (ServerRequest :id 7 :method "item/commandExecution/requestApproval" :params (OpaqueJson.of {"threadId" "t"})))
          asked)
  ;; result も error も無い答えは読めない行(答えを発明しない)。
  (<- empty-answer (classify-line (json.dumps {"id" 8 "result" None})))
  (assert (and (isinstance empty-answer Unparsed) (in "result" empty-answer.reason)) empty-answer)
  ;; 要求への誤りの答え。
  (<- refused (classify-line (json.dumps {"id" 9 "error" {"code" -32600 "message" "bad"}})))
  (assert (= refused (ErrorResponse :id 9 :code -32600 :message "bad")) refused)
  ;; 考えている間の差分(要約と本文)は本文の差分と分けた型。
  (<- thinking (classify-line (json.dumps {"method" "item/reasoning/summaryTextDelta"
                                           "params" {"threadId" "t" "turnId" "u" "itemId" "i" "delta" "thinking"}})))
  (assert (= thinking (ReasoningDelta :thread-id "t" :turn-id "u" :item-id "i" :text "thinking" :summary True)) thinking))


(deftest test-exec-json-carries-no-text-deltas
  ;; 起動の形を app-server に決めた根拠の実測: 同じ版の `codex exec --json` は答えを item.completed で丸ごと 1 行に出し、文字の途中の行を
  ;; 出さない。この版で exec が途中を出すようになったら、形を決め直す。
  (val lines (tuple (gfor line (.splitlines (.read-text (/ RECORDED "exec-json.stdout.jsonl") :encoding "utf-8")) :if (.strip line)
                          (json.loads line))))
  (assert (not (any (gfor line lines (in "delta" (.lower (get line "type")))))) lines)
  (assert (in {"id" "item_1" "type" "agent_message" "text" "Hello, world."}
              (lfor line lines :if (= (get line "type") "item.completed") (get line "item")))
          lines))
