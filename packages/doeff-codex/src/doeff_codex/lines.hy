;;; codex の app-server の stdout の 1 行(JSON-RPC の message 1 つ)を、上の層が読む型つきの記録へ分ける読み手。
;;;
;;; 分類(classify-line)は効果を持たない: 1 行 → CodexLine。JSON の境目はこの file の 1 か所 — 行は defwire の型で解いて確かめ、
;;; 上の層は記録の型だけを読む(生の dict を読み直す 2 か所目を作らない)。形は codex 0.162.1 の実物の行(tests/recorded/codex-0.162.1)
;;; と、同じ版の `codex app-server generate-json-schema` の定義に合わせた。wire の型は使う欄だけを持ち、知らない欄は読み捨てる
;;; (codex は版ごとに欄を足す — 足された欄で読めなくならないように)。使う欄が無い・型が違う行は Unparsed にし、訳を残す(黙って
;;; Other に混ぜない — 版の食い違いを上の層が数えられるように)。
;;;
;;; 行の 4 つの形(JSON-RPC): 要求への答え = id と result / 要求への誤りの答え = id と error / 通知 = method と params /
;;; codex からの要求 = id と method と params(道具の許可の問いなど — 上の層が同じ id で答える)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex" :role "foundation"})
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defenum defrecord defwire])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import re)
(import doeff_hy.frozen [FrozenJson freeze-json-text])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.wire [parse parse-json Malformed])

;; ターンの状態(turn/started・turn/completed の turn.status の綴り — codex 0.162.1 の TurnStatus)。
(defenum TurnStatus COMPLETED INTERRUPTED FAILED (IN-PROGRESS "inProgress"))
;; 口座の利用枠切れの誤りの種類(codexErrorInfo の綴り — codex 0.162.1)。綴りの定義はここ 1 か所(handler の判定と fake の終わりが読む)。
(val USAGE-LIMIT-KIND "usageLimitExceeded")
;; code mode が使えない警告の文の頭と、道具の呼びを閉じた(fail closed)と名乗る句(codex 0.162.1 の codex-rs/core の
;; CodeModeService.take_unavailable_warning — 「Code Mode is unavailable because <訳>. <振る舞い>; enable …」)。振る舞いは道具の出し方で
;; 2 つ: code mode だけで道具を呼ぶ形(gpt-6-astra)は「Code mode will fail closed」で道具を 1 つも呼べず、code mode と直の道具の両方を
;; 持つ形は「Falling back to direct tools」で直の道具に戻る(道具は使える)。codex の文を照らすのはこの 2 つの綴りだけ(warning-of)。
(val CODE-MODE-UNAVAILABLE-HEAD "Code Mode is unavailable because ")
(val CODE-MODE-FAIL-CLOSED "Code mode will fail closed")

;; 道具の item の状態(道具の item の status の綴り — codex 0.162.1 の CommandExecutionStatus と PatchApplyStatus は completed・failed・
;; declined・inProgress、McpToolCallStatus と DynamicToolCallStatus はそのうち 3 つ、CollabAgentToolCallStatus は declined の代わりに
;; interrupted)。
(defenum ToolStatus COMPLETED FAILED DECLINED INTERRUPTED (IN-PROGRESS "inProgress"))


;; --- 読む行の wire の型(使う欄だけ・知らない欄は読み捨てる) ---------------------------------------

