;;; claude の print mode の stdout の 1 行の型と、手番の終わりの型(設計 4.2 — この package が持つ型)。
;;;
;;; 分類(classify-record)は純関数: stream-json の 1 行(JSON の object)→ ClaudeLineKind。語彙の外の行は捨てずに
;;; Other { type, subtype } へ名前だけ持つ(発明しない)。1 行の逐語は ClaudeStreamLine.raw に残る。
;;;
;;; JSON の境界はこの file の 1 か所(JSON Lines を行に割る jsonl-lines・parse-record と classify-*、transcript の額の行を読む
;;; recorded-cost)。状態機械(dialogue.hy)も
;;; 上の層も、分類した型だけを読む — 生の dict を読み直す 2 か所目を作らない。
(require doeff-hy.macros [defk val <-])
(require doeff-hy.record [defenum defrecord])
(import dataclasses [dataclass field fields replace])
(import datetime [datetime])
(import enum [StrEnum])
(import json)
(import math)
(import doeff [run])
(import doeff_hy.frozen [FrozenMap FrozenJson freeze-json frozen-json-object])
(import doeff_claude_code.values [ClaudeTurn])


;; --- 消費の token ------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Usage []
  "手番の終わり(result の行)の usage。CLI が名乗った数だけを持ち、名乗らない欄は None(0 を発明しない)。
   result の行の usage はその CLI の手番 1 回分で、累積ではない(実測 2.1.283・#883: 同じ process の 2 つ目の
   result の行は 2 回目の分だけを名乗る)。
   欄: input-tokens = input_tokens / output-tokens = output_tokens / cache-creation-input-tokens = cache_creation_input_tokens /
   cache-read-input-tokens = cache_read_input_tokens / cache-creation-5m-input-tokens・cache-creation-1h-input-tokens =
   cache_creation.ephemeral_5m_input_tokens・ephemeral_1h_input_tokens / web-search-requests = server_tool_use.web_search_requests /
   service-tier = service_tier。ここに無い欄(CLI の版で増える欄)は捨てる — 要る欄はここへ足す。"
  (setv #^ (| int None) input-tokens None)
  (setv #^ (| int None) output-tokens None)
  (setv #^ (| int None) cache-creation-input-tokens None)
  (setv #^ (| int None) cache-read-input-tokens None)
  (setv #^ (| int None) cache-creation-5m-input-tokens None)
  (setv #^ (| int None) cache-creation-1h-input-tokens None)
  (setv #^ (| int None) web-search-requests None)
  (setv #^ (| str None) service-tier None)
  (defn __add__ [self #^ "Usage" other]
    "2 つの result の行の usage の和。1 つの host の手番に result の行が 2 つ以上ある時(読まれていない注入を CLI が次の CLI の手番として
     走らせた・CLI が自分で起こした手番)に、手番の usage を額と同じ母集団(手番に読んだ全部の行)で数えるため(状態機械 dialogue.hy)。
     欄ごとに、どちらも名乗らなければ None、片方だけなら名乗った数(0 を発明しない)。service-tier は後の行の物(無ければ前の行の物)。"
    (when (not (isinstance other Usage)) (return NotImplemented))
    (Usage #** (dfor usage-field (fields self)
                     :setv before (getattr self usage-field.name)
                     :setv after (getattr other usage-field.name)
                     usage-field.name (cond
                                        (is after None) before
                                        (is before None) after
                                        (= usage-field.name "service_tier") after
                                        True (+ before after))))))


;; --- 行の種類 ---------------------------------------------------------------------------------

(defrecord TimedPhase
  "CLI が名乗った計時の区間 1 つ(環境変数 CLAUDE_CODE_EMIT_STARTUP_TIMING を付けて起こした process だけが出す — argv.hy の
   process-env)。name = 区間の名(CLI の鍵のまま — 例 node_boot_ms・input_hooks)/ ms = 区間の長さのミリ秒 / start-ms = 区間の
   始まり(起動の区間だけが名乗る — CLI の process の時刻の起点 startup-origin-ms からのミリ秒。要求までの区間は始まりを名乗らないので
   None)。送ってから最初の字までのうち、CLI の起動と要求を送るまでの秒を区間ごとに割るため(#3855)。"
  (#^ str name)
  (#^ int ms)
  (setv #^ (| int None) start-ms None))

(defclass [(dataclass :frozen True)] Init []
  "system/init: 会話の id・CLI の能力の宣言・model・許可のモード・MCP の server の名 /
   startup-phases = startup_timing.phases の起動の区間(始まりは startup_timing.phase_start_ms — 区間は入れ子になる)/
   startup-origin-ms = startup_timing.time_origin_ms(区間の始まりの起点の壁の時刻・epoch ミリ秒を床へ)。startup_timing は
   CLAUDE_CODE_EMIT_STARTUP_TIMING を付けた process の最初の init の行だけが持つ — 無ければ空と None(実測 2.1.292・#3855)。"
  (#^ str session-id)
  (setv #^ (get tuple #(str ...)) capabilities #())
  (setv #^ str model "")
  (setv #^ str permission-mode "")
  (setv #^ (get tuple #(str ...)) mcp-servers #())
  (setv #^ (get tuple #(TimedPhase ...)) startup-phases #())
  (setv #^ (| int None) startup-origin-ms None))

(defclass [(dataclass :frozen True)] ToolCall []
  "assistant の message の tool_use の block 1 つ: id = block の id(続く user の行の tool_result の tool_use_id が同じ id で
   結果を名指す)/ name = 道具の名。どちらも空でない文字列(空の欄は分類の段で Other として断る — 空文字で通さない)/
   input = 道具の呼びの命令(block の input の JSON の object のまま — 鍵の集合は道具ごとに開いているので、PermissionRequested.input と
   同じく深く凍らせた写像。input の無い・写像でない block は分類の段で Other として断る)。#3744。"
  (#^ str id)
  (#^ str name)
  (setv #^ (get FrozenMap FrozenJson) input (field :default-factory FrozenMap))
  (defn __post_init__ [self]
    (when (not (and (isinstance self.id str) self.id))
      (raise (ValueError (.format "ToolCall.id は空でない文字列: {!r}" self.id))))
    (when (not (and (isinstance self.name str) self.name))
      (raise (ValueError (.format "ToolCall.name は空でない文字列: {!r}" self.name))))
    (object.__setattr__ self "input" (frozen-json-object self.input "ToolCall.input"))))

(defclass [(dataclass :frozen True)] AssistantMessage []
  "assistant の message: 本文の text の block を連ねたものと、呼んだ道具(tool_use の block の id と名の組)の列 /
   usage = message.usage(この行を出した API の呼び 1 回の消費 — 1 つの呼びは content の block ごとに行を出し、どの行も同じ usage を
   名乗る。出力の token は呼びの途中の値。usage の object が無ければ None)/ model = message.model(無ければ None)/
   parent-tool-use-id = subagent の行なら親の呼び(道具の呼び)の id、本体の会話の行は None(行の最上位の parent_tool_use_id)。#3744。
   message-id = message.id(この行を出した API の呼びの id — 1 つの呼びの block ごとの行は同じ id を名乗る。subagent の行も持つ。
   無ければ None)— 上の層が、並んで走る subagent の行が交互に来ても呼びごとに畳むため(ki-8239be27d5f6)。"
  (setv #^ str text "")
  (setv #^ (get tuple #(ToolCall ...)) tool-calls #())
  (setv #^ (| Usage None) usage None)
  (setv #^ (| str None) model None)
  (setv #^ (| str None) parent-tool-use-id None)
  (setv #^ (| str None) message-id None)
  ;; error = 行の最上位の error(CLI が API の誤りを答えの代わりに出した時の語 — 例 rate_limit: 口座の限度に当たった・#3983。無ければ None)。
  (setv #^ (| str None) error None)
  ;; stop-reason = message.stop_reason(無ければ None)/ refusal-category = model の安全装置が答えを断った行(stop-reason refusal)の
  ;; message.stop_details.category(例 reasoning_extraction・cyber — 無ければ None)。
  (setv #^ (| str None) stop-reason None)
  (setv #^ (| str None) refusal-category None))

(defclass [(dataclass :frozen True)] ToolAnswer []
  "user の行の tool_result の block 1 つ(道具の結果 1 つ): id = 答えた呼びの id(tool_use_id — 前の ToolCall.id と同じ・空でない文字列)/
   text = 結果の中身の本文(content が文字列ならそのまま、block の列なら text の block の本文を改行で連ねた物、content が無ければ空)/
   is-error = 道具が誤りで終えたか(block の is_error が真の時だけ真)/ non-text-kinds = content の列にあった text でない block
   (画像など)の種類の名を出た順に(本文に入らない中身が在ったことを黙って消さない — type の無い block は空の名)。#3744。"
  (#^ str id)
  (#^ str text)
  (#^ bool is-error)
  (setv #^ (get tuple #(str ...)) non-text-kinds #())
  (defn __post_init__ [self]
    (when (not (and (isinstance self.id str) self.id))
      (raise (ValueError (.format "ToolAnswer.id は空でない文字列: {!r}" self.id))))
    (when (not (isinstance self.text str))
      (raise (TypeError (.format "ToolAnswer.text は文字列: {!r}" self.text))))
    (when (not (isinstance self.non-text-kinds tuple))
      (raise (TypeError (.format "ToolAnswer.non_text_kinds は tuple: {!r}" self.non-text-kinds))))))

(defclass [(dataclass :frozen True)] ToolResult []
  "user の行の tool_result(道具の結果が model へ返った): answers = 結果ごとの答え(block の順 — 答えた呼びの id は各答えの id)。"
  (#^ (get tuple #(ToolAnswer ...)) answers))

;; stream_event の delta の種類(#3746 (a) — 閉じた語彙): TEXT = text_delta(本文の差分)/ THINKING = thinking_delta(考えている間の
;; 差分)/ TOOL-INPUT = input_json_delta(道具の呼びの命令を書いている間の差分)/ OTHER = 種類の名の在るほかの delta(signature_delta・
;; citations_delta・CLI の版で増える物)/ NO-DELTA = 種類の名の在る delta を持たない stream_event(message_start・content_block_start /
;; stop・message_delta)。
(defenum DeltaKind TEXT THINKING TOOL-INPUT OTHER NO-DELTA)

(defrecord CallOpened
  "API の呼び 1 回の始まり(stream_event の message_start — card ki-290ec664737d): message-id = message.id / model = message.model
   (無ければ None)/ parent-tool-use-id = 行の最上位の parent_tool_use_id(本体の会話は None)/ usage = message.usage(始まりの usage —
   出力は途中の値。実測 CLI 2.1.292: stdout の message_start も、その呼びの assistant の行も、出力 16 を名乗る)。"
  (#^ str message-id)
  (#^ (| str None) model)
  (#^ (| str None) parent-tool-use-id)
  (#^ Usage usage))

(defclass [(dataclass :frozen True)] PartialMessage []
  "stream_event(--include-partial-messages)。text-delta = text_delta なら本文の差分(ほかは空)/ delta = delta の種類(DeltaKind —
   考えている間や道具の命令を書いている間の行を、本文の空の行と分けて数えるため)/ thinking-delta = thinking_delta なら考えの差分の
   文字列(ほかは空 — 上の層が本文の前に「考えている」と分かる表示を出すため・#3789)/ tool-input-delta = input_json_delta なら道具の
   命令の切れ端(partial_json — JSON の途中で単独では読めない文字列・ほかは空)/ tool-start = content_block_start の tool_use なら呼びの
   id と道具の名(ToolCall — 命令はまだ無い・ほかは None)。後の 2 つは上の層が「担当は <道具> の命令を書いている」と出すため
   (#3974 の 3)。thinking-start = content_block_start の thinking(考えの block の始まり)なら真 — 考えの最初の差分より先に届くので、
   上の層が「考えている」の表示をその時に出せる(#4186・ほかは偽)。本文の差分は種類 TEXT の行だけ、考えの差分は種類 THINKING の行だけ、命令の切れ端は種類 TOOL-INPUT の
   行だけが持つ — 作り手が種類を名乗り忘れた行を作る時に断る。ttft-ms = 行の最上位の ttft_ms(message_start の行だけが名乗る —
   CLI が API へ要求を送ってから message_start を受けるまでのミリ秒。実測 2.1.292 で result の ttft_stream_ms − time_to_request_ms と
   1 ms 以内で同じ・名乗らなければ None — #3855)。
   call-opened = message_start の行なら、その呼びの始まり(CallOpened — id の無い message_start とほかの行は None)/ call-end-usage =
   message_delta の行なら、その event の usage(呼びの終わりの usage — 実測 CLI 2.1.292 は最終の出力と入力の側・cache の寿命の内訳・
   server_tool_use を名乗る。名乗らない欄は None・ほかの行は None)/ parent-tool-use-id = 行の最上位の parent_tool_use_id(本体の会話は
   None)。後の 3 つは、上の層が呼びの最終の usage を始まりと終わりの対から組むため(call-step — card ki-290ec664737d)。"
  (setv #^ str text-delta "")
  (setv #^ DeltaKind delta DeltaKind.NO-DELTA)
  (setv #^ str thinking-delta "")
  (setv #^ str tool-input-delta "")
  (setv #^ (| ToolCall None) tool-start None)
  (setv #^ (| int None) ttft-ms None)
  (setv #^ bool thinking-start False)
  (setv #^ (| CallOpened None) call-opened None)
  (setv #^ (| Usage None) call-end-usage None)
  (setv #^ (| str None) parent-tool-use-id None)
  (defn __post_init__ [self]
    (when (and self.tool-input-delta (!= self.delta DeltaKind.TOOL-INPUT))
      (raise (ValueError (.format "PartialMessage の命令の切れ端は種類 TOOL-INPUT の行だけ: delta {!r}" self.delta))))
    (when (and self.text-delta (!= self.delta DeltaKind.TEXT))
      (raise (ValueError (.format "PartialMessage の本文の差分は種類 TEXT の行だけ: delta {!r}" self.delta))))
    (when (and self.thinking-delta (!= self.delta DeltaKind.THINKING))
      (raise (ValueError (.format "PartialMessage の考えの差分は種類 THINKING の行だけ: delta {!r}" self.delta))))))

;; --- 呼びの最終の usage(card ki-290ec664737d) ------------------------------------------------------
;;
;; CLI の stdout の assistant の行の usage は、その呼びの message_start の値のまま(出力は途中の値 — 実測 CLI 2.1.292 は block ごとの行が
;; どれも出力 16 を名乗る)。呼びの出力の全部と、終わりに確かになる入力の側は、その呼びの message_delta の stream_event だけが名乗る。
;; 上の層が呼びごとの使用量を最終の値で記録できるように、同じ親(本体の会話か subagent の道具の呼び)の message_start と message_delta の
;; 対を 1 つの「閉じた呼び」(CallClosed)へ組む。重ね方は CLI が自分の記録に使う規則と同じ(closed-usage)。message_delta の来ない呼び
;; (止めた・process が降りた・stream が切れて CLI が送り直した)は閉じない — 上の層はその呼びの出力を途中の値として扱う(0 も最終の値も
;; 発明しない)。

(defrecord CallClosed
  "API の呼び 1 回の最終の消費: 同じ親の最後の message_start(CallOpened)の usage に、その呼びの message_delta の usage を重ねた物
   (closed-usage)。stdout の行ではなく、handler と fake が message_delta の行の直後に手番の記録へ足す行の種類(raw は空)。
   message-id・model = その呼びの message_start の値 / parent-tool-use-id = 本体の会話は None / usage = 最終の usage。"
  (#^ str message-id)
  (#^ (| str None) model)
  (#^ (| str None) parent-tool-use-id)
  (#^ Usage usage))

(defrecord CallStep
  "1 行を読んだ後の呼びの組み: open = 開いている呼び(親ごとに高々 1 つ — CLI は 1 つの親の呼びを順に送る)/ closed = この行で閉じた
   呼び(閉じなければ None)。"
  (#^ (get tuple #(CallOpened ...)) open)
  (#^ (| CallClosed None) closed))

(defk positive-or [#^ (| int None) stated #^ (| int None) earlier]
  {:pre [(: stated (| int None)) (: earlier (| int None))] :post [(: % (| int None))] :tags {:context "claude-code" :role "foundation"}}
  "終わりの usage の入力の側の 1 欄を、始まりの値へ重ねるため(CLI と同じ — 終わりが 0 より大きい数を名乗った時だけ採り、名乗らない・0 は
   始まりの値)。"
  (if (and (is-not stated None) (> stated 0)) stated earlier))

(defk stated-or [stated earlier]
  {:pre [(: stated (| int str None)) (: earlier (| int str None))] :post [(: % (| int str None))]
   :tags {:context "claude-code" :role "foundation"}}
  "終わりの usage の 1 欄を、始まりの値へ重ねるため(CLI と同じ — 終わりが名乗れば 0 でもその値・名乗らなければ始まりの値)。"
  (if (is-not stated None) stated earlier))

(defk closed-usage [start end]
  {:pre [(: start (| Usage None)) (: end (| Usage None))] :post [(: % (| Usage None))] :tags {:context "claude-code" :role "judgment"}}
  "呼びの始まり(message_start)の usage に終わり(message_delta)の usage を重ねた、呼びの最終の usage を返すため。規則は CLI 2.1.292 が
   自分の記録に使う重ね方と同じ: 入力・cache の読み・cache の書きは終わりが 0 より大きい数を名乗った時だけ終わりの値(cache の書きの
   総数を名乗らずに寿命の内訳だけを名乗れば内訳の和)・cache の書きの 5 分 / 1 時間の内訳は終わりが内訳を名乗った時(和が 0 より大きい)
   だけ終わりの値(欄ごとに名乗らなければ始まり)・出力・web 検索の数・service tier は終わりが名乗れば終わりの値。始まりか終わりの
   無い呼びは閉じない(None — 読まなかった側の usage を発明しない)。"
  (when (or (is start None) (is end None))
    (return None))
  (val end-5m (or end.cache-creation-5m-input-tokens 0))
  (val end-1h (or end.cache-creation-1h-input-tokens 0))
  (val lifetimes-stated (> (+ end-5m end-1h) 0))
  (<- input (positive-or end.input-tokens start.input-tokens))
  (<- write (positive-or end.cache-creation-input-tokens (if lifetimes-stated (+ end-5m end-1h) start.cache-creation-input-tokens)))
  (<- read (positive-or end.cache-read-input-tokens start.cache-read-input-tokens))
  (<- write-5m (stated-or end.cache-creation-5m-input-tokens start.cache-creation-5m-input-tokens))
  (<- write-1h (stated-or end.cache-creation-1h-input-tokens start.cache-creation-1h-input-tokens))
  (<- output (stated-or end.output-tokens start.output-tokens))
  (<- searches (stated-or end.web-search-requests start.web-search-requests))
  (<- tier (stated-or end.service-tier start.service-tier))
  (Usage :input-tokens input
         :output-tokens output
         :cache-creation-input-tokens write
         :cache-read-input-tokens read
         :cache-creation-5m-input-tokens (if lifetimes-stated write-5m start.cache-creation-5m-input-tokens)
         :cache-creation-1h-input-tokens (if lifetimes-stated write-1h start.cache-creation-1h-input-tokens)
         :web-search-requests searches
         :service-tier tier))

(defk call-step [#^ tuple open kind]
  {:pre [(: open (get tuple #(CallOpened ...))) (: kind ClaudeLineKind)] :post [(: % CallStep)] :tags {:context "claude-code" :role "judgment"}}
  "1 行(分類した行の種類 kind)を読んだ後の呼びの組みを返すため(節の頭の註): message_start の行は、その親の開いた呼びを新しい呼びに
   替える(前の呼びは閉じずに捨てる — 終わりの来なかった呼び)・message_delta の行は、その親の開いた呼びを閉じる(開いた呼びが無ければ
   何も閉じない — 始まりを読まなかった呼びの usage を発明しない)・ほかの行は組みを変えない。"
  (val partial (isinstance kind PartialMessage))
  (val parent (if partial kind.parent-tool-use-id None))
  (val others (tuple (gfor call open :if (!= call.parent-tool-use-id parent) call)))
  (val started (next (gfor call open :if (= call.parent-tool-use-id parent) call) None))
  (<- usage (closed-usage (if (is started None) None started.usage) (if partial kind.call-end-usage None)))
  (cond
    (and partial (is-not kind.call-opened None)) (CallStep :open (+ others #(kind.call-opened)) :closed None)
    (is usage None) (CallStep :open open :closed None)
    True (CallStep :open others
                   :closed (CallClosed :message-id started.message-id :model started.model :parent-tool-use-id parent
                                       :usage usage))))

(defclass [(dataclass :frozen True)] ThinkingTokens []
  (setv #^ int estimated 0))

(setv INPUT-FATES #("queued" "started" "completed" "cancelled" "discarded" "refused"))
(setv INPUT-FATE-TERMINAL #("completed" "cancelled" "discarded" "refused"))

(defclass [(dataclass :frozen True)] InputFate []
  "入力の行の運命(command_lifecycle)。state は閉語彙 INPUT-FATES。"
  (#^ str ref)
  (#^ str state))

(defclass [(dataclass :frozen True)] PermissionRequested []
  "道具の前の許可の問い(control_request can_use_tool)。答えは ClaudeAnswerPermission。
   input = 道具の入力(鍵の集合は道具ごとに開いているので凍らせた写像)/ suggestions = CLI の提案の JSON の列(凍らせる)。"
  (#^ str request-id)
  (#^ str tool-name)
  (setv #^ FrozenMap input (field :default-factory FrozenMap))
  (setv #^ (get tuple #(object ...)) suggestions #())
  (defn __post_init__ [self]
    (object.__setattr__ self "input" (frozen-json-object self.input "PermissionRequested.input"))
    (when (not (isinstance self.suggestions tuple))
      (raise (TypeError (.format "PermissionRequested.suggestions は tuple: {!r}" self.suggestions))))
    (object.__setattr__ self "suggestions" (freeze-json self.suggestions))))

(defclass [(dataclass :frozen True)] ControlResponse []
  "control_request への CLI の答え(control_response)。request-id = 結ぶ求めの id / subtype = success | error /
   still-queued = interrupt の答えが名指した、生き残る注入の ref(名乗らなければ空)。"
  (#^ str request-id)
  (#^ str subtype)
  (setv #^ (get tuple #(str ...)) still-queued #()))

;; hook の行の段階(閉じた語彙): STARTED = system/hook_started(hook が動き始めた)/ RESPONSE = system/hook_response(hook が応答した)。
;; 途中経過の行(hook_progress)はこの語彙に入れない(Other のまま)。
(defenum HookPhase STARTED RESPONSE)

(defrecord HookNotice
  "CLI が hook を実行した通知の行(system/hook_started・system/hook_response): event = hook のイベント名(hook_event — この欄の無い版は
   hook_name の「:」の前)/ phase = 段階(HookPhase)/ name = hook の名前(hook_name — 「イベント名:matcher」・無ければ空)。入力を
   書かずに事前起動した process が最初の入力の前に出してよい行と、ターンの外で出てよい行を、イベント名で絞るため(dialogue.hy の
   quiet-before-first-input・quiet-outside-turn)。hook-id = hook の 1 回の実行の id(hook_id — 開始と応答で同じ)。応答の行だけが
   運ぶ欄(上の層が hook の結果を会話の画面に出すため・ki-d8b473480303 — CLI 2.1.292 は SessionStart・Setup の
   ほかの hook の行を --include-hook-events の時だけ出す): stdout・stderr = hook の命令の出力(JSON で答える hook の stdout は JSON の
   文字列のまま)/ output = CLI がまとめた出力 / exit-code = 命令の終了の code(名乗らなければ None)/ outcome = CLI の終わり方の語
   (success・error ほか — 語は CLI のまま・名乗らなければ空)。"
  (#^ str event)
  (#^ HookPhase phase)
  (#^ str name)
  (setv #^ str hook-id "")
  (setv #^ str stdout "")
  (setv #^ str stderr "")
  (setv #^ str output "")
  (setv #^ (| int None) exit-code None)
  (setv #^ str outcome ""))

(defclass [(dataclass :frozen True)] TaskEvent []
  "CLI の道具の task の開始と報せ(system/task_started・task_notification)。tool-use-id = task を起こした道具の呼びの id(行の
   tool_use_id — 名乗らなければ None)/ task-type = task の種類(task_started の task_type — subagent は SUBAGENT-TASK-TYPE。報せの行は
   名乗らないので空)/ total-tokens = 報せの行の usage.total_tokens(CLI の値のまま。始まりの行と名乗らない行は None。transcript の
   最後の呼びの 4 種 token の和と合うとは限らない — 本番 2026-10-22 で書き切った transcript の和より +251〜+1,801 多い報せが 13 件中
   10 件在った・card ki-3d45f5857239。transcript を書き切ったかの判定には使わない — subagent-transcript-of)。
   subagent の task の id は、CLI がその subagent の transcript を書く file の名(agent-<id>.jsonl)の id と同じ(実測 2.1.292・
   card ki-e798888dce4e)。"
  (#^ str task-id)
  (#^ str status)
  (setv #^ (| str None) tool-use-id None)
  (setv #^ str task-type "")
  (setv #^ (| int None) total-tokens None))

;; task_started の task_type のうち、subagent(Agent の道具が起こした agent)を名乗る語(実測 2.1.292・card ki-e798888dce4e)。
(val SUBAGENT-TASK-TYPE "local_agent")
;; 報せの行の status のうち、subagent が答え終えた事を名乗る語(実測 2.1.292 — 書き切った transcript は閉じた呼びで終わる)。
(val SUBAGENT-ANSWERED-STATUS "completed")
;; CLI が API を呼ばずに書く assistant の行の model の名(誤りの知らせ — 呼びではないので subagent の呼びに数えない)。
(val SYNTHETIC-MODEL "<synthetic>")
;; 呼びの終わり方(message.stop_reason — API の語)のうち、呼びが続く物: 道具の答えを待つ・server の道具の途中で止めた。名乗らない
;; (null)行も閉じていない。ほかの語(end_turn・max_tokens・stop_sequence・refusal ほか)は答えを閉じた呼び(last-call-closed)。
(val CONTINUING-STOP-REASONS #{"tool_use" "pause_turn"})

;; subagent の transcript の読みの欠けの訳(閉じた語彙 — card ki-e798888dce4e・ki-3d45f5857239): ABSENT = file が無い / UNREADABLE =
;; 読めない所が在る(OS の誤り・task の id が file の名にできない = 何も読めない。UTF-8 でない・JSON の object でない行・呼びの行が
;; 時差つきの刻を名乗らない = その行だけ数えない)/ TRUNCATED = 書きの途中(最後の行が改行で終わっていない・答え終えた subagent の
;; transcript が閉じた呼びで終わらない — CLI は transcript を報せの行より後に書き足す。実測 2.1.292: 報せの行を出した時点の file には
;; 最後の呼びの行がまだ無かった)。
(defenum SubagentTranscriptProblem ABSENT UNREADABLE TRUNCATED)

(defrecord SubagentCall
  "subagent の API の呼び 1 回の消費(transcript の assistant の行を呼びの id で畳んだ物 — 1 つの呼びは content の block ごとに行を書き、
   出力の token は呼びの途中の値なので、usage と model は最後の行の物・刻は最初の行の物)。at = 最初の行の timestamp(CLI の壁の時刻)/
   usage = message.usage / model = message.model(無ければ None)/ call-id = message.id(無ければ None — 無い行は畳まずに 1 回と数える)。"
  (#^ datetime at)
  (#^ Usage usage)
  (setv #^ (| str None) model None)
  (setv #^ (| str None) call-id None))

(defrecord SubagentTranscript
  "subagent 1 つの transcript(<設定 dir>/projects/<cwd>/<会話の id>/subagents/agent-<task の id>.jsonl)を、その subagent の task の
   報せの行(task_notification)を受けた所で読んだ結果(card ki-e798888dce4e)。stdout の行ではなく、handler が報せの行の直後に手番の記録へ
   足す行の種類(raw は空)。背景の task を止めた CLI(argv.hy の PER-TURN-SETTINGS-ENV)は subagent の呼びの行を stdout に出さないので、
   subagent の呼びの消費はここからだけ読む。
   agent-id = task の id / parent-tool-use-id = subagent を起こした道具の呼びの id / calls = 読めた呼び(書かれた順 — 前の報せの後に
   伸びた分だけ)/ problem = 読みの欠けの訳(欠けが無ければ None)/ detail = 欠けの手がかり(path・誤りの文・欠けた行の数 — 欠けが
   無ければ空)。欠けが在っても、読めた呼びは calls に載せる(捨てて 0 にしない — card ki-3d45f5857239)。読めなかった行の呼びは
   発明しない。"
  (#^ str agent-id)
  (#^ str parent-tool-use-id)
  (setv #^ (get tuple #(SubagentCall ...)) calls #())
  (setv #^ (| SubagentTranscriptProblem None) problem None)
  (setv #^ str detail ""))

(defclass [(dataclass :frozen True)] RateLimit []
  "rate_limit_event の行: window = rateLimitType / utilization = その窓の使った割合 / resets-at = resetsAt(epoch 秒)/ status = 限度の
   答えの語(allowed・allowed_warning・rejected — 名乗らなければ空。rejected = 口座の限度に当たって要求が拒まれた・#3983)。"
  (#^ str window)
  (setv #^ (| float None) utilization None)
  (setv #^ (| int None) resets-at None)
  (setv #^ str status ""))

;; 限度の答えの語のうち、口座の限度に当たって要求が拒まれた事を名乗る語(#3983)。
(setv RATE-LIMIT-REJECTED "rejected")
;; assistant の行の error のうち、口座の限度に当たった事を名乗る語(#3983)。
(setv ASSISTANT-ERROR-RATE-LIMIT "rate_limit")

(defclass [(dataclass :frozen True)] AccountLimitHit []
  "手番が口座の限度に当たった事実(#3983 — 上の層が「この口座の枠が尽きた」と知って、枠の残る口座へ付け替えるため)。
   window = 尽きた限度の種類(rateLimitType — 例 five_hour。名乗らなければ None)/ resets-at = 枠が戻る刻(epoch 秒・名乗らなければ None)/
   text = CLI が答えの代わりに出した限度の文(例「You've hit your session limit · resets 7am (UTC)」— 読めなければ空)。
   どの口座かは CLI の行に無い — 手番を起こした上の層が知っている。"
  (setv #^ (| str None) window None)
  (setv #^ (| int None) resets-at None)
  (setv #^ str text ""))

;; assistant の行の error のうち、口座の側が要求を断った事を名乗る語(閉じた集まり — ここ 1 か所)。oauth_org_not_allowed = 口座の組織が
;; Claude Code での subscription の利用を止めている(本番 2026-10-08 22:32 の口座 cryptic-2・apiErrorStatus 403)/ authentication_failed =
;; 口座の資格が通らない / billing_error = 口座の支払いの側の断り。語は CLI の型 SDKAssistantMessageError の語のまま。rate_limit(口座の
;; 限度)はこの集まりに入れない — 限度は AccountLimitHit の道。
(val ACCOUNT-REFUSAL-ERRORS #("oauth_org_not_allowed" "authentication_failed" "billing_error"))

(defrecord AccountRefusalHit
  "手番の要求を口座の側が断った事実(上の層が「この口座は使えない」と知って、ほかの口座へ付け替えるため — 限度で尽きた
   AccountLimitHit とは別の事実)。error = CLI が本体の assistant の行の最上位の error で名乗った語そのまま(ACCOUNT-REFUSAL-ERRORS の
   語だけ — 外の語は作る時に断る)/ text = CLI が答えの代わりに出した断りの文(例「Your organization has disabled Claude subscription
   access for Claude Code · …」— 読めなければ空)。どの口座かは CLI の行に無い — 手番を起こした上の層が知っている。"
  {:check [(in error ACCOUNT-REFUSAL-ERRORS)]}
  (#^ str error)
  (#^ str text))

;; assistant の行の message.stop_reason のうち、model の安全装置が答えを断った事を名乗る語(CLI は API の答えの stop_reason refusal を、
;; 本体の assistant の行 — error invalid_request・本文「API Error: <model>'s safeguards flagged this message …」— に写す。実測 2.1.x の
;; CLI の code: 行の message.stop_reason = refusal・message.stop_details = API の stop_details {category, explanation})。
(val STOP-REASON-REFUSAL "refusal")

(defrecord ModelRefusalHit
  "手番の要求を model の安全装置が断った事実(上の層が「この transcript はこの model で続けられない」と知って、別の model で引き継ぎ書を
   書かせ、新しい session で続けるため — 口座の側の断り AccountRefusalHit とは別の事実: 口座は使える)。category = 断りの種類
   (message.stop_details.category — 例 reasoning_extraction・名乗らなければ None)/ model = 断った行が名乗った model(無ければ None)/
   text = CLI が答えの代わりに出した断りの文(読めなければ空)。"
  (#^ (| str None) category)
  (#^ (| str None) model)
  (#^ str text))

(defclass [(dataclass :frozen True)] ModelWindow []
  "result の行の modelUsage の model 1 つの窓(会話の context の大きさを上限と比べるため — #3744): model = model の名(modelUsage の鍵)/
   context-window = contextWindow / max-output-tokens = maxOutputTokens(名乗らない欄は None — 0 を発明しない)。"
  (#^ str model)
  (setv #^ (| int None) context-window None)
  (setv #^ (| int None) max-output-tokens None))

(defclass [(dataclass :frozen True)] ModelUsage []
  "modelUsage の model 1 つの使用量(card ki-1d986d4f0007)。result の行と transcript の cost-state の行の modelUsage は会話の累積
   (実測 CLI 2.1.292: 同じ process の 2 つ目の result の行は 1 つ目との和、--resume の process も前の値から数え続ける — costUSD の和は
   total_cost_usd と同じ値)。TurnResult と CostMark はその累積を、手番の終わりは手番の分へ直した増分を持つ(per-turn-model-usage)。
   model = modelUsage の鍵 / input-tokens = inputTokens / output-tokens = outputTokens / cache-read-input-tokens = cacheReadInputTokens /
   cache-creation-input-tokens = cacheCreationInputTokens(5 分と 1 時間の内訳は CLI が名乗らない)/ web-search-requests =
   webSearchRequests / cost-usd = costUSD。名乗らない欄は None(0 を発明しない)。窓の欄(contextWindow・maxOutputTokens)は ModelWindow。"
  (#^ str model)
  (setv #^ (| int None) input-tokens None)
  (setv #^ (| int None) output-tokens None)
  (setv #^ (| int None) cache-read-input-tokens None)
  (setv #^ (| int None) cache-creation-input-tokens None)
  (setv #^ (| int None) web-search-requests None)
  (setv #^ (| float None) cost-usd None))

(defclass [(dataclass :frozen True)] CostMark []
  "手番の額の起点(CLI の会話の累積の 1 点 — 前の手番を閉じた result の行・transcript の最後の cost-state の行・新しい会話の 0)。
   額と model ごとの使用量を同じ 1 点で持つ — 手番の額と model ごとの増分を必ず同じ起点から数えるため(card ki-1d986d4f0007)。
   total-usd = 累積の額(total_cost_usd・totalCostUSD)/ models = その点の model ごとの累積(ModelUsage の列 — 新しい会話は空。
   None = その点の modelUsage を知らない: 欄の無い行・古い CLI の cost-state の行)。"
  (#^ float total-usd)
  (setv #^ (| (get tuple #(ModelUsage ...)) None) models None))

;; 新しい会話の起点: CLI は額も model ごとの使用量も 0 から数える。
(val FRESH-MARK (CostMark 0.0 #()))

;; model ごとの額の和と手番の額を照合する時の許し(USD)— CLI の costUSD と total_cost_usd は同じ和の丸めなので、越えるのは数え違い。
(val MODEL-COST-TOLERANCE-USD 0.01)

;; 手番の model ごとの使用量が手番の額と照合できなかった訳(閉じた語彙 — card ki-1d986d4f0007):
;; UNKNOWN-MARK = 起点の model ごとの累積が分からない(額の起点が分からない・起点の行が modelUsage を持たない)/
;; NOT-NAMED = 手番を閉じた result の行が modelUsage を名乗らない(result の行の無い終わりも)/
;; COUNTED-BACK = model ごとの累積が起点より減った・起点に在った model が消えた(CLI が起点から数えていない)/
;; UNPRICED = 手番の額か、ある model の額が名乗られず、和を比べられない /
;; SUM-MISMATCH = model ごとの額の和と手番の額が MODEL-COST-TOLERANCE-USD を越えて違う。
(defenum ModelUsageGap UNKNOWN-MARK NOT-NAMED COUNTED-BACK UNPRICED SUM-MISMATCH)

(defrecord ModelUsageTally
  "手番の model ごとの使用量と照合の結末: models = 手番の分の増分(この手番に使った model だけ — 増分が全部 0 の model は入れない。
   増分を出せなければ None)/ gap = 照合できなかった訳(ModelUsageGap — 照合が合えば None)。"
  (#^ (| (get tuple #(ModelUsage ...)) None) models)
  (#^ (| ModelUsageGap None) gap))

(val MODEL-USAGE-COUNTS #("input_tokens" "output_tokens" "cache_read_input_tokens" "cache_creation_input_tokens"
                           "web_search_requests" "cost_usd"))

(defk model-cost-gap [models #^ (| float None) cost]
  {:pre [(: models (| tuple None)) (: cost (| float None))] :post [(: % (| ModelUsageGap None))]
   :tags {:context "claude-code" :role "judgment"}}
  "手番の model ごとの増分の額の和が手番の額と合うかを決めるため(状態機械と fake が同じ規則で照合する): 増分が無ければ NOT-NAMED・
   手番の額か model の額が無ければ UNPRICED・差が MODEL-COST-TOLERANCE-USD を越えれば SUM-MISMATCH・合えば None。"
  (cond
    (is models None) ModelUsageGap.NOT-NAMED
    (or (is cost None) (any (gfor usage models (is usage.cost-usd None)))) ModelUsageGap.UNPRICED
    (> (abs (- (sum (gfor usage models usage.cost-usd)) cost)) MODEL-COST-TOLERANCE-USD) ModelUsageGap.SUM-MISMATCH
    True None))

(defk usage-at-mark [#^ (get tuple #(ModelUsage ...)) marked #^ str model]
  {:pre [(: marked tuple) (: model str)] :post [(: % ModelUsage)] :tags {:context "claude-code" :role "foundation"}}
  "起点の model ごとの累積から model 1 つの累積を引くため。起点に無い model はまだ使っていない model なので、どの欄も 0 の累積。"
  (next (gfor usage marked :if (= usage.model model) usage)
        (ModelUsage model #** (dfor name MODEL-USAGE-COUNTS name 0))))

(defk model-usage-increment [#^ ModelUsage before #^ ModelUsage after]
  {:pre [(: before ModelUsage) (: after ModelUsage)] :post [(: % (| ModelUsage None))]
   :tags {:context "claude-code" :role "judgment"}}
  "model 1 つの累積 2 点の差を手番の増分にするため: 欄ごとに後 − 前(どちらかが名乗らなければ None)。どれかの欄が減っていれば None
   (CLI が前の点から数えていない)。"
  (val diffs (dfor name MODEL-USAGE-COUNTS
                   :setv b (getattr before name) :setv a (getattr after name)
                   name (if (or (is a None) (is b None)) None (- a b))))
  (if (any (gfor value (.values diffs) (and (is-not value None) (< value 0))))
      None
      (ModelUsage :model after.model #** diffs)))

(defk per-turn-model-usage [#^ (| CostMark None) mark #^ (| (get tuple #(ModelUsage ...)) None) now #^ (| float None) cost]
  {:pre [(: mark (| CostMark None)) (: now (| tuple None)) (: cost (| float None))] :post [(: % ModelUsageTally)]
   :tags {:context "claude-code" :role "judgment"}}
  "手番を閉じた result の行の model ごとの累積(now)を、手番の額と同じ起点(mark)からの増分へ直し、手番の額(cost — 同じ起点の差)と
   照合するため(card ki-1d986d4f0007)。起点に無い model は 0 から数える。増分が全部 0 の model(前の手番だけで使った model)は
   入れない。起点が分からない・now が無い・累積が減った時は増分を出さず、訳だけを返す(推測で埋めない)。"
  (when (or (is mark None) (is mark.models None))
    (return (ModelUsageTally :models None :gap ModelUsageGap.UNKNOWN-MARK)))
  (when (is now None)
    (return (ModelUsageTally :models None :gap ModelUsageGap.NOT-NAMED)))
  (val named (frozenset (gfor usage now usage.model)))
  (var increments [])
  (for [usage now]
    (<- start (usage-at-mark mark.models usage.model))
    (<- increment (model-usage-increment start usage))
    (.append increments increment))
  (when (or (in None increments) (any (gfor usage mark.models (not-in usage.model named))))
    (return (ModelUsageTally :models None :gap ModelUsageGap.COUNTED-BACK)))
  (val used (tuple (gfor increment increments
                         :if (any (gfor name MODEL-USAGE-COUNTS (not-in (getattr increment name) #(None 0))))
                         increment)))
  (<- gap (model-cost-gap used cost))
  (ModelUsageTally :models used :gap gap))

(defk merged-windows [#^ tuple earlier #^ tuple later]
  {:pre [(: earlier tuple) (: later tuple)] :post [(: % (get tuple #(ModelWindow ...)))] :tags {:context "claude-code" :role "foundation"}}
  "1 つの host の手番に読んだ result の行の窓を 1 つの列にするため(状態機械と fake が同じ規則で数える): model ごとに 1 つ、並びは最初に
   名乗った順、値は後の行が名乗った物。"
  (val by-model (dict (gfor window (+ earlier later) #(window.model window))))
  (tuple (.values by-model)))

(defrecord RequestTiming
  "result の行の計時の欄。どれも CLI の手番の時計が動き始めた刻(入力を読んだ刻)からのミリ秒で、名乗らない欄は None(0 を発明しない)。
   time-to-request-ms = time_to_request_ms(model へ要求を送るまで — 入力ごとの hook を含む)/ ttft-stream-ms = ttft_stream_ms
   (message_start を受けるまで)/ first-content-frame-ms = first_content_frame_ms / ttft-ms = ttft_ms / duration-ms = duration_ms /
   duration-api-ms = duration_api_ms(API の呼びの合計)/ request-phases = time_to_request_phases_ms の区間(和が time-to-request-ms
   ちょうど — 例 input_hooks・system_prompt。CLAUDE_CODE_EMIT_STARTUP_TIMING を付けた process だけが出す)。欄の名は CLI のまま
   (実測 2.1.292・#3855)。"
  (setv #^ (| int None) time-to-request-ms None)
  (setv #^ (| int None) ttft-stream-ms None)
  (setv #^ (| int None) first-content-frame-ms None)
  (setv #^ (| int None) ttft-ms None)
  (setv #^ (| int None) duration-ms None)
  (setv #^ (| int None) duration-api-ms None)
  (setv #^ (get tuple #(TimedPhase ...)) request-phases #()))

(defclass [(dataclass :frozen True)] TurnResult []
  "result の行(CLI の手番の終わり)。host の手番の終わりかどうかは状態機械(dialogue.hy)が決める。
   origin-kind = origin.kind(CLI が自分で起こした手番の印)/ result-text = result の本文 /
   usage = 消費の token(この CLI の手番の分)/
   cost-usd = total_cost_usd — 会話の累積の額(USD)で、この行の手番だけの額ではない(実測 2.1.283・#883: 同じ process の
   2 つ目の result の行は 1 つ目の額との和を名乗り、--resume で起こした process は、前の process が降りる時に transcript へ記した額から
   数え続ける — --fork-session の枝も親の transcript の額から数える)。手番の額へ直すのは状態機械(dialogue.hy)/
   api-error-status = API の誤りの HTTP status / input-refs = user_message_uuids(名乗らなければ空)/
   model-windows = modelUsage の model ごとの窓(ModelWindow の列・object の鍵の順 — 無ければ空)/
   model-usage = modelUsage の model ごとの累積の使用量(ModelUsage の列・object の鍵の順 — 行が modelUsage の object を持たなければ
   None。手番の分へ直すのは状態機械 — card ki-1d986d4f0007)/ timing = 計時の欄(RequestTiming)/ structured-output = structured_output(--json-schema を付けて起こした CLI の構造化の答え —
   深く凍らせた JSON の値。名乗らない・null なら None)。"
  (#^ str subtype)
  (#^ bool is-error)
  (setv #^ str terminal-reason "")
  (setv #^ str origin-kind "")
  (setv #^ str result-text "")
  (setv #^ Usage usage (field :default-factory Usage))
  (setv #^ (| float None) cost-usd None)
  (setv #^ (| int None) api-error-status None)
  (setv #^ (get tuple #(str ...)) input-refs #())
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #())
  (setv #^ (| (get tuple #(ModelUsage ...)) None) model-usage None)
  (setv #^ RequestTiming timing (field :default-factory RequestTiming))
  (setv #^ FrozenJson structured-output None))

;; Stop hook が答えを差し戻した時に CLI が手番へ注入する行の本文の頭(実測 CLI 2.1.292・#4020)。
(val STOP-HOOK-FEEDBACK-HEAD "Stop hook feedback:")

(defrecord StopHookFeedback
  "Stop hook が手番の答えを差し戻した事実(#4020): CLI は差し戻しの理由を手番へ注入して答え直させる — isSynthetic の user の行で、本文の
   text の block が STOP-HOOK-FEEDBACK-HEAD で始まる(実測 CLI 2.1.292・旗なしの既定の出力。同じ時に出る system/notification の行
   key stop-hook-error は印に使わない — 差し戻し以外の hook の誤りでも出るかが分からない)。reason = 頭の後の理由(前後の空白を外す)。
   この行より前で、手番の最後の道具の結果より後の本文は差し戻された答え(Stop hook は道具を呼ばずに終えた応答の後にだけ走る)。"
  (#^ str reason))

;; 会話の圧縮の起き方(compact_metadata.trigger の閉じた語彙 — CLI 2.1.294 の書き手は auto と manual だけを書く): AUTO = CLI が自分の
;; 閾値で圧縮した / MANUAL = /compact の命令で圧縮した。
(defenum CompactTrigger AUTO MANUAL)

(defrecord CompactBoundary
  "CLI が会話の context を圧縮した境目の行(system/compact_boundary — #4189)。上の層が、無人のターンで CLI が会話を
   圧縮した事をターンの出来事として記録するため。欄は CLI 2.1.294 の stream-json の compact_metadata(会話の記録の compactMetadata を
   snake の名へ写した物)のうち: trigger = 起き方(CompactTrigger)/ pre-tokens = 圧縮の前の context の token 数(pre_tokens — CLI が
   必ず書く)/ post-tokens = 圧縮の後の token 数(post_tokens)/ cumulative-dropped-tokens = その会話で圧縮が落とした token 数の累計
   (cumulative_dropped_tokens — 実測 2.1.287 の同じ会話の 2 回目の値 = 1 回目と 2 回目の落とした数の和)/ duration-ms = 圧縮にかかった
   ミリ秒(duration_ms)。後の 3 つは CLI が書いた時だけ(書かなければ None — 0 を発明しない)。preserved_segment(残した区間の uuid の
   組)・user_context などは持たない。圧縮は手番の途中の出来事で、手番は続く(状態機械 dialogue.hy はこの行で何もしない)。"
  (#^ CompactTrigger trigger)
  (#^ int pre-tokens)
  (setv #^ (| int None) post-tokens None)
  (setv #^ (| int None) cumulative-dropped-tokens None)
  (setv #^ (| int None) duration-ms None))

;; CLI が自動の圧縮を始めた時に出す system/status の行の状態の語(CLI 2.1.294 — 替え玉の CLI tests/stub_cli/claude.hy が #4189 で写した形)。
(val COMPACTING-STATUS "compacting")

(defrecord CompactStarted
  "CLI が会話の context の圧縮を始めた行(system/status の status = compacting — ki-aeae6f9e643d)。上の層が、圧縮の間
   (要約を作る数十秒〜数分 — 実測 2026-10-10 の 2 分)会話の画面に圧縮中を出すため。終わりは CompactBoundary(compact_boundary)。
   行は token 数も起き方も持たない(それは終わりの行が持つ)。status の他の状態(requesting など)は今までどおり Other。")

;; 会話の圧縮の終わり方(閉じた語彙 — ki-8239be27d5f6)。CLI 2.1.292 は圧縮の関数の終わり(finally)で必ず
;; system/status の行(status = null)を 1 つ出し、圧縮を試みた時は compact_result(success / failed)と、失敗なら compact_error(誤りの文)を
;; 添える: SUCCEEDED = compact_result success / FAILED = compact_result failed(誤りの文は compact_error)/ INTERRUPTED = compact_result failed
;; で、誤りの文が止めの文(COMPACT-ABORTED-ERROR — 圧縮の要求が止められた)/ NOT-COMPACTED = compact_result の無い status null(圧縮を
;; 始めたが圧縮しなかった — PreCompact の hook が止めた・圧縮する物が無かった)。
(defenum CompactOutcome SUCCEEDED FAILED INTERRUPTED (NOT-COMPACTED "not_compacted"))

;; 圧縮の要求が止められた時の compact_error の文(CLI 2.1.292 の止めの誤りの文 — 同じ CLI は この文の失敗だけ圧縮の誤りの知らせ
;; を出さない)。
(val COMPACT-ABORTED-ERROR "API Error: Request was aborted.")

(defrecord CompactEnded
  "CLI が会話の context の圧縮を終えた行(system/status の status = null — ki-8239be27d5f6)。上の層が、圧縮の
   成功だけでなく失敗・中断・圧縮しなかった事も手番の出来事として記録するため(成功の中身 — token 数・起き方 — は CompactBoundary が持つ)。
   outcome = 終わり方(CompactOutcome)/ error = CLI が名乗った誤りの文(compact_error — 失敗と中断の時だけ・無ければ None)。
   圧縮の要求 1 回の token(入力・出力・cache)は CLI 2.1.292 が stream-json のどの行にも名乗らない(この行にも compact_boundary の
   compact_metadata にも usage の欄は無い — CLI の中では compactionUsage として持つが出さない)ので、この型も持たない。
   CLI 2.1.292 の status null の行は圧縮の終わりでしか出ない(書き手の 9 か所はどれも圧縮の終わり)。"
  (#^ CompactOutcome outcome)
  (setv #^ (| str None) error None))

(defclass [(dataclass :frozen True)] Other []
  "語彙の外の行(名前だけ持つ)。"
  (#^ str type)
  (setv #^ str subtype ""))

(setv ClaudeLineKind (| Init AssistantMessage ToolResult PartialMessage ThinkingTokens InputFate
                        PermissionRequested ControlResponse TaskEvent HookNotice RateLimit StopHookFeedback CompactStarted CompactEnded
                        CompactBoundary TurnResult SubagentTranscript CallClosed Other))

(defclass [(dataclass :frozen True)] ClaudeStreamLine []
  "stdout の 1 行。seq は会話の中で単調増加・at は読んだ時刻(doeff-time の時計)・raw は 1 行の逐語(stdout の行でない
   SubagentTranscript は空)。"
  (#^ int seq)
  (#^ datetime at)
  (#^ ClaudeLineKind kind)
  (#^ str raw))


;; --- 手番の終わり ---------------------------------------------------------------------------------
;;
;; どの終わりも、会話の今の context の大きさを出すための 3 欄を持つ(#3744 — 状態機械 dialogue.hy の ended が 1 か所で載せる):
;; last-call-usage = この手番の本体の会話(parent_tool_use_id が null)の最後の assistant の行の usage(最後の API の呼び 1 回の消費 —
;; 今の context の大きさは入力の側 input・cache_read・cache_creation の和。出力の token は呼びの途中の値)/ last-call-model = その行の
;; model / model-windows = この手番に読んだ result の行の modelUsage の窓(model ごとに 1 つ)。手番の途中で終わった時は、それまでに
;; 読んだ値(本体の assistant の行が無ければ None・result の行が無ければ空 — 0 を発明しない)。
;;
;; CLI が終えた手番(Completed・Failed)は、手番の model ごとの使用量と照合の結末を持つ(card ki-1d986d4f0007 — 状態機械 dialogue.hy の
;; end-of-result が per-turn-model-usage の 1 か所で載せる): model-usage = 手番を閉じた result の行の modelUsage の累積を、額と同じ起点
;; (CostMark)からの増分へ直した物(ModelUsage の列 — この手番に使った model だけ。出せなければ None)/ model-usage-gap = model ごとの額の
;; 和と手番の額 cost-usd を照合できなかった訳(ModelUsageGap — 合えば None。既定は NOT-NAMED = modelUsage を名乗らない終わり)。止めた・
;; 消えた手番は額と同じく持たない(止めた手番の分は次の手番の起点へ動かして捨てる)。
;;
;; 手番の途中で CLI の process が降りた終わり(BackendLost と、注入を待って飲んだ result の Failed)は、process の終了 code と stderr の
;; 末尾を欄で持つ(#4207 — 文 detail の中だけでなく、上の層が型の欄で「なぜ降りたか」を読めるように)。欄の値は状態機械 dialogue.hy の
;; on-exit が 1 か所で載せる。stderr の末尾は STDERR-TAIL-CHARS 字まで(越えた分は頭を捨てる — stderr-tail-within)。

;; 手番の終わりの欄 stderr-tail に載せる stderr の末尾の字数の上限(#4207 — ここ 1 か所。状態機械・fake・doeff-agents の adapter が同じ
;; 上限で切る)。
(val STDERR-TAIL-CHARS 2000)

(defk stderr-tail-within [#^ str text]
  {:pre [(: text str)] :post [(: % str) (<= (len %) STDERR-TAIL-CHARS)] :tags {:context "claude-code" :role "foundation"}}
  "手番の終わりの欄に載せる stderr の末尾を、字数の上限 STDERR-TAIL-CHARS の内に収めるため(上の層の記録へ運ぶ値の大きさを決める):
   上限を越えた文は頭を捨てて末尾の STDERR-TAIL-CHARS 字を残し、上限の内の文はそのまま返す。"
  (if (> (len text) STDERR-TAIL-CHARS)
      (cut text (- (len text) STDERR-TAIL-CHARS) None)
      text))

(defclass [(dataclass :frozen True)] Completed []
  "CLI が誤りなく終えた手番。usage = この手番に読んだ result の行の消費の token の和(Usage)/
   cost-usd = この手番の額(USD)= CLI が名乗った累積の額(total_cost_usd)の、手番の始まりから終わりまでの差(状態機械 dialogue.hy が
   数える)。始まりか終わりの額が分からなければ None(0 を発明しない)/ last-call-usage・last-call-model・model-windows は節の頭の註 /
   account-limit = この手番が口座の限度に当たった事実(AccountLimitHit — 当たっていなければ None・#3983)/
   account-refusal = この手番の要求を口座の側が断った事実(AccountRefusalHit — 断られていなければ None)/
   model-refusal = この手番の要求を model の安全装置が断った事実(ModelRefusalHit — 断られていなければ None)/
   model-usage・model-usage-gap は節の頭の註。"
  (setv #^ str result-text "")
  (setv #^ Usage usage (field :default-factory Usage))
  (setv #^ (| float None) cost-usd None)
  (setv #^ (| (get tuple #(ModelUsage ...)) None) model-usage None)
  (setv #^ (| ModelUsageGap None) model-usage-gap ModelUsageGap.NOT-NAMED)
  (setv #^ (get tuple #(str ...)) input-refs #())
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #())
  (setv #^ (| AccountLimitHit None) account-limit None)
  (setv #^ (| AccountRefusalHit None) account-refusal None)
  (setv #^ (| ModelRefusalHit None) model-refusal None))

(defclass [(dataclass :frozen True)] Failed []
  "CLI が誤りで終えた手番。detail = CLI が名乗った文(無ければ subtype)・api-error-status = API の誤りの HTTP status・
   usage = 誤りの前に消費した token(result の行が名乗った物 — 注入の断りのように result の行が無い終わりは空の Usage)・
   cost-usd = 誤りの前に使った額(USD — 数え方は Completed と同じ。result の行が無い終わり・額が分からない時は None)/
   last-call-usage・last-call-model・model-windows は節の頭の註 / account-limit(#3983)・account-refusal・model-refusal は Completed と同じ /
   exit-code・stderr-tail = 注入を待って飲んだ result で終わる前に CLI の process が降りた時の、process の終了 code と stderr の末尾
   (STDERR-TAIL-CHARS 字まで — #4207)。result の行で閉じた失敗(process は降りていない)・注入の断りの終わりは None /
   model-usage・model-usage-gap は節の頭の註(result の行の無い終わりは None と NOT-NAMED)。"
  (#^ str detail)
  (setv #^ (| int None) api-error-status None)
  (setv #^ str terminal-reason "")
  (setv #^ Usage usage (field :default-factory Usage))
  (setv #^ (| float None) cost-usd None)
  (setv #^ (| (get tuple #(ModelUsage ...)) None) model-usage None)
  (setv #^ (| ModelUsageGap None) model-usage-gap ModelUsageGap.NOT-NAMED)
  (setv #^ (get tuple #(str ...)) input-refs #())
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #())
  (setv #^ (| AccountLimitHit None) account-limit None)
  (setv #^ (| AccountRefusalHit None) account-refusal None)
  (setv #^ (| ModelRefusalHit None) model-refusal None)
  (setv #^ (| int None) exit-code None)
  (setv #^ (| str None) stderr-tail None))

(defclass [(dataclass :frozen True)] Interrupted []
  "止めた手番の終わり。process-kept = 同じ CLI の process が会話に残り次の手番も使うか(control の止め — 真)、止めと一緒に
   process が降りたか(SIGINT の形・会話を閉じる止め・止めの途中で process が消えた — 偽。#3672 の決め 6)— 既定値を置かない
   (作り手が必ず名乗る)・surviving-refs = CLI が次の手番として走らせる入力(continued-by がその手番)・dropped-refs = 読まれずに
   捨てられた入力 / last-call-usage・last-call-model・model-windows は節の頭の註。"
  (#^ bool process-kept)
  (setv #^ (get tuple #(str ...)) surviving-refs #())
  (setv #^ (get tuple #(str ...)) dropped-refs #())
  (setv #^ (| ClaudeTurn None) continued-by None)
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #()))

(defclass [(dataclass :frozen True)] BackendLost []
  "終わりの行を読む前に process が消えた(OOM・kill・host の再起動)。次の手番は同じ ResumeSession で頼めばよい /
   last-call-usage・last-call-model・model-windows は節の頭の註(消える前に読んだ値)/
   exit-code = 降りた process の終了 code(信号で降りたなら負の数)/ stderr-tail = その process の stderr の末尾(STDERR-TAIL-CHARS 字
   まで・空の stderr は空の文字列 — #4207)。process の降り方を知らない終わり(handler が手番を知らない・fake が消した process)は None。"
  (#^ str detail)
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #())
  (setv #^ (| int None) exit-code None)
  (setv #^ (| str None) stderr-tail None))

(setv ClaudeTurnEnd (| Completed Failed Interrupted BackendLost))


;; --- 分類(純関数) ------------------------------------------------------------------------------

(defn #^ dict object-at [value #^ str key]
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (isinstance inner dict) inner {}))

(defn #^ str text-at [value #^ str key]
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (isinstance inner str) inner ""))

(defn #^ tuple strings-at [value #^ str key]
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (isinstance inner list) (tuple (gfor item inner :if (isinstance item str) item)) #()))

(defn #^ (| int None) int-at [value #^ str key]
  "整数ちょうど(bool・文字列・小数は数えない)。"
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (and (isinstance inner int) (not (isinstance inner bool))) inner None))

(defn #^ (| float None) number-at [value #^ str key]
  "数(整数か小数 — bool は数えない)を float で。無ければ None。"
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (and (isinstance inner #(int float)) (not (isinstance inner bool))) (float inner) None))

(defn #^ Usage usage-of [#^ dict usage]
  "result の行の usage の object → Usage(名乗らない欄は None)。"
  (setv cache (object-at usage "cache_creation"))
  (setv tier (.get usage "service_tier"))
  (Usage :input-tokens (int-at usage "input_tokens")
         :output-tokens (int-at usage "output_tokens")
         :cache-creation-input-tokens (int-at usage "cache_creation_input_tokens")
         :cache-read-input-tokens (int-at usage "cache_read_input_tokens")
         :cache-creation-5m-input-tokens (int-at cache "ephemeral_5m_input_tokens")
         :cache-creation-1h-input-tokens (int-at cache "ephemeral_1h_input_tokens")
         :web-search-requests (int-at (object-at usage "server_tool_use") "web_search_requests")
         :service-tier (if (isinstance tier str) tier None)))

(defn #^ (| dict None) parse-record [#^ str line]
  "stdout の 1 行 → JSON の object(壊れた行・object でない行は None)。"
  (setv stripped (.strip line))
  (when (not stripped) (return None))
  (try
    (setv value (json.loads stripped))
    (except [ValueError] (return None)))
  (if (isinstance value dict) value None))

;; JSON Lines の行の終わり(card ki-3d45f5857239 — 割るのはこの 1 字だけ)。
(val JSONL-LINE-END "\n")

(defk jsonl-lines [#^ str text]
  {:pre [(: text str)] :post [(: % tuple)] :tags {:context "claude-code" :role "foundation"}}
  "CLI が書く JSON Lines(会話と subagent の transcript・stream-json の stdout)の本文を行の列にする唯一の所(transcript を読む上の層も
   これで割る)。割るのは改行(JSONL-LINE-END)だけ: str.splitlines は JSON の文字列の中に生の
   まま書かれた U+2028・U+2029(JSON は escape を求めない — web の本文に在る)や \\x1c〜\\x1e・\\x85・\\v・\\f でも割り、正しい
   JSON の行を読めない 2 行にする(実測: 本番の subagent の transcript agent-a20aa06… の U+2028 4 つで、読みが UNREADABLE になった)。
   空白だけの行は落とす。最後の改行の後の切れ端も 1 行として答える(書き切ったかは読み手が見る — whole-lines-end)。"
  (tuple (gfor line (.split text JSONL-LINE-END) :if (.strip line) line)))

(defk whole-lines-end [#^ bytes data]
  {:pre [(: data bytes)] :post [(: % int)] :tags {:context "claude-code" :role "foundation"}}
  "書き足されている JSON Lines の byte のうち、改行で閉じた行の終わり(最後の JSONL-LINE-END の次の byte の位置 — 無ければ 0)を
   知るため。読み手はここまでを読み終えた所とし、後ろの切れ端(書きの途中の行)は次の読みで頭から読む。UTF-8 の多 byte の字は
   0x0A を含まないので、byte で探して字の途中を切らない。"
  (+ (.rfind data (.encode JSONL-LINE-END "utf-8")) 1))

(defn #^ tuple content-blocks [#^ dict record]
  (setv content (.get (object-at record "message") "content"))
  (if (isinstance content list) (tuple (gfor block content :if (isinstance block dict) block)) #()))

(defn classify-assistant [#^ dict record]
  "assistant の行 → AssistantMessage。tool_use の block は id・name・input を読んで ToolCall にする。id か name が無い・空の、または
   input が無い・JSON の object でない tool_use の block を 1 つでも持つ行は、名指しの Other(type = assistant・subtype =
   tool_use_without_id / tool_use_without_name / tool_use_without_input)で断る — 空の id の ToolCall を作らない(結果の tool_use_id と
   突き合わせられない呼びを通さない)・命令の無い呼びを空の命令として通さない。
   message.usage(object の時だけ — usage-of で読む)・message.model・message.id・行の最上位の parent_tool_use_id・message.stop_reason・
   message.stop_details.category も読む(無い・空なら None)。"
  (setv blocks (content-blocks record))
  (setv uses (tuple (gfor block blocks :if (= (.get block "type") "tool_use") block)))
  (setv message (object-at record "message"))
  (setv usage (.get message "usage"))
  (cond
    (any (gfor block uses (not (text-at block "id")))) (Other :type "assistant" :subtype "tool_use_without_id")
    (any (gfor block uses (not (text-at block "name")))) (Other :type "assistant" :subtype "tool_use_without_name")
    (any (gfor block uses (not (isinstance (.get block "input") dict)))) (Other :type "assistant" :subtype "tool_use_without_input")
    True (AssistantMessage
           :text (.join "" (gfor block blocks :if (= (.get block "type") "text") (text-at block "text")))
           :tool-calls (tuple (gfor block uses (ToolCall :id (text-at block "id") :name (text-at block "name")
                                                         :input (frozen-json-object (get block "input") "tool_use の block の input"))))
           :usage (if (isinstance usage dict) (usage-of usage) None)
           :model (or (text-at message "model") None)
           :parent-tool-use-id (or (text-at record "parent_tool_use_id") None)
           :message-id (or (text-at message "id") None)
           :error (or (text-at record "error") None)
           :stop-reason (or (text-at message "stop_reason") None)
           :refusal-category (or (text-at (object-at message "stop_details") "category") None))))

(defk tool-answer-of [#^ dict block]
  {:pre [(: block dict)] :post [(: % ToolAnswer)] :tags {:context "claude-code" :role "foundation"}}
  "tool_result の block 1 つ → ToolAnswer。content は文字列(本文そのまま)・block の列(text の block の本文を改行で連ね、text でない
   block は種類の名を non-text-kinds へ)・無い(空の本文)のどれか。それ以外の値(写像・数)は 1 つの text でない中身として種類の名を残す。"
  (val content (.get block "content"))
  (val parts (cond
               (is content None) #()
               (isinstance content str) #({"type" "text" "text" content})
               (isinstance content list) (tuple content)
               True #(content)))
  (ToolAnswer :id (text-at block "tool_use_id")
              :text (.join "\n" (gfor part parts :if (= (text-at part "type") "text") (text-at part "text")))
              :is-error (is (.get block "is_error") True)
              :non-text-kinds (tuple (gfor part parts :if (!= (text-at part "type") "text") (text-at part "type")))))

(defn classify-user [#^ dict record]
  "user の行 → ToolResult(tool_result の block ごとに ToolAnswer)。isSynthetic の行で本文が STOP-HOOK-FEEDBACK-HEAD で始まる行は
   StopHookFeedback(#4020)。どちらでもない行は Other(type = user)。tool_use_id が無い・空の tool_result を 1 つでも持つ行は、
   名指しの Other(subtype = tool_result_without_id)で断る — どの呼びへの答えか分からない結果を通さない。"
  (setv results (tuple (gfor block (content-blocks record) :if (= (.get block "type") "tool_result") block))
        said (.join "\n" (gfor block (content-blocks record) :if (= (.get block "type") "text") (text-at block "text"))))
  (cond
    (and (not results) (is (.get record "isSynthetic") True) (.startswith said STOP-HOOK-FEEDBACK-HEAD))
    (StopHookFeedback :reason (.strip (cut said (len STOP-HOOK-FEEDBACK-HEAD) None)))
    (not results) (Other :type "user")
    (any (gfor block results (not (text-at block "tool_use_id")))) (Other :type "user" :subtype "tool_result_without_id")
    True (ToolResult :answers (tuple (gfor block results (run (tool-answer-of block)))))))

(defk hook-notice-of [#^ dict record #^ HookPhase phase]
  {:pre [(: record dict) (: phase HookPhase)] :post [(: % HookNotice)] :tags {:context "claude-code" :role "foundation"}}
  "hook の開始と応答の行を、hook のイベント名で読めるようにするため(ターンの外と最初の入力の前に許す行を名前で絞る — dialogue.hy)と、
   応答の行の hook の結果(出力・exit code・終わり方)を上の層へ運ぶため。イベント名は hook_event、この欄の無い版は
   hook_name(「イベント名:matcher」)の「:」の前。"
  (val name (text-at record "hook_name"))
  (HookNotice :event (or (text-at record "hook_event") (get (.partition name ":") 0)) :phase phase :name name
              :hook-id (text-at record "hook_id") :stdout (text-at record "stdout") :stderr (text-at record "stderr")
              :output (text-at record "output") :exit-code (int-at record "exit_code") :outcome (text-at record "outcome")))

(defk whole-ms-of [number]
  {:pre [(: number (| float None))] :post [(: % (| int None))] :tags {:context "claude-code" :role "foundation"}}
  "CLI が小数で名乗る刻(epoch ミリ秒)を、計時の行の他の刻と同じく床へ丸めた整数にするため(無ければ None)。"
  (if (is number None) None (math.floor number)))

(defk timed-phases-of [#^ dict phases #^ dict starts]
  {:pre [(: phases dict) (: starts dict)] :post [(: % (get tuple #(TimedPhase ...)))] :tags {:context "claude-code" :role "foundation"}}
  "CLI の区間の写像(名 → ミリ秒の整数)を TimedPhase の列(object の鍵の順)にするため。starts = 名 → 始まりのミリ秒(名乗らない名は
   None)。空の名・長さが整数でない名は飛ばす(0 を発明しない)。"
  (tuple (gfor #(name _) (.items phases)
               :if (and (isinstance name str) name (is-not (int-at phases name) None))
               (TimedPhase :name name :ms (int-at phases name) :start-ms (int-at starts name)))))

(defk request-timing-of [#^ dict record]
  {:pre [(: record dict)] :post [(: % RequestTiming)] :tags {:context "claude-code" :role "foundation"}}
  "result の行の計時の欄を RequestTiming にするため(名乗らない欄は None・区間は空)。"
  (<- phases (timed-phases-of (object-at record "time_to_request_phases_ms") {}))
  (RequestTiming :time-to-request-ms (int-at record "time_to_request_ms")
                 :ttft-stream-ms (int-at record "ttft_stream_ms")
                 :first-content-frame-ms (int-at record "first_content_frame_ms")
                 :ttft-ms (int-at record "ttft_ms")
                 :duration-ms (int-at record "duration_ms")
                 :duration-api-ms (int-at record "duration_api_ms")
                 :request-phases phases))

(defk compact-boundary-of [#^ dict record]
  {:pre [(: record dict)] :post [(: % (| CompactBoundary Other))] :tags {:context "claude-code" :role "foundation"}}
  "上の層が会話の圧縮をターンの出来事として記録できるように、system/compact_boundary の行の compact_metadata を CompactBoundary に
   するため。起き方が語彙(CompactTrigger)の外の行と、圧縮の前の token 数(整数)の無い行は、名指しの Other(subtype =
   compact_boundary_unknown_trigger / compact_boundary_without_pre_tokens)で断る — 既定の起き方や 0 の token 数を発明しない。"
  (val metadata (object-at record "compact_metadata"))
  (val trigger (text-at metadata "trigger"))
  (val pre-tokens (int-at metadata "pre_tokens"))
  (cond
    (not-in trigger (frozenset (gfor known CompactTrigger known.value))) (Other :type "system" :subtype "compact_boundary_unknown_trigger")
    (is pre-tokens None) (Other :type "system" :subtype "compact_boundary_without_pre_tokens")
    True (CompactBoundary :trigger (CompactTrigger trigger)
                          :pre-tokens pre-tokens
                          :post-tokens (int-at metadata "post_tokens")
                          :cumulative-dropped-tokens (int-at metadata "cumulative_dropped_tokens")
                          :duration-ms (int-at metadata "duration_ms"))))

(defk compact-ended-of [#^ dict record]
  {:pre [(: record dict)] :post [(: % (| CompactEnded Other))] :tags {:context "claude-code" :role "foundation"}}
  "上の層が圧縮の終わり方(成功・失敗・中断・圧縮しなかった)を手番の出来事として記録できるように、status null の system/status の行を
   CompactEnded にするため。compact_result が語彙(success / failed)の外の行は、名指しの Other(subtype = compact_end_unknown_result)で
   断る — 既定の終わり方を発明しない。"
  (val result (.get record "compact_result"))
  (val error (or (text-at record "compact_error") None))
  (cond
    (is result None) (CompactEnded :outcome CompactOutcome.NOT-COMPACTED)
    (= result "success") (CompactEnded :outcome CompactOutcome.SUCCEEDED)
    (and (= result "failed") (= error COMPACT-ABORTED-ERROR)) (CompactEnded :outcome CompactOutcome.INTERRUPTED :error error)
    (= result "failed") (CompactEnded :outcome CompactOutcome.FAILED :error error)
    True (Other :type "system" :subtype "compact_end_unknown_result")))

(defn classify-system [#^ dict record]
  (setv subtype (text-at record "subtype"))
  (setv startup (object-at record "startup_timing"))
  (cond
    (= subtype "init")
      (Init :session-id (text-at record "session_id")
            :capabilities (strings-at record "capabilities")
            :model (text-at record "model")
            :permission-mode (text-at record "permissionMode")
            :mcp-servers (tuple (gfor server (or (.get record "mcp_servers") [])
                                      :if (isinstance server dict) (text-at server "name")))
            :startup-phases (run (timed-phases-of (object-at startup "phases") (object-at startup "phase_start_ms")))
            :startup-origin-ms (run (whole-ms-of (number-at startup "time_origin_ms"))))
    (= subtype "thinking_tokens")
      (ThinkingTokens :estimated (or (int-at record "estimated_tokens") 0))
    (= subtype "task_started")
      (TaskEvent :task-id (text-at record "task_id") :status "started" :tool-use-id (or (text-at record "tool_use_id") None)
                 :task-type (text-at record "task_type"))
    (= subtype "task_notification")
      (TaskEvent :task-id (text-at record "task_id") :status (or (text-at record "status") "notified")
                 :tool-use-id (or (text-at record "tool_use_id") None)
                 :total-tokens (int-at (object-at record "usage") "total_tokens"))
    (= subtype "hook_started") (run (hook-notice-of record HookPhase.STARTED))
    (= subtype "hook_response") (run (hook-notice-of record HookPhase.RESPONSE))
    (= subtype "compact_boundary") (run (compact-boundary-of record))
    ;; 圧縮の始まり(card ki-aeae6f9e643d)。status の他の状態は名前だけの Other のまま。
    (and (= subtype "status") (= (text-at record "status") COMPACTING-STATUS)) (CompactStarted)
    ;; 圧縮の終わり(status が JSON の null — 鍵の無い行は数えない・card ki-8239be27d5f6)。
    (and (= subtype "status") (in "status" record) (is (get record "status") None)) (run (compact-ended-of record))
    True (Other :type "system" :subtype subtype)))

(defn classify-rate-limit [#^ dict record]
  (setv info (object-at record "rate_limit_info"))
  (setv window (text-at info "rateLimitType"))
  (setv utilization (.get (object-at (object-at info "unifiedWindows") window) "utilization"))
  (RateLimit :window window
             :utilization (if (and (isinstance utilization #(int float)) (not (isinstance utilization bool)))
                              (float utilization) None)
             :resets-at (int-at info "resetsAt")
             :status (or (text-at info "status") "")))

(defn classify-control-request [#^ dict record]
  (setv request (object-at record "request"))
  (if (= (text-at request "subtype") "can_use_tool")
      (PermissionRequested :request-id (text-at record "request_id")
                           :tool-name (text-at request "tool_name")
                           :input (frozen-json-object (object-at request "input") "control_request の request.input")
                           :suggestions (tuple (or (.get request "permission_suggestions") [])))
      (Other :type "control_request" :subtype (text-at request "subtype"))))

(defn classify-control-response [#^ dict record]
  (setv response (object-at record "response"))
  (ControlResponse :request-id (text-at response "request_id")
                   :subtype (text-at response "subtype")
                   :still-queued (strings-at (object-at response "response") "still_queued")))

(defk model-windows-of [#^ dict model-usage]
  {:pre [(: model-usage dict)] :post [(: % (get tuple #(ModelWindow ...)))] :tags {:context "claude-code" :role "foundation"}}
  "result の行の modelUsage(model の名 → その model の数の object)→ ModelWindow の列(object の鍵の順)— 会話の context の大きさを
   model の窓と比べるため。値が object でない model・空の名は飛ばす。"
  (tuple (gfor #(model numbers) (.items model-usage) :if (and (isinstance model str) model (isinstance numbers dict))
               (ModelWindow :model model
                            :context-window (int-at numbers "contextWindow")
                            :max-output-tokens (int-at numbers "maxOutputTokens")))))

(defk model-usage-of [record]
  {:pre [(: record dict)] :post [(: % (| (get tuple #(ModelUsage ...)) None))] :tags {:context "claude-code" :role "foundation"}}
  "行(result の行・transcript の cost-state の行 — 同じ欄の名)の modelUsage を model ごとの累積の使用量(ModelUsage の列・object の鍵の順)
   にするため(card ki-1d986d4f0007)。行が modelUsage の object を持たなければ None(空の object は空の列 — まだ何の model も使っていない)。
   値が object でない model・空の名は飛ばす。"
  (val model-usage (.get record "modelUsage"))
  (if (isinstance model-usage dict)
      (tuple (gfor #(model numbers) (.items model-usage) :if (and (isinstance model str) model (isinstance numbers dict))
                   (ModelUsage :model model
                               :input-tokens (int-at numbers "inputTokens")
                               :output-tokens (int-at numbers "outputTokens")
                               :cache-read-input-tokens (int-at numbers "cacheReadInputTokens")
                               :cache-creation-input-tokens (int-at numbers "cacheCreationInputTokens")
                               :web-search-requests (int-at numbers "webSearchRequests")
                               :cost-usd (number-at numbers "costUSD"))))
      None))

(defn classify-result [#^ dict record]
  (TurnResult :model-windows (run (model-windows-of (object-at record "modelUsage")))
              :model-usage (run (model-usage-of record))
              :subtype (text-at record "subtype")
              :is-error (is (.get record "is_error") True)
              :terminal-reason (text-at record "terminal_reason")
              :origin-kind (text-at (object-at record "origin") "kind")
              :result-text (text-at record "result")
              :usage (usage-of (object-at record "usage"))
              :cost-usd (number-at record "total_cost_usd")
              :api-error-status (int-at record "api_error_status")
              :input-refs (strings-at record "user_message_uuids")
              :timing (run (request-timing-of record))
              :structured-output (freeze-json (.get record "structured_output"))))

(defn classify-lifecycle [#^ dict record]
  (setv state (text-at record "state"))
  (if (in state INPUT-FATES)
      (InputFate :ref (text-at record "command_uuid") :state state)
      (Other :type "command_lifecycle" :subtype state)))

(defk delta-kind-of [#^ str delta-type]
  {:pre [(: delta-type str)] :post [(: % DeltaKind)] :tags {:context "claude-code" :role "foundation"}}
  "stream_event の delta の type の名 → DeltaKind(手番の終わりの計時の行が差分の種類ごとに数えるため — 名の無い delta は NO-DELTA・
   知らない名は OTHER)。"
  (match delta-type
    "" DeltaKind.NO-DELTA
    "text_delta" DeltaKind.TEXT
    "thinking_delta" DeltaKind.THINKING
    "input_json_delta" DeltaKind.TOOL-INPUT
    _ DeltaKind.OTHER))

(defn tool-start-of [#^ dict event]
  "stream_event の event が tool_use の block の始まり(content_block_start)なら、その呼びの id と道具の名(命令はまだ無い)を読むため。
   ほかの event・id か名の欠けた block は None(空の欄で ToolCall を作らない)。"
  (setv block (object-at event "content_block"))
  (if (and (= (text-at event "type") "content_block_start") (= (text-at block "type") "tool_use")
           (text-at block "id") (text-at block "name"))
      (ToolCall (text-at block "id") (text-at block "name"))
      None))

(defn thinking-start-of [#^ dict event]
  "stream_event の event が考えの block の始まり(content_block_start の thinking)かを読むため(#4186 — 考えの最初の差分より先に届く)。
   ほかの event・ほかの種類の block は偽。"
  (and (= (text-at event "type") "content_block_start") (= (text-at (object-at event "content_block") "type") "thinking")))

(defk call-opened-of [#^ dict event #^ (| str None) parent]
  {:pre [(: event dict) (: parent (| str None))] :post [(: % (| CallOpened None))] :tags {:context "claude-code" :role "foundation"}}
  "stream_event の event が呼びの始まり(message_start)なら、その呼びの id・model・始まりの usage を読むため(呼びの最終の usage を
   終わりの usage と組むため — card ki-290ec664737d)。ほかの event・message.id の無い始まり(どの呼びか分からない)は None。"
  (val message (object-at event "message"))
  (val usage (.get message "usage"))
  (if (and (= (text-at event "type") "message_start") (text-at message "id"))
      (CallOpened :message-id (text-at message "id") :model (or (text-at message "model") None) :parent-tool-use-id parent
                  :usage (if (isinstance usage dict) (usage-of usage) (Usage)))
      None))

(defk call-end-usage-of [#^ dict event]
  {:pre [(: event dict)] :post [(: % (| Usage None))] :tags {:context "claude-code" :role "foundation"}}
  "stream_event の event が呼びの終わり(message_delta)で usage を名乗れば、その usage を読むため(card ki-290ec664737d)。ほかの event・
   usage の無い終わりは None。"
  (val usage (.get event "usage"))
  (if (and (= (text-at event "type") "message_delta") (isinstance usage dict)) (usage-of usage) None))

(defn classify-stream-event [#^ dict record]
  (setv event (object-at record "event"))
  (setv delta (object-at event "delta"))
  (setv kind (run (delta-kind-of (text-at delta "type"))))
  (setv parent (or (text-at record "parent_tool_use_id") None))
  (PartialMessage :text-delta (if (= kind DeltaKind.TEXT) (text-at delta "text") "") :delta kind
                  :thinking-delta (if (= kind DeltaKind.THINKING) (text-at delta "thinking") "")
                  :tool-input-delta (if (= kind DeltaKind.TOOL-INPUT) (text-at delta "partial_json") "")
                  :tool-start (tool-start-of event)
                  :ttft-ms (int-at record "ttft_ms")
                  :thinking-start (thinking-start-of event)
                  :call-opened (run (call-opened-of event parent))
                  :call-end-usage (run (call-end-usage-of event))
                  :parent-tool-use-id parent))

;; --- transcript の額の行(純関数) ---------------------------------------------------------------------

(defk recorded-cost [#^ str transcript-text]
  {:pre [(: transcript-text str)] :post [(: % (| CostMark None))] :tags {:context "claude-code" :role "foundation"}}
  "--resume(--fork-session を含む)で起こした CLI が数え始める額と model ごとの使用量(CostMark)を、handler がその会話の transcript
   (jsonl の本文)から知るため: CLI は降りる時に会話の累積を {\"type\":\"cost-state\", \"totalCostUSD\": 数, \"modelUsage\": {…}} の行で
   記し、続きの process と枝の process は最後のその行から数え続ける(実測 2.1.283・#883・modelUsage は 2.1.292 — card ki-1d986d4f0007)。
   読むのは最後の cost-state の行の totalCostUSD と modelUsage だけ。行が無い・最後の行の額が数でない(bool は数えない)なら None
   (0 を発明しない — CLI の版で形が変わっても誤った額を出さない)。modelUsage の無い行は額だけの起点(models None)。"
  (<- lines (jsonl-lines transcript-text))
  (val found (next (gfor line (reversed lines)
                         :if (in "\"cost-state\"" line)
                         :setv record (parse-record line)
                         :if (and (is-not record None) (= (.get record "type") "cost-state"))
                         record)
                   None))
  (val total (if (is found None) None (number-at found "totalCostUSD")))
  (<- models (model-usage-of (or found {})))
  (if (is total None) None (CostMark total models)))

(defk mark-of-result [#^ TurnResult result]
  {:pre [(: result TurnResult)] :post [(: % (| CostMark None))] :tags {:context "claude-code" :role "foundation"}}
  "手番を閉じた result の行を次の手番の起点(CostMark)にするため — 額と model ごとの累積を同じ行から取る。額を名乗らない行は None。"
  (if (is result.cost-usd None) None (CostMark result.cost-usd result.model-usage)))

;; --- subagent の transcript の呼び(純関数 — card ki-e798888dce4e) ------------------------------------------

(defk instant-at [#^ dict value #^ str key]
  {:pre [(: value dict) (: key str)] :post [(: % (| datetime None))] :tags {:context "claude-code" :role "foundation"}}
  "subagent の呼びの刻を transcript の行から読むため: ISO 8601 の刻の文字列(末尾の Z も)→ 時差つきの datetime。無い・読めない・時差の
   無い刻は None(刻を発明しない)。"
  (val text (text-at value key))
  (when (not text) (return None))
  (try
    (val instant (datetime.fromisoformat text))
    (except [ValueError] (return None)))
  (if (is instant.tzinfo None) None instant))

(defn #^ (| dict None) transcript-record [#^ str line]
  "surrogateescape で解いた transcript の 1 行 → JSON の object(UTF-8 でない byte を含む行・JSON の object でない行は None)。"
  (try
    (.encode line "utf-8")
    (except [UnicodeEncodeError] (return None)))
  (parse-record line))

(defk transcript-records [#^ bytes data]
  {:pre [(: data bytes)] :post [(: % tuple)] :tags {:context "claude-code" :role "foundation"}}
  "読んだ transcript の byte のうち改行で閉じた行(whole-lines-end まで)を、行ごとに JSON の object か None(UTF-8 でない・JSON の
   object でない行)にするため。割るのは jsonl-lines(U+2028 で割らない)。UTF-8 でない byte は行ごとに見分ける — 1 行の壊れで
   ほかの行を読み捨てない。"
  (<- end (whole-lines-end data))
  (<- lines (jsonl-lines (.decode (cut data 0 end) "utf-8" :errors "surrogateescape")))
  (tuple (gfor line lines (transcript-record line))))

(defrecord SubagentCalls
  "subagent の transcript の行から読んだ呼び(subagent-calls-of の答え): calls = 呼びごとに畳んだ消費(書かれた順)/ undated = 時差つきの
   刻を名乗らないので呼びに数えなかった行の数。"
  (#^ (get tuple #(SubagentCall ...)) calls)
  (#^ int undated))

(defk subagent-calls-of [#^ tuple records]
  {:pre [(: records tuple)] :post [(: % SubagentCalls)] :tags {:context "claude-code" :role "foundation"}}
  "subagent の transcript の行(JSON の object の列)から、API の呼びごとの消費を書かれた順に読むため(SubagentCall の註の畳み方)。
   数えるのは message.usage が object の assistant の行だけで、CLI が呼ばずに書いた行(model が SYNTHETIC-MODEL)は数えない。
   時差つきの刻(timestamp)を名乗らない行は呼びに数えず、数だけを答える(刻を発明しない — ほかの行の呼びは捨てない)。"
  (val called (lfor record records
                    :setv message (object-at record "message")
                    :if (and (= (text-at record "type") "assistant") (isinstance (.get message "usage") dict)
                             (!= (text-at message "model") SYNTHETIC-MODEL))
                    #(record message)))
  ;; 呼びの id ごとに 1 つ(id の無い行は行の番号で 1 つずつ)。組の列から作る dict は、鍵の順が最初に出た順で値が最後の物になる —
  ;; usage と model は最後の行・刻は最初の行(逆順から作った dict)の物。
  (var keyed #())
  (var undated 0)
  (for [#(index #(record message)) (enumerate called)]
    (<- at (instant-at record "timestamp"))
    (when (is at None)
      (:= undated (+ undated 1))
      (continue))
    (val call-id (or (text-at message "id") None))
    (:= keyed (+ keyed #(#((if (is call-id None) index call-id)
                           (SubagentCall :at at :usage (usage-of (get message "usage")) :model (or (text-at message "model") None)
                                         :call-id call-id))))))
  (val firsts (dict (reversed keyed)))
  (SubagentCalls :calls (tuple (gfor #(key call) (.items (dict keyed)) (replace call :at (. (get firsts key) at))))
                 :undated undated))

(defk last-call-closed [#^ tuple records]
  {:pre [(: records tuple)] :post [(: % bool)] :tags {:context "claude-code" :role "foundation"}}
  "subagent の transcript を書き切ったかを transcript そのものから知るため: 最後の usage つきの assistant の行(SYNTHETIC-MODEL の行も —
   CLI が API の誤りで閉じた答え)の message.stop_reason が、呼びの続かない語(null でなく CONTINUING-STOP-REASONS の外)か。CLI は
   呼びの行を、その呼びの終わり(message_delta)の後に最後の usage と stop_reason を載せて書く(実測 2.1.292: 本物の CLI と偽の API の
   検・本番の会話の transcript)ので、答え終えた subagent の transcript は閉じた呼びで終わる。そういう行が無ければ偽。"
  (val messages (lfor record records
                      :setv message (object-at record "message")
                      :if (and (= (text-at record "type") "assistant") (isinstance (.get message "usage") dict))
                      message))
  (when (not messages) (return False))
  (val reason (text-at (get messages -1) "stop_reason"))
  (and (bool reason) (not-in reason CONTINUING-STOP-REASONS)))

(defk subagent-transcript-of [#^ str agent-id #^ str parent-tool-use-id #^ bytes data #^ bool answered]
  {:pre [(: agent-id str) (: parent-tool-use-id str) (: data bytes) (: answered bool)] :post [(: % SubagentTranscript)]
   :tags {:context "claude-code" :role "foundation"}}
  "上の層が subagent の呼びごとの消費を記録できるように、subagent の transcript の読んだ byte(前の読みの後に伸びた分)を
   SubagentTranscript にするため。answered = CLI が subagent の答え終えを報せた(報せの行の status が SUBAGENT-ANSWERED-STATUS)— 書き
   切った transcript は閉じた呼び(last-call-closed)で終わる。偽 = 報せの前に process が降りた・答えずに終わった task(書き手が止めた
   所までが全部)。書き切ったかは transcript だけで決め、報せの行の total_tokens とは比べない(実測: 本番で書き切った transcript の
   最後の呼びの和より報せが +251〜+1,801 多い subagent が 13 件中 10 件在り、呼びを全部捨てていた — card ki-3d45f5857239)。
   改行で閉じた行から読めた呼びは、訳が在っても全部載せる(捨てて 0 にしない)。訳(problem・detail)は欠けを名指す: 最後の行が改行で
   終わらない・answered なのに最後の呼びが閉じていない = TRUNCATED(後ろの行がまだ書かれていない)/ UTF-8 でない・JSON の object
   でない行・刻を名乗らない呼びの行が在る = UNREADABLE(その行は数えない)。両方なら TRUNCATED(書き手を待てば読める訳を先にする)。
   空の byte は、answered でなければ呼びの無い読み。"
  (<- records (transcript-records data))
  (<- end (whole-lines-end data))
  (val readable (tuple (gfor record records :if (is-not record None) record)))
  (<- counted (subagent-calls-of readable))
  (<- closed (last-call-closed readable))
  (val tail (cut data end None))
  (val broken (- (len records) (len readable)))
  (val undated counted.undated)
  (val truncations (+ (if (.strip tail) [(.format "the last line of the transcript has no newline ({} bytes)" (len tail))] [])
                      (if (and answered (not closed))
                          ["the transcript does not end with a closed call (no stop_reason that ends the answer)"]
                          [])))
  (val unreadables (+ (if broken [(.format "{} line(s) of the transcript are not UTF-8 JSON objects" broken)] [])
                      (if undated [(.format "{} assistant line(s) with usage have no timestamp with a UTC offset" undated)] [])))
  (SubagentTranscript :agent-id agent-id :parent-tool-use-id parent-tool-use-id :calls counted.calls
                      :problem (cond truncations SubagentTranscriptProblem.TRUNCATED
                                     unreadables SubagentTranscriptProblem.UNREADABLE
                                     True None)
                      :detail (.join "; " (+ truncations unreadables))))

(defn classify-record [#^ dict record]
  "stream-json の 1 行 → ClaudeLineKind(純関数・語彙の外は Other)。"
  (setv kind (text-at record "type"))
  (cond
    (= kind "system") (classify-system record)
    (= kind "assistant") (classify-assistant record)
    (= kind "user") (classify-user record)
    (= kind "stream_event") (classify-stream-event record)
    (= kind "command_lifecycle") (classify-lifecycle record)
    (= kind "control_request") (classify-control-request record)
    (= kind "control_response") (classify-control-response record)
    (= kind "rate_limit_event") (classify-rate-limit record)
    (= kind "result") (classify-result record)
    True (Other :type kind :subtype (text-at record "subtype"))))