(defwire RpcErrorWire
  "要求への誤りの答えの error(code と文 — JSON-RPC ではどちらも必ず在る。欠けた答えは形の合わない行)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ int code)
  (#^ str message))

(defwire EnvelopeWire
  "JSON-RPC の message 1 つの外枠。params と result は method ごとに形が違うので、中を読まずに運び、method で選んだ型で解き直す。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| int str None) id None)
  (setv #^ (| str None) method None)
  (setv #^ (| OpaqueJson None) params None)
  (setv #^ (| OpaqueJson None) result None)
  (setv #^ (| RpcErrorWire None) error None))

(defwire IdWire
  "id だけを読む入れ子の欄(thread・turn)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str id))

(defwire ResultWire
  "要求への答えの result のうち、上の層が使う欄(thread/start・thread/resume の thread、turn/start の turn)。どちらも無い答えも在る。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| IdWire None) thread None)
  (setv #^ (| IdWire None) turn None))

(defwire ThreadParamsWire
  "thread/started の params。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ IdWire thread))

(defwire HttpFailureWire
  "誤りの種類のうち HTTP の失敗を名乗る物の中身(status は無いこともある)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| int None) http-status-code None))

(defwire ErrorInfoWire
  "codexErrorInfo の object の形(codex 0.162.1 の CodexErrorInfo — 鍵ちょうど 1 つが誤りの種類を名乗る)。知らない鍵(版で増えた
   種類)と鍵の無い object はこの型に解けず、TurnErrorWire の欄の OpaqueJson の側へ落ちて UnknownValue として運ばれる — 知らない
   種類を「種類なし」に読み替えず、行も落とさない(ターンの終わりを失わない)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject
   :check [(= 1 (sum (gfor kind [http-connection-failed response-stream-connection-failed response-stream-disconnected
                                 response-too-many-failed-attempts active-turn-not-steerable]
                           (is-not kind None))))]}
  (setv #^ (| HttpFailureWire None) http-connection-failed None)
  (setv #^ (| HttpFailureWire None) response-stream-connection-failed None)
  (setv #^ (| HttpFailureWire None) response-stream-disconnected None)
  (setv #^ (| HttpFailureWire None) response-too-many-failed-attempts None)
  (setv #^ (| OpaqueJson None) active-turn-not-steerable None))

(defwire TurnErrorWire
  "ターンの誤り(error の通知の error と、turn/completed の turn.error)。codexErrorInfo は種類の名の文字列か ErrorInfoWire の object で、
   版で増えた種類も在るので、ここでは中を読まずに OpaqueJson で受け、error-facts-of が順に解く(union に OpaqueJson を混ぜると、何でも
   受ける OpaqueJson の側が既知の値まで取る — pydantic の union の選び方・2026-10-10 の検で実測)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str message)
  (setv #^ (| OpaqueJson None) codex-error-info None))

(defwire ErrorKindNameWire
  "codexErrorInfo が種類の名の文字列で来た時に、その文字列を解くための包み(error-facts-of が {\"kind\": 値} に包んで解く)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str kind))

(defwire TurnWire
  "turn/started・turn/completed の turn。status は綴りのまま読み、TurnStatus に無い綴り(版で増えた状態)は記録の側で UnknownValue に
   する — 知らない状態でターンの終わりの行を落とさない。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str id)
  (#^ str status)
  (setv #^ (| TurnErrorWire None) error None))

(defwire TurnParamsWire
  "turn/started・turn/completed の params。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str thread-id)
  (#^ TurnWire turn))

(defwire DeltaParamsWire
  "差分の通知(item/agentMessage/delta・item/reasoning/textDelta・item/reasoning/summaryTextDelta)の params。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str delta))

(defwire ItemWire
  "item/started・item/completed の item のうち、どの種類も持つ欄(種類と id と、答えの全文 — agentMessage だけが text を持つ)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str type)
  (#^ str id)
  (setv #^ (| str None) text None))

(defwire ItemParamsWire
  "item/started・item/completed の params。item は種類ごとに形が違うので中を読まずに運び、どの種類も持つ欄(ItemWire)と、道具の
   種類の中身(種類で選んだ下の wire の型)を順に解く。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ OpaqueJson item))

(defwire CommandExecutionWire
  "item の種類 commandExecution(命令を走らせる道具)の中身のうち使う欄。aggregatedOutput・exitCode は命令が終わるまで null。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str command)
  (#^ str cwd)
  (#^ str status)
  (setv #^ (| str None) aggregated-output None)
  (setv #^ (| int None) exit-code None))

(defwire PatchKindWire
  "fileChange の 1 つの変更の種類(PatchChangeKind — type は add・delete・update、update は移す先 move_path を持てる)。この object
   だけ欄の名が snake(codex 0.162.1 の schema のまま)。"
  {:tags {:context "codex" :role "type"} :names :snake :unknown :ignore}
  (#^ str type)
  (setv #^ (| str None) move-path None))

(defwire FileUpdateWire
  "fileChange の 1 つのファイルの変更(FileUpdateChange)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str path)
  (#^ PatchKindWire kind)
  (#^ str diff))

(defwire FileChangeWire
  "item の種類 fileChange(ファイルを変える道具 — apply_patch)の中身のうち使う欄。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ (get tuple #(FileUpdateWire ...)) changes)
  (#^ str status))

(defwire WebSearchActionWire
  "webSearch の動き(WebSearchAction — type は search・openPage・findInPage・other、種類ごとに語の列・頁・頁の中の語を持てる)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str type)
  (setv #^ (| (get tuple #(str ...)) None) queries None)
  (setv #^ (| str None) url None)
  (setv #^ (| str None) pattern None))

(defwire WebSearchWire
  "item の種類 webSearch(web 検索の道具)の中身のうち使う欄。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str query)
  (setv #^ (| WebSearchActionWire None) action None))

(defwire McpContentWire
  "mcpToolCall の結果の 1 つの block(MCP の content — type は text・image・resource など。text の block だけが text を持つ)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str type)
  (setv #^ (| str None) text None))

(defwire McpResultWire
  "mcpToolCall の結果(McpToolCallResult)のうち使う欄。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ (get tuple #(McpContentWire ...)) content))

(defwire McpErrorWire
  "mcpToolCall の誤り(McpToolCallError)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str message))

(defwire McpToolCallWire
  "item の種類 mcpToolCall(MCP の server の道具の呼び)の中身のうち使う欄。arguments は道具が決めた形の JSON(中を読まずに運ぶ)・
   result と error は呼びが終わるまで null。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str server)
  (#^ str tool)
  (#^ str status)
  (#^ OpaqueJson arguments)
  (setv #^ (| McpResultWire None) result None)
  (setv #^ (| McpErrorWire None) error None))

(defwire DynamicContentWire
  "dynamicToolCall の答えの 1 つの項目(DynamicToolCallOutputContentItem — type は inputText・inputImage・inputAudio。inputText だけが
   text を持つ)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str type)
  (setv #^ (| str None) text None))

(defwire DynamicToolCallWire
  "item の種類 dynamicToolCall(thread に宣言した動的な道具の呼び)の中身のうち使う欄。arguments は道具が決めた形の JSON・
   contentItems と success は答え手が答えるまで null。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str tool)
  (setv #^ (| str None) namespace None)
  (#^ str status)
  (#^ OpaqueJson arguments)
  (setv #^ (| (get tuple #(DynamicContentWire ...)) None) content-items None)
  (setv #^ (| bool None) success None))

(defwire AgentStateWire
  "collabAgentToolCall の子の agent 1 つの最後の状態(CollabAgentState)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str status)
  (setv #^ (| str None) message None))

(defwire CollabAgentToolCallWire
  "item の種類 collabAgentToolCall(子の agent を始める・頼む・待つ・閉じる道具)の中身のうち使う欄。agentsStates は子の thread の
   id → 最後の状態(codex 0.162.1 の schema のまま — 鍵は thread の id)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str tool)
  (#^ str status)
  (#^ (get tuple #(str ...)) receiver-thread-ids)
  (setv #^ (| str None) prompt None)
  (setv #^ (| str None) model None)
  (#^ (get dict #(str AgentStateWire)) agents-states))

(defwire ImageViewWire
  "item の種類 imageView(作業の dir の画像を model に見せる道具)の中身。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str path))

(defwire ErrorParamsWire
  "error の通知の params(ターンの誤り — willRetry が偽なら codex はこのターンを繰り返さない)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ bool will-retry)
  (#^ TurnErrorWire error))

(defwire WarningParamsWire
  "warning の通知の params(codex 0.162.1 の WarningNotification — 利用者へ見せる短い文と、名指す thread〔無いこともある〕。ターンは
   名乗らない)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| str None) thread-id None)
  (#^ str message))

(defwire TokenCountWire
  "token の数の組(名乗らない欄は None — 0 を発明しない)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| int None) total-tokens None)
  (setv #^ (| int None) input-tokens None)
  (setv #^ (| int None) cached-input-tokens None)
  (setv #^ (| int None) output-tokens None)
  (setv #^ (| int None) reasoning-output-tokens None))

(defwire TokenUsageWire
  "thread/tokenUsage/updated の tokenUsage。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ TokenCountWire total)
  (#^ TokenCountWire last)
  (setv #^ (| int None) model-context-window None))

(defwire TokenUsageParamsWire
  "thread/tokenUsage/updated の params。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ TokenUsageWire token-usage))

(defwire RateLimitWindowWire
  "口座の利用枠の窓 1 つ(codex 0.162.1 の RateLimitWindow — 使った割合は必ず在り、長さと戻りの刻は無いこともある)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ int used-percent)
  (setv #^ (| int None) window-duration-mins None)
  (setv #^ (| int None) resets-at None))

(defwire RateLimitSnapshotWire
  "account/rateLimits/updated の rateLimits のうち使う欄(枠の名と 2 つの窓 — 名乗らない窓は null)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| str None) limit-id None)
  (setv #^ (| RateLimitWindowWire None) primary None)
  (setv #^ (| RateLimitWindowWire None) secondary None))

(defwire RateLimitsParamsWire
  "account/rateLimits/updated の params(thread もターンも名乗らない — process の口座の通知)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ RateLimitSnapshotWire rate-limits))


;; --- 記録の型(上の層が読む) ---------------------------------------------------------------------

(defrecord UnknownValue
  "この版の語彙に無い値(版で増えた状態・誤りの種類)を、中を読まずに運ぶ: value = codex が出した値そのまま。知らない値を既知の値や
   None に読み替えず、行も落とさない — 上の層が名指して数えられるように。"
  {:tags {:context "codex" :role "type"}}
  (#^ OpaqueJson value))

(defrecord Response
  "要求への答え: id = 要求の id / thread-id = 答えが名乗る thread の id(thread/start・thread/resume)/ turn-id = 答えが名乗るターンの
   id(turn/start)。名乗らない答え(initialize・turn/interrupt)は None。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| int str) id)
  (setv #^ (| str None) thread-id None)
  (setv #^ (| str None) turn-id None))

(defrecord ErrorResponse
  "要求への誤りの答え: id = 要求の id / code = JSON-RPC の誤りの code / message = 誤りの文。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| int str) id)
  (#^ int code)
  (#^ str message))

(defrecord ServerRequest
  "codex からの要求(道具の許可の問いなど): id = 答えに使う id / method = 要求の名 / params = 要求の中身(method ごとに形が違うので
   中を読まずに運ぶ — 答える側が method で選んだ型で解く。無ければ None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| int str) id)
  (#^ str method)
  (setv #^ (| OpaqueJson None) params None))

(defrecord ThreadStarted
  "thread が開いた(thread/started)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id))

(defrecord TurnStarted
  "ターンが始まった(turn/started)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id))

(defrecord TextDelta
  "答えの文字の途中(item/agentMessage/delta): text = 前の差分の後ろに続く文字。同じ item の差分を順に連ねると、その item の
   AgentMessageDone の text になる。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str text))

(defrecord ReasoningDelta
  "考えている間の差分: summary = 要約の差分(item/reasoning/summaryTextDelta)なら真、本文の差分(item/reasoning/textDelta)なら偽。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str text)
  (#^ bool summary))

(defrecord AgentMessageDone
  "答えの全文(item/completed の agentMessage)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str text))

(defrecord CommandRun
  "道具の item commandExecution の中身(命令を走らせる): command = codex が走らせた命令の文(codex が包んだ形のまま — 例
   `/bin/bash -lc 'echo hi'`)/ cwd = 走らせた dir / status = 状態(この版に無い綴りは UnknownValue)/ output = 標準出力と標準誤りを
   合わせた出力(aggregatedOutput — 終わるまで None)/ exit-code = 終了 code(終わるまで None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str command)
  (#^ str cwd)
  (#^ (| ToolStatus UnknownValue) status)
  (setv #^ (| str None) output None)
  (setv #^ (| int None) exit-code None))

(defrecord FileEdit
  "fileChange の 1 つのファイルの変更: path = ファイル / kind = 変更の種類の名(add・delete・update — 版で増えるので名のまま)/
   diff = 変更の中身(codex の綴りのまま — add は新しい中身・update は unified diff)/ move-path = update で移す先(ほかは None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str path)
  (#^ str kind)
  (#^ str diff)
  (setv #^ (| str None) move-path None))

(defrecord FilePatch
  "道具の item fileChange の中身(ファイルを変える — apply_patch): changes = ファイルごとの変更(FileEdit の tuple)/ status = 状態
   (この版に無い綴りは UnknownValue)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (get tuple #(FileEdit ...)) changes)
  (#^ (| ToolStatus UnknownValue) status))

(defrecord WebSearch
  "道具の item webSearch の中身(web 検索): query = 検索の語(始まりの item は空 — 上流は語を検索の終わりで名乗る)/ action = 動きの
   種類の名(search・openPage・findInPage・other — 名乗らなければ None)/ queries = 動きが名乗る検索の語の列 / url・pattern = 動きが
   名乗る頁と頁の中の語(名乗らなければ None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str query)
  (setv #^ (| str None) action None)
  (setv #^ (get tuple #(str ...)) queries #())
  (setv #^ (| str None) url None)
  (setv #^ (| str None) pattern None))

(defrecord McpCall
  "道具の item mcpToolCall の中身(MCP の server の道具の呼び): server = server の名(codex の設定の mcp_servers の鍵)/ tool = 道具の
   名 / arguments = 道具に渡した引数(凍らせた JSON — 形は MCP の server の道具が決めるので、中を読まずに上の層の道具の呼びの命令へ
   運ぶ)/ status = 状態(この版に無い綴りは UnknownValue)/ output = 結果の text の block を改行でつないだ文(終わるまで・結果が無ければ
   None)/ non-text-kinds = 結果の text でない block の種類の名(image など)/ error = 誤りの文(誤りで終わった時だけ)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str server)
  (#^ str tool)
  (#^ FrozenJson arguments)
  (#^ (| ToolStatus UnknownValue) status)
  (setv #^ (| str None) output None)
  (setv #^ (get tuple #(str ...)) non-text-kinds #())
  (setv #^ (| str None) error None))

(defrecord DynamicCall
  "道具の item dynamicToolCall の中身(thread に宣言した動的な道具の呼び — codex は呼びを item/tool/call の要求で答え手へ頼む):
   tool = 道具の名 / namespace = 道具の namespace(無ければ None)/ arguments = 道具に渡した引数(凍らせた JSON — 形は道具の宣言が
   決める)/ status = 状態 / output = 答えの inputText の項目を改行でつないだ文(答えるまで None)/ non-text-kinds = 答えの文でない項目の
   種類の名(inputImage など)/ success = 答え手が名乗った成否(答えるまで None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str tool)
  (setv #^ (| str None) namespace None)
  (#^ FrozenJson arguments)
  (#^ (| ToolStatus UnknownValue) status)
  (setv #^ (| str None) output None)
  (setv #^ (get tuple #(str ...)) non-text-kinds #())
  (setv #^ (| bool None) success None))

(defrecord AgentState
  "子の agent 1 つの最後の状態: thread-id = 子の thread の id / status = 状態の名(pendingInit・running・completed など — 版で増える
   ので名のまま)/ message = 子の最後の答え(無ければ None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str status)
  (setv #^ (| str None) message None))

(defrecord AgentCall
  "道具の item collabAgentToolCall の中身(子の agent を始める・頼む・待つ・閉じる): tool = 動きの名(spawnAgent・sendInput・wait・
   closeAgent など — 版で増えるので名のまま)/ status = 状態 / receivers = 相手の子の thread の id(始める動きの始まりは空 — 子の id は
   終わりで届く)/ prompt = 子への頼み(頼む動きだけ)/ model = 子の model(名乗らなければ None — 始まりの空の綴りも None)/ states =
   子ごとの最後の状態(thread の id の順 — 終わりで届く)。子の agent は同じ process の別の thread で走り、その thread の行は会話の
   ターンに積まない(handler — 頭の註の TurnRecord)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str tool)
  (#^ (| ToolStatus UnknownValue) status)
  (setv #^ (get tuple #(str ...)) receivers #())
  (setv #^ (| str None) prompt None)
  (setv #^ (| str None) model None)
  (setv #^ (get tuple #(AgentState ...)) states #()))

(defrecord ImageView
  "道具の item imageView の中身(作業の dir の画像を model に見せる): path = 画像の path。codex 0.162.1 の item は状態も結果も持たない
   (始まりと終わりが同じ中身)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str path))

(val ToolItem (| CommandRun FilePatch WebSearch McpCall DynamicCall AgentCall ImageView))

(defrecord ItemStarted
  "ターンの中の item が始まった(item/started): item-type = codex の item の種類の名(userMessage・agentMessage・reasoning・
   commandExecution など — 版で増えるので名のまま)/ tool = 道具の種類(commandExecution・fileChange・webSearch・mcpToolCall・
   dynamicToolCall・collabAgentToolCall・imageView)の item の中身
   (ほかの種類は None)。上の層が「考えている」「道具を呼んでいる」を出す材料。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str item-type)
  (setv #^ (| ToolItem None) tool None))

(defrecord ItemDone
  "答えの全文でない item が終わった(item/completed の agentMessage 以外): item-type・tool は ItemStarted と同じ(tool は終わりの
   時点の中身 — 命令の出力と終了 code・検索の語はここで届く)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str item-type)
  (setv #^ (| ToolItem None) tool None))

(defrecord TokenCount
  "token の数の組(名乗らない欄は None)。"
  {:tags {:context "codex" :role "type"}}
  (setv #^ (| int None) total-tokens None)
  (setv #^ (| int None) input-tokens None)
  (setv #^ (| int None) cached-input-tokens None)
  (setv #^ (| int None) output-tokens None)
  (setv #^ (| int None) reasoning-output-tokens None))

(defrecord TokenUsage
  "使った token の数(thread/tokenUsage/updated): last = この呼びの分 / total = thread の累積 / model-context-window = model の窓の
   大きさ(名乗らなければ None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ TokenCount last)
  (#^ TokenCount total)
  (setv #^ (| int None) model-context-window None))

(defrecord TurnError
  "ターンの誤り(error の通知): message = 誤りの文 / error-kind = codex の誤りの種類の名(codexErrorInfo — 文字列の値か object の鍵・
   この版に無い形は UnknownValue・名乗らなければ None)/ http-status = 上流の HTTP の status(名乗らなければ None)/ will-retry =
   codex がこのターンを繰り返すか。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str message)
  (#^ (| str UnknownValue None) error-kind)
  (#^ (| int None) http-status)
  (#^ bool will-retry))

(defrecord UsageLimitHit
  "ターンが口座の利用枠切れで断られた事実(誤りの種類 USAGE-LIMIT-KIND の失敗の終わり): minutes = 口座が待つ窓の長さ(分)/
   resets-at = その窓が戻る刻(epoch 秒)/ text = codex の誤りの文。窓と刻は、そのターンで届いた枠の知らせの尽きた窓から読む
   (limits.hy の usage-limit-hit-of — 名乗られなければ None)。窓は長さで名乗る(窓の名は上の層の語)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| int None) minutes)
  (#^ (| int None) resets-at)
  (#^ str text))

(defrecord TurnEnded
  "ターンの終わり(turn/completed — ターンごとにちょうど 1 つ): status = 終わりの状態(この版に無い綴りは UnknownValue — 知らない状態でも
   終わりは届く)/ error-message・error-kind・http-status = 失敗の終わりの誤り(turn.error — 無ければ None・種類は TurnError と同じ)/
   usage-limit = 口座の利用枠切れの事実(行の分類では None。handler がそのターンの枠の知らせから埋める・fake は筋書きから)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ (| TurnStatus UnknownValue) status)
  (setv #^ (| str None) error-message None)
  (setv #^ (| str UnknownValue None) error-kind None)
  (setv #^ (| int None) http-status None)
  (setv #^ (| UsageLimitHit None) usage-limit None))

(defrecord RateLimitWindow
  "口座の利用枠の窓 1 つ: used-percent = 使った割合(百分率 — 100 以上で尽きた)/ minutes = 窓の長さ(分)/ resets-at = 窓が戻る刻
   (epoch 秒)。長さと戻りの刻は名乗らなければ None。"
  {:tags {:context "codex" :role "type"}}
  (#^ int used-percent)
  (#^ (| int None) minutes)
  (#^ (| int None) resets-at))

(defrecord RateLimits
  "口座の利用枠の知らせ(account/rateLimits/updated): limit-id = 枠の名(例 codex)/ primary・secondary = 窓(この知らせが名乗らなければ
   None — codex 0.162.1 の schema の註: 名乗らない欄は前に見た値を消さない)。codex は上流の答えの header から読んだ枠を出し、利用枠
   切れの断り(誤りの種類 usageLimitExceeded)ではその誤りの通知より先に出す。thread もターンも名乗らない(process の口座の通知)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| str None) limit-id)
  (#^ (| RateLimitWindow None) primary)
  (#^ (| RateLimitWindow None) secondary))

(defrecord CodexWarning
  "codex の警告(warning の通知 — 利用者へ見せる短い文): thread-id = 名指す thread(名乗らなければ None)/ message = 警告の文。ターンを
   名乗らない(codex 0.162.1 はターンを始める前に出す — 例 model の宣言が見つからない・試しの機能を使っている)。code mode が使えず
   道具の呼びを閉じた警告は、この型でなく CodeModeUnavailable。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| str None) thread-id)
  (#^ str message))

(defrecord CodeModeUnavailable
  "code mode が使えず、道具の呼びを閉じた(fail closed)と codex が名乗った事実(warning の通知のうち、文が CODE-MODE-UNAVAILABLE-HEAD
   で始まり CODE-MODE-FAIL-CLOSED を含む物): thread-id = 名指す thread / message = codex の警告の文(使えない訳 — 例 host の file が
   見つからない — を含む)。この process の model は道具を 1 つも呼べないまま文だけを返す。codex 0.162.1 は thread ごとに 1 度だけ、
   最初のターンを始める時(turn/start の答えより前)に出す — 2 つ目からのターンでは出ない。code mode の使える・使えないは codex が
   host の file が在るかだけで決めるので、host の file が在って起動できない形ではこの警告は出ない(録り code-mode-host-broken)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| str None) thread-id)
  (#^ str message))

(defrecord Other
  "語彙の外の通知: method = 通知の名(中身は持たない — 発明しない)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str method))

(defrecord Unparsed
  "読めない行: method = 読めた method の名(JSON として読めない行・外枠の形の違う行は空)/ reason = 読めない訳(どの型の・どの欄が)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str method)
  (#^ str reason))

(val CodexLine (| Response ErrorResponse ServerRequest ThreadStarted TurnStarted TextDelta ReasoningDelta AgentMessageDone
                   ItemStarted ItemDone TokenUsage RateLimits TurnError TurnEnded CodexWarning CodeModeUnavailable Other Unparsed))

;; ターンを名乗る記録(params の turnId か turn.id でターンの id を必ず持つ記録)。どの記録がターンを名乗るかは codex の行の形から
;; 決まる知識なので、記録の型と同じこの file が 1 か所で名乗る — handler はこの集まりで記録をターンの列へ振り分け、記録の class を
;; 列挙しない。ターンの id を必ず持つ記録を足したらここにも足す(tests/test_turn_records.hy が一致を確かめる)。
(val TurnRecord (| TurnStarted TextDelta ReasoningDelta AgentMessageDone ItemStarted ItemDone TokenUsage TurnError TurnEnded))

;; 口座の枠の窓を運ぶ記録(利用枠切れの事実の材料 — limits.hy の merged-limits が重ねる)。どの記録が窓を運ぶかも codex の行の形から
;; 決まる知識なので、TurnRecord と同じくこの file が 1 か所で名乗る — handler はこの集まりで記録をターンの枠へ重ね、記録の class を
;; 列挙しない。窓(RateLimitWindow・RateLimits)を運ぶ記録を足したらここにも足し、重ね方は limits.hy の merged-limits が受ける
;; (tests/test_limits.hy が一致を確かめる)。
(val LimitRecord (| RateLimits))

(defrecord ErrorFacts
  "codexErrorInfo から読んだ誤りの種類の名(この版に無い形は UnknownValue)と HTTP の status(どちらも名乗らなければ None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| str UnknownValue None) kind)
  (#^ (| int None) http-status))


;; --- 分類 -------------------------------------------------------------------------------------

(defk reason-of [#^ Malformed malformed]
  {:pre [(: malformed Malformed)] :post [(: % str)] :tags {:context "codex" :role "foundation"}}
  "形の合わない所を Unparsed の訳の 1 文にするため(型の名と、欄ごとの場所と訳)。"
  (.format "{}: {}" malformed.wire-type
           (.join "; " (gfor problem malformed.fields (.format "{} {}" (or problem.field "(値の全体)") problem.reason)))))


(defk error-facts-of [info]
  {:pre [(: info (| OpaqueJson None))] :post [(: % ErrorFacts)] :tags {:context "codex" :role "foundation"}}
  "codexErrorInfo(種類の名の文字列か、鍵ちょうど 1 つの object)から、誤りの種類の名と HTTP の status を読むため。順に解く: この版の
   object の形 → 種類の名の文字列 → どちらでもない形(版で増えた object)は UnknownValue。"
  (when (is info None)
    (return (ErrorFacts :kind None :http-status None)))
  (<- known (parse ErrorInfoWire info))
  (when (isinstance known ErrorInfoWire)
    (<- facts (known-error-facts-of known))
    (return facts))
  ;; 文字列かどうかは、{"kind": 値} に包んで解いて確かめる(OpaqueJson の text は最小の直列化の JSON の値そのもの)。
  (<- named (parse ErrorKindNameWire (OpaqueJson.from-text (.format "{{\"kind\":{}}}" info.text))))
  (if (isinstance named ErrorKindNameWire)
      (ErrorFacts :kind named.kind :http-status None)
      (ErrorFacts :kind (UnknownValue :value info) :http-status None)))


(defk known-error-facts-of [#^ ErrorInfoWire info]
  {:pre [(: info ErrorInfoWire)] :post [(: % ErrorFacts)] :tags {:context "codex" :role "foundation"}}
  "この版の codexErrorInfo の object(鍵ちょうど 1 つ)から、鍵の名を種類に、中の httpStatusCode を HTTP の status にするため。"
  (cond
    (is-not info.response-too-many-failed-attempts None)
    (ErrorFacts :kind "responseTooManyFailedAttempts" :http-status info.response-too-many-failed-attempts.http-status-code)
    (is-not info.http-connection-failed None)
    (ErrorFacts :kind "httpConnectionFailed" :http-status info.http-connection-failed.http-status-code)
    (is-not info.response-stream-connection-failed None)
    (ErrorFacts :kind "responseStreamConnectionFailed" :http-status info.response-stream-connection-failed.http-status-code)
    (is-not info.response-stream-disconnected None)
    (ErrorFacts :kind "responseStreamDisconnected" :http-status info.response-stream-disconnected.http-status-code)
    ;; 残りは activeTurnNotSteerable だけ(ErrorInfoWire の :check が鍵ちょうど 1 つを確かめている)。
    True (ErrorFacts :kind "activeTurnNotSteerable" :http-status None)))


(defk token-count-of [#^ TokenCountWire wire]
  {:pre [(: wire TokenCountWire)] :post [(: % TokenCount)] :tags {:context "codex" :role "foundation"}}
  "wire の token の数の組を、上の層が読む記録の型へ写すため。"
  (TokenCount :total-tokens wire.total-tokens :input-tokens wire.input-tokens :cached-input-tokens wire.cached-input-tokens
              :output-tokens wire.output-tokens :reasoning-output-tokens wire.reasoning-output-tokens))


(defk turn-status-of [#^ str spelled]
  {:pre [(: spelled str)] :post [(: % (| TurnStatus UnknownValue))] :tags {:context "codex" :role "foundation"}}
  "turn.status の綴りを TurnStatus にするため。この版に無い綴りは UnknownValue(既知の状態に読み替えない)。"
  (if (in spelled (frozenset (gfor known TurnStatus known.value)))
      (TurnStatus spelled)
      (UnknownValue :value (OpaqueJson.of spelled))))


(defk turn-ended-of [#^ TurnParamsWire params]
  {:pre [(: params TurnParamsWire)] :post [(: % TurnEnded)] :tags {:context "codex" :role "foundation"}}
  "turn/completed を、状態と失敗の誤りを持つターンの終わりにするため(知らない状態・誤りの種類でも終わりは作る)。"
  (val failure params.turn.error)
  (<- facts (error-facts-of (if (is failure None) None failure.codex-error-info)))
  (<- status (turn-status-of params.turn.status))
  (TurnEnded :thread-id params.thread-id :turn-id params.turn.id :status status
             :error-message (if (is failure None) None failure.message) :error-kind facts.kind :http-status facts.http-status))


(defk turn-error-of [#^ ErrorParamsWire params]
  {:pre [(: params ErrorParamsWire)] :post [(: % TurnError)] :tags {:context "codex" :role "foundation"}}
  "error の通知を、誤りの文・種類・HTTP の status を持つターンの誤りにするため。"
  (<- facts (error-facts-of params.error.codex-error-info))
  (TurnError :thread-id params.thread-id :turn-id params.turn-id :message params.error.message :error-kind facts.kind
             :http-status facts.http-status :will-retry params.will-retry))


(defk token-usage-of [#^ TokenUsageParamsWire params]
  {:pre [(: params TokenUsageParamsWire)] :post [(: % TokenUsage)] :tags {:context "codex" :role "foundation"}}
  "thread/tokenUsage/updated を、この呼びの分と累積を分けた使用量にするため。"
  (<- this-call (token-count-of params.token-usage.last))
  (<- whole-thread (token-count-of params.token-usage.total))
  (TokenUsage :thread-id params.thread-id :turn-id params.turn-id :last this-call :total whole-thread
              :model-context-window params.token-usage.model-context-window))


(defk warning-of [#^ WarningParamsWire params]
  {:pre [(: params WarningParamsWire)] :post [(: % (| CodexWarning CodeModeUnavailable))] :tags {:context "codex" :role "foundation"}}
  "warning の通知を、上の層が道具を呼べない process を見分ける事実(code mode が使えず道具の呼びを閉じた警告 — CodeModeUnavailable)と、
   ほかの警告(CodexWarning — 直の道具に戻る code mode の警告を含む)に分けるため。codex の文を照らすのはここ 1 か所。"
  (if (and (.startswith params.message CODE-MODE-UNAVAILABLE-HEAD) (in CODE-MODE-FAIL-CLOSED params.message))
      (CodeModeUnavailable :thread-id params.thread-id :message params.message)
      (CodexWarning :thread-id params.thread-id :message params.message)))


(defk window-of [window]
  {:pre [(: window (| RateLimitWindowWire None))] :post [(: % (| RateLimitWindow None))] :tags {:context "codex" :role "foundation"}}
  "枠の窓の wire を記録の窓にするため(名乗らない窓は None のまま)。"
  (if (is window None)
      None
      (RateLimitWindow :used-percent window.used-percent :minutes window.window-duration-mins :resets-at window.resets-at)))


(defk rate-limits-of [#^ RateLimitsParamsWire params]
  {:pre [(: params RateLimitsParamsWire)] :post [(: % RateLimits)] :tags {:context "codex" :role "foundation"}}
  "account/rateLimits/updated を、上の層が尽きた窓と戻りの刻を読む枠の知らせにするため。"
  (val limits params.rate-limits)
  (<- primary (window-of limits.primary))
  (<- secondary (window-of limits.secondary))
  (RateLimits :limit-id limits.limit-id :primary primary :secondary secondary))


(defk tool-status-of [#^ str spelled]
  {:pre [(: spelled str)] :post [(: % (| ToolStatus UnknownValue))] :tags {:context "codex" :role "foundation"}}
  "道具の item の status の綴りを ToolStatus にするため。この版に無い綴りは UnknownValue(既知の状態に読み替えない)。"
  (if (in spelled (frozenset (gfor known ToolStatus known.value)))
      (ToolStatus spelled)
      (UnknownValue :value (OpaqueJson.of spelled))))


(defk command-run-of [#^ CommandExecutionWire wire]
  {:pre [(: wire CommandExecutionWire)] :post [(: % CommandRun)] :tags {:context "codex" :role "foundation"}}
  "commandExecution の中身を、命令・dir・状態・出力・終了 code の記録にするため。"
  (<- status (tool-status-of wire.status))
  (CommandRun :command wire.command :cwd wire.cwd :status status :output wire.aggregated-output :exit-code wire.exit-code))


(defk file-patch-of [#^ FileChangeWire wire]
  {:pre [(: wire FileChangeWire)] :post [(: % FilePatch)] :tags {:context "codex" :role "foundation"}}
  "fileChange の中身を、ファイルごとの変更と状態の記録にするため。"
  (<- status (tool-status-of wire.status))
  (FilePatch :changes (tuple (gfor change wire.changes (FileEdit :path change.path :kind change.kind.type :diff change.diff
                                                                 :move-path change.kind.move-path)))
             :status status))


(defk web-search-of [#^ WebSearchWire wire]
  {:pre [(: wire WebSearchWire)] :post [(: % WebSearch)] :tags {:context "codex" :role "foundation"}}
  "webSearch の中身を、検索の語と動き(種類・語の列・頁・頁の中の語)の記録にするため。"
  (val action wire.action)
  (if (is action None)
      (WebSearch :query wire.query)
      (WebSearch :query wire.query :action action.type :queries (or action.queries #()) :url action.url :pattern action.pattern)))


(defk mcp-call-of [#^ McpToolCallWire wire]
  {:pre [(: wire McpToolCallWire)] :post [(: % McpCall)] :tags {:context "codex" :role "foundation"}}
  "mcpToolCall の中身を、server と道具の名・凍らせた引数・状態・結果の文(text の block)と文でない block の種類・誤りの文の記録に
   するため。引数は道具が決めた形の JSON なので、この JSON の境目で凍らせるだけで中を読まない。"
  (<- status (tool-status-of wire.status))
  (val blocks (if (is wire.result None) #() wire.result.content))
  (McpCall :server wire.server :tool wire.tool :arguments (freeze-json-text wire.arguments.text) :status status
           :output (if (is wire.result None) None (.join "\n" (gfor block blocks :if (= block.type "text") (or block.text ""))))
           :non-text-kinds (tuple (gfor block blocks :if (!= block.type "text") block.type))
           :error (if (is wire.error None) None wire.error.message)))


(defk dynamic-call-of [#^ DynamicToolCallWire wire]
  {:pre [(: wire DynamicToolCallWire)] :post [(: % DynamicCall)] :tags {:context "codex" :role "foundation"}}
  "dynamicToolCall の中身を、道具の名と namespace・凍らせた引数・状態・答えの文(inputText の項目)と文でない項目の種類・成否の記録に
   するため。"
  (<- status (tool-status-of wire.status))
  (val items (or wire.content-items #()))
  (DynamicCall :tool wire.tool :namespace wire.namespace :arguments (freeze-json-text wire.arguments.text) :status status
               :output (if (is wire.content-items None) None (.join "\n" (gfor item items :if (= item.type "inputText") (or item.text ""))))
               :non-text-kinds (tuple (gfor item items :if (!= item.type "inputText") item.type))
               :success wire.success))


(defk agent-call-of [#^ CollabAgentToolCallWire wire]
  {:pre [(: wire CollabAgentToolCallWire)] :post [(: % AgentCall)] :tags {:context "codex" :role "foundation"}}
  "collabAgentToolCall の中身を、動きの名・状態・相手の子の thread・頼み・model・子ごとの最後の状態の記録にするため(model の空の
   綴りは名乗らないと読む — 子を始める動きの始まりは model を空で名乗る)。"
  (<- status (tool-status-of wire.status))
  (AgentCall :tool wire.tool :status status :receivers wire.receiver-thread-ids :prompt wire.prompt :model (or wire.model None)
             :states (tuple (gfor [thread state] (sorted (.items wire.agents-states))
                                  (AgentState :thread-id thread :status state.status :message state.message)))))


(defk image-view-of [#^ ImageViewWire wire]
  {:pre [(: wire ImageViewWire)] :post [(: % ImageView)] :tags {:context "codex" :role "foundation"}}
  "imageView の中身を、見せた画像の path の記録にするため。"
  (ImageView :path wire.path))


(defk tool-parsed [#^ str method #^ type wire-type convert item]
  {:pre [(: method str) (: wire-type type) (: convert Callable) (: item OpaqueJson)] :post [(: % (| ToolItem Unparsed))]
   :tags {:context "codex" :role "foundation"}}
  "道具の item の中身を、その種類の wire の型で解いて記録へ写すため(convert = 写す defk)。形の合わない中身は、method と訳を持つ
   Unparsed(道具の中身の無い item に読み替えない)。"
  (<- parsed (parse wire-type item))
  (when (isinstance parsed Malformed)
    (<- reason (reason-of parsed))
    (return (Unparsed :method method :reason reason)))
  (<- record (convert parsed))
  record)


(defk tool-of [#^ str method #^ str item-type item]
  {:pre [(: method str) (: item-type str) (: item OpaqueJson)] :post [(: % (| ToolItem Unparsed None))]
   :tags {:context "codex" :role "foundation"}}
  "item の種類が道具(commandExecution・fileChange・webSearch・mcpToolCall・dynamicToolCall・collabAgentToolCall・imageView)なら
   中身を記録にするため(ほかの種類は None)。"
  (match item-type
    "commandExecution" (do (<- tool (tool-parsed method CommandExecutionWire command-run-of item)) tool)
    "fileChange" (do (<- tool (tool-parsed method FileChangeWire file-patch-of item)) tool)
    "webSearch" (do (<- tool (tool-parsed method WebSearchWire web-search-of item)) tool)
    "mcpToolCall" (do (<- tool (tool-parsed method McpToolCallWire mcp-call-of item)) tool)
    "dynamicToolCall" (do (<- tool (tool-parsed method DynamicToolCallWire dynamic-call-of item)) tool)
    "collabAgentToolCall" (do (<- tool (tool-parsed method CollabAgentToolCallWire agent-call-of item)) tool)
    "imageView" (do (<- tool (tool-parsed method ImageViewWire image-view-of item)) tool)
    _ None))


(defk item-started-of [#^ ItemParamsWire params]
  {:pre [(: params ItemParamsWire)] :post [(: % (| ItemStarted Unparsed))] :tags {:context "codex" :role "foundation"}}
  "item/started を、上の層が「考えている」「道具を呼んでいる」を出す材料(item の種類と id と、道具の中身)にするため。"
  (<- item (parse ItemWire params.item))
  (when (isinstance item Malformed)
    (<- reason (reason-of item))
    (return (Unparsed :method "item/started" :reason reason)))
  (<- tool (tool-of "item/started" item.type params.item))
  (when (isinstance tool Unparsed)
    (return tool))
  (ItemStarted :thread-id params.thread-id :turn-id params.turn-id :item-id item.id :item-type item.type :tool tool))


(defk item-done-of [#^ ItemParamsWire params]
  {:pre [(: params ItemParamsWire)] :post [(: % (| AgentMessageDone ItemDone Unparsed))] :tags {:context "codex" :role "foundation"}}
  "item/completed のうち答えの全文(agentMessage)を AgentMessageDone に、ほかの種類の item を ItemDone(道具の種類は終わりの時点の
   中身つき)にするため。全文の無い agentMessage は読めない行(答えの全文を空と読み替えない)。"
  (<- item (parse ItemWire params.item))
  (when (isinstance item Malformed)
    (<- reason (reason-of item))
    (return (Unparsed :method "item/completed" :reason reason)))
  (when (= item.type "agentMessage")
    (return (if (is item.text None)
                (Unparsed :method "item/completed" :reason (.format "agentMessage の item {} に text が無い" item.id))
                (AgentMessageDone :thread-id params.thread-id :turn-id params.turn-id :item-id item.id :text item.text))))
  (<- tool (tool-of "item/completed" item.type params.item))
  (when (isinstance tool Unparsed)
    (return tool))
  (ItemDone :thread-id params.thread-id :turn-id params.turn-id :item-id item.id :item-type item.type :tool tool))


(defk text-delta-of [#^ DeltaParamsWire params]
  {:pre [(: params DeltaParamsWire)] :post [(: % TextDelta)] :tags {:context "codex" :role "foundation"}}
  "item/agentMessage/delta を答えの文字の途中にするため。"
  (TextDelta :thread-id params.thread-id :turn-id params.turn-id :item-id params.item-id :text params.delta))


(defk reasoning-text-of [#^ DeltaParamsWire params]
  {:pre [(: params DeltaParamsWire)] :post [(: % ReasoningDelta)] :tags {:context "codex" :role "foundation"}}
  "item/reasoning/textDelta を考えの本文の差分にするため。"
  (ReasoningDelta :thread-id params.thread-id :turn-id params.turn-id :item-id params.item-id :text params.delta :summary False))


(defk reasoning-summary-of [#^ DeltaParamsWire params]
  {:pre [(: params DeltaParamsWire)] :post [(: % ReasoningDelta)] :tags {:context "codex" :role "foundation"}}
  "item/reasoning/summaryTextDelta を考えの要約の差分にするため。"
  (ReasoningDelta :thread-id params.thread-id :turn-id params.turn-id :item-id params.item-id :text params.delta :summary True))


(defk thread-started-of [#^ ThreadParamsWire params]
  {:pre [(: params ThreadParamsWire)] :post [(: % ThreadStarted)] :tags {:context "codex" :role "foundation"}}
  "thread/started を thread の id の記録にするため。"
  (ThreadStarted :thread-id params.thread.id))


(defk turn-started-of [#^ TurnParamsWire params]
  {:pre [(: params TurnParamsWire)] :post [(: % TurnStarted)] :tags {:context "codex" :role "foundation"}}
  "turn/started をターンの始まりの記録にするため。"
  (TurnStarted :thread-id params.thread-id :turn-id params.turn.id))


(defk parsed-into [#^ str method #^ type wire-type convert params]
  {:pre [(: method str) (: wire-type type) (: convert Callable) (: params (| OpaqueJson None))] :post [(: % CodexLine)]
   :tags {:context "codex" :role "foundation"}}
  "語彙の中の通知の params を、その method の wire の型で解いて記録へ写すため(convert = 写す defk)。params が無い・形が合わない
   通知は、method を名乗る Unparsed にする。"
  (when (is params None)
    (return (Unparsed :method method :reason "params が無い")))
  (<- parsed (parse wire-type params))
  (when (isinstance parsed Malformed)
    (<- reason (reason-of parsed))
    (return (Unparsed :method method :reason reason)))
  (<- record (convert parsed))
  record)


(defk notification-of [#^ str method params]
  {:pre [(: method str) (: params (| OpaqueJson None))] :post [(: % CodexLine)] :tags {:context "codex" :role "foundation"}}
  "通知を method で選んだ型で解き、記録にするため。語彙の外は Other(差分の 3 つは wire の型が同じで、写し方だけが違う)。"
  (match method
    "thread/started" (do (<- record (parsed-into method ThreadParamsWire thread-started-of params)) record)
    "turn/started" (do (<- record (parsed-into method TurnParamsWire turn-started-of params)) record)
    "turn/completed" (do (<- record (parsed-into method TurnParamsWire turn-ended-of params)) record)
    "item/agentMessage/delta" (do (<- record (parsed-into method DeltaParamsWire text-delta-of params)) record)
    "item/reasoning/textDelta" (do (<- record (parsed-into method DeltaParamsWire reasoning-text-of params)) record)
    "item/reasoning/summaryTextDelta" (do (<- record (parsed-into method DeltaParamsWire reasoning-summary-of params)) record)
    "item/started" (do (<- record (parsed-into method ItemParamsWire item-started-of params)) record)
    "item/completed" (do (<- record (parsed-into method ItemParamsWire item-done-of params)) record)
    "error" (do (<- record (parsed-into method ErrorParamsWire turn-error-of params)) record)
    "thread/tokenUsage/updated" (do (<- record (parsed-into method TokenUsageParamsWire token-usage-of params)) record)
    "account/rateLimits/updated" (do (<- record (parsed-into method RateLimitsParamsWire rate-limits-of params)) record)
    "warning" (do (<- record (parsed-into method WarningParamsWire warning-of params)) record)
    _ (Other :method method)))


(defk response-of [#^ EnvelopeWire envelope]
  {:pre [(: envelope EnvelopeWire)] :post [(: % (| Response Unparsed))] :tags {:context "codex" :role "foundation"}}
  "要求への答えを、要求の id と答えが名乗る thread・ターンの id の記録にするため。result の無い答え(error も無い)は形の合わない行。"
  (when (is envelope.result None)
    (return (Unparsed :method "" :reason (.format "id {} の答えに result も error も無い" envelope.id))))
  (<- parsed (parse ResultWire envelope.result))
  (when (isinstance parsed Malformed)
    (<- reason (reason-of parsed))
    (return (Unparsed :method "" :reason reason)))
  (Response :id envelope.id
            :thread-id (if (is parsed.thread None) None parsed.thread.id)
            :turn-id (if (is parsed.turn None) None parsed.turn.id)))


(defk classify-line [#^ str line]
  {:pre [(: line str)] :post [(: % CodexLine)] :tags {:context "codex" :role "foundation"}}
  "app-server の stdout の 1 行を、上の層が読む記録にするため(JSON の境目の 1 か所)。"
  (<- envelope (parse-json EnvelopeWire line))
  (when (isinstance envelope Malformed)
    (<- reason (reason-of envelope))
    (return (Unparsed :method "" :reason reason)))
  (cond
    (and (is-not envelope.id None) (is-not envelope.method None))
    (ServerRequest :id envelope.id :method envelope.method :params envelope.params)
    (and (is-not envelope.id None) (is-not envelope.error None))
    (ErrorResponse :id envelope.id :code envelope.error.code :message envelope.error.message)
    (is-not envelope.id None)
    (do (<- answer (response-of envelope)) answer)
    (is-not envelope.method None)
    (do (<- record (notification-of envelope.method envelope.params)) record)
    True (Unparsed :method "" :reason "id も method も無い")))


;; --- 上限を超えて頭だけを読んだ行 -------------------------------------------------------------------
;; 読み手(process.hy)は、読む上限を超えた行の頭(CutLine)だけを渡す。頭は途中で切れた JSON なので、最後に揃った値の後で閉じてから
;; 外枠を読む。

;; 途中で切れた JSON の頭の字句: 閉じた文字列 / 区切りの 1 字 / 空白 / 数と語(true・false・null)。閉じていない文字列は合わない
;; (頭の終わりで読むのをやめる)。
(val JSON-HEAD-TOKEN (re.compile #[x["[^"\\]*(?:\\.[^"\\]*)*"|[{}\[\],:]|\s+|[^{}\[\],:\s"]+]x]))


(defk json-head-scan [#^ str text]
  {:pre [(: text str)] :post [(: % tuple)] :tags {:context "codex" :role "foundation"}}
  "途中で切れた JSON の text を閉じる所を探すため、前から読んで #(最後に揃った値の後の位置 開いたままの入れ子の列) を答える。揃った
   値 = 閉じた文字列の値・後ろに字の来た数と語・閉じた入れ子(object の鍵の文字列は値でない — 頭の終わりに触れる数と語は切れている
   かもしれないので揃わない)。入れ子の列の 1 つ = \"{k\"(object・次は鍵)/ \"{v\"(object・次は値)/ \"[\"(array)。読めない字
   (閉じていない文字列の頭など)で読むのをやめる(位置は揃った値が無ければ 0)。"
  (var stack #())
  (var safe 0)
  (var at 0)
  (while (< at (len text))
    (val token (.match JSON-HEAD-TOKEN text at))
    (when (is token None) (break))
    (val word (.group token))
    (val end (.end token))
    (val lead (get word 0))
    (val top (if stack (get stack -1) None))
    (match lead
      _ :if (.isspace lead) None
      "{" (:= stack (+ stack #("{k")))
      "[" (:= stack (+ stack #("[")))
      (| "}" "]") (do (:= stack (cut stack 0 -1))
                      (:= safe end))
      ":" (:= stack (+ (cut stack 0 -1) #("{v")))
      "," (when (= top "{v") (:= stack (+ (cut stack 0 -1) #("{k"))))
      _ :if (= top "{k") None
      "\"" (:= safe end)
      _ :if (= end (len text)) (break)
      _ (:= safe end))
    (:= at end))
  #(safe stack))


(defk closed-json-head [#^ str head]
  {:pre [(: head str)] :post [(: % str)] :tags {:context "codex" :role "foundation"}}
  "途中で切れた JSON の頭を、読める JSON の文字列にするため: 最後に揃った値の後で切り、開いたままの入れ子を順に閉じる。途中の
   文字列・数・鍵だけの欄・開いたばかりの入れ子は丸ごと落とす(欄を途中で切らない)。揃った値が無ければ空の文字列。"
  (<- scanned (json-head-scan head))
  (val safe (get scanned 0))
  (when (= safe 0)
    (return ""))
  (val kept (cut head 0 safe))
  (<- rescanned (json-head-scan kept))
  (+ kept (.join "" (gfor frame (reversed (get rescanned 1)) (if (= frame "[") "]" "}")))))


(defk classify-cut-line [#^ str head #^ int size]
  {:pre [(: head str) (: size int)] :post [(: % (| Response ErrorResponse Unparsed))] :tags {:context "codex" :role "foundation"}}
  "読む上限を超えて頭だけを読んだ行(process.hy の CutLine — head = 行の頭・size = 行の大きさの bytes)を、上の層が読む記録にする
   ため。頭を閉じて(closed-json-head)外枠を読み、要求への答えと誤りの答えはその記録にする — 運ぶ欄は id と短い欄だけで、閉じた頭は
   欄を途中で切らず丸ごと落とすので、記録は欠けないか形の違いになる(thread を開く答えは thread の id が頭に、履歴 turns が thread の
   最後の欄に在る — codex 0.162.1)。ほかの行(通知・codex からの要求)は中身が欠けるので記録を作らず、method と行の大きさを名乗る
   Unparsed にする(欠けた記録を揃った物として渡さない — codex 0.162.1 の turn/completed は items を載せないので上限に届かない)。"
  (val reason (.format "行の大きさ {} bytes が読む上限を超えた — 頭だけを読み、残りを読み捨てた" size))
  (<- closed (closed-json-head head))
  (<- envelope (parse-json EnvelopeWire closed))
  (match envelope
    (Malformed) (Unparsed :method "" :reason reason)
    _ :if (is-not envelope.method None) (Unparsed :method envelope.method :reason reason)
    _ :if (and (is-not envelope.id None) (is-not envelope.error None))
    (ErrorResponse :id envelope.id :code envelope.error.code :message envelope.error.message)
    _ :if (and (is-not envelope.id None) (is-not envelope.result None))
    (do (<- answer (response-of envelope))
        (if (isinstance answer Response) answer (Unparsed :method "" :reason reason)))
    _ (Unparsed :method "" :reason reason)))
