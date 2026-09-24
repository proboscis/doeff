(require doeff-hy.macros [defk <-])

;;; 手番に届いた 1 通の「出どころ」の判定と、agent に見せる形・返し方の 1 点(設計 herdr-hud
;;; docs/design-checks/direct-chat-2026-09-24/design.md §3・段 1)。
;;;
;;; operator の要件(2026-09-24 逐語): "using yuubin system internally for impl is okay but from the agent the difference must be
;;; clear, to reply with ai tell or just reply" / "iraisho answer are messages"。
;;;
;;; 根(設計 §2 の実測): operator の chat が他の会話からの郵便と同じ見出し `[郵便 …・from=operator…]` で agent に見え、前置きは
;;; 「返事は `ai tell --to <見出しの from>`」と書き、`ai tell` は `--to operator` を断る。指示どおりにすると断られ、指示に背く
;;; chat の出力のほうが operator に届く — 同じ中身を郵便と chat の 2 回で返した会話が本番に在る。
;;;
;;; 不変条件: 届いた 1 通ごとに「誰から来たか」と「どう返すか」がここの判定 1 つで決まり、prompt の形(区切りと項の見出し)は
;;; その結果だけを使う。⚠ ここの外で `from == "operator"` を比べて返し方を決めない。
;;;
;;; 判定の規則(input-source-of)— **差出人だけで決めない**(盲検の反例 1: 受付経由で operator が出した依頼は from=operator の
;;; ask で、包みなしにすると担い手が郵便 id を失い `ai reply <id> --kind report` の完了報告が止まる):
;;;   1. spec.notice が在る(ACP の Messaging が機械として作った報せ)か差出人が system → Notice(返事不要)
;;;   2. kind = ask → Request(差出人が operator でも。完了は ai reply <id> --kind report)
;;;   3. kind = accept / send-back → Verdict(差出人 = 依頼者)。kind = withdraw かつ差出人 operator → Verdict(取り下げ)
;;;   4. 差出人 operator かつ kind = answer → OperatorAnswer(問い = inReplyTo・依頼書 = refs)
;;;   5. 差出人 operator(上のどれでもない — note)→ OperatorChat。⚠ 設計 §3 は「class も inReplyTo も無い note だけ」と書くが、
;;;      inReplyTo 付きの operator の note(画面の担い手の付け替えの報せ・「送り直す」)を Mail に落とすと、Mail の返し方
;;;      「ai tell --to operator」が断られる元の欠陥をそのまま再現する。operator の発言として見せ、参照は項の見出しに添える。
;;;   6. それ以外 → Mail(他の会話からの郵便)

(import datetime [datetime timedelta timezone])
(import doeff_agents.sessionhost.acp.input_source [OperatorChat OperatorAnswer Verdict Request Mail Notice
                                                   TurnInputText SECTION-ORDER])

;; 契約 agora-kinds.json message.spec.from / kind の語(判定の材料 — 綴りはここ 1 点)。
(setv FROM-OPERATOR "operator")
(setv FROM-SYSTEM "system")
(setv KIND-ASK "ask")
(setv KIND-ANSWER "answer")
(setv KIND-ACCEPT "accept")
(setv KIND-SEND-BACK "send-back")
(setv KIND-WITHDRAW "withdraw")
;; 契約 agora-kinds.json message.spec.notice(ACP の Messaging が機械として作った報せに理由の語を書く欄 — 設計 §3「報せの判定の材料」)。
(setv NOTICE-FIELD "notice")
(setv NONE-WORD "無し")

;; 区切りの見出し(区切りの閉語彙 input_source.Section ごとに 1 行)。返し方の説明はこの表ちょうどで、前置き(agora-controllers
;; controllers/messaging/carrier-charter.md)は同じ語を指して「各区切りの見出しに従う」と書く。見出しは毎手番の prompt に載るので、
;; 前置きの無い温かい手番(send)でも返し方が見える。
(setv SECTION-HEADINGS
  {"operator" (+ "【operator の発言】この会話の利用者(operator)が画面から送った文です。この手番の chat の出力でそのまま答えて"
                 "ください。`ai tell` / `ai reply` では返しません(`ai tell --to operator` は断られ、chat の出力はそのまま operator の"
                 "画面に届きます)。ただし項の見出しに別の返し方が書いてある項(検収の差し戻し)はその指示に従ってください。")
   "request" (+ "【依頼】途中の経過は chat の出力で構いません。仕上げたら、各見出しの `ai reply <依頼の id> --kind report` で"
                "完了を報告してください(依頼者が operator の時は `--author-model <この会話のモデル>` も付けます)。")
   "mail" "【他の会話からの郵便】返事が要る時は、各見出しに書いた `ai tell` で出してください。chat の出力は差出人に届きません。"
   "notice" "【機械からの報せ】返事は要りません。報せを受け取ったことだけを理由に調査や報告を始めず、今の作業を続けてください。"})

;; 前置き(charter の prompt)の区切り。「これまでの会話」は写しを組む側(judgment.history-of)が自分の見出しで始める。
(setv PREAMBLE-HEADING "【前置き】")


(defk text-field-of [spec key]
  {:pre [(: spec dict) (: key str)]
   :post [(: % (| str None))]}
  "spec の文字列の欄(空白だけ・文字列でない = None — 発明しない)。"
  (setv value (.get spec key))
  (if (and (isinstance value str) (.strip value)) value None))


(defk refs-field-of [spec]
  {:pre [(: spec dict)]
   :post [(: % tuple)]}
  "spec.refs の文字列の項(順を保つ)。"
  (setv refs (.get spec "refs"))
  (if (isinstance refs list)
      (tuple (lfor ref refs :if (and (isinstance ref str) (.strip ref)) ref))
      #()))


(defk at-field-of [spec]
  {:pre [(: spec dict)]
   :post [(: % (| int None))]}
  "spec.at(epoch ms・真偽値は数にしない)。"
  (setv at (.get spec "at"))
  (if (and (isinstance at int) (not (isinstance at bool))) at None))


(defk at-text-of [at]
  {:pre [(: at (| int None))]
   :post [(: % str)]}
  "見出しの時刻(契約の時計 epoch ms → JST の秒)。無ければ「無し」。"
  (if (is at None)
      NONE-WORD
      (.strftime (datetime.fromtimestamp (/ at 1000) :tz (timezone (timedelta :hours 9) "JST")) "%Y-%m-%d %H:%M:%S JST")))


(defk mail-served-class-of [status]
  {:pre [(: status (| dict None))]
   :post [(: % (| str None))]}
  "郵便の行の status → **扱う class**(`status.routing.servedClass` の逐語・無ければ None)。

   card acp:kanban-issue:ki-fa719b70d37c(設計 agora-redesign docs/design/kanban-class-route/README.md §D2c-4):
   配送表は 1 つの class を**別の class として配る**行(投函の身元の名簿 `postedBy` + 扱う class の宣言 `as`)を
   持てる。読み替えが起きた拍だけ ACP の純関数 `Acp.App.Messaging.Decide.servedClassOf` が 1 回この欄へ書く。
   ⚠ **素通しちょうどで、第 2 の導出を置かない** — 方策の受付の表をここで読み直して同じ答えを組み直すと、
     表が動いた日に 2 つの答えが割れる。⚠ 欄が無い = 読み替えが起きていない(「無い」を名乗りで埋めない)。"
  (when (not (isinstance status dict))
    (return None))
  (setv routing (.get status "routing"))
  (when (not (isinstance routing dict))
    (return None))
  (setv value (.get routing "servedClass"))
  (if (and (isinstance value str) (.strip value)) value None))


(defk shown-class-of [spec status]
  {:pre [(: spec dict) (: status (| dict None))]
   :post [(: % (| str None))]}
  "見出しに出す class: 名乗り(spec.class)。扱う class(status.routing.servedClass)が名乗りと違う拍は `扱う(名乗り <名乗り か 無し>)`
   の形で両方を出す(card ki-fa719b70d37c — 担い手は自分がどの class の担当として開かれたかをこの 1 語で読む)。どちらも無ければ None。"
  (<- declared (| str None) (text-field-of spec "class"))
  (<- served (| str None) (mail-served-class-of status))
  (if (and (is-not served None) (!= served declared))
      (+ served "(名乗り " (if (is declared None) NONE-WORD declared) ")")
      declared))


(defk input-source-of [message-id spec [status None]]
  {:pre [(: message-id str) (: spec dict) (: status (| dict None))]
   :post [(: % (| OperatorChat OperatorAnswer Verdict Request Mail Notice))]}
  "届いた郵便の行 1 つ → 出どころ(判断はここ 1 点・規則は file の頭の 1〜6)。純関数。"
  (<- sender (| str None) (text-field-of spec "from"))
  (<- kind (| str None) (text-field-of spec "kind"))
  (<- in-reply-to (| str None) (text-field-of spec "inReplyTo"))
  (<- notice (| str None) (text-field-of spec NOTICE-FIELD))
  (<- at (| int None) (at-field-of spec))
  (<- refs tuple (refs-field-of spec))
  (setv from-operator (= sender FROM-OPERATOR))
  (cond
    (is-not notice None)
    (Notice :message-id message-id :at at :sender sender :reason notice)
    (= sender FROM-SYSTEM)
    (Notice :message-id message-id :at at :sender sender :reason FROM-SYSTEM)
    (= kind KIND-ASK)
    (do
      (<- shown (| str None) (shown-class-of spec status))
      (<- parent (| str None) (text-field-of spec "parent"))
      (Request :message-id message-id :at at :requester (if (is sender None) NONE-WORD sender)
               :served-class shown :parent parent))
    (or (in kind #{KIND-ACCEPT KIND-SEND-BACK}) (and from-operator (= kind KIND-WITHDRAW)))
    (Verdict :message-id message-id :at at :verdict kind :target in-reply-to
             :requester (if (is sender None) NONE-WORD sender) :from-operator from-operator)
    (and from-operator (= kind KIND-ANSWER))
    (OperatorAnswer :message-id message-id :at at :question in-reply-to :refs refs)
    from-operator
    (OperatorChat :message-id message-id :at at :in-reply-to in-reply-to :refs refs)
    True
    (Mail :message-id message-id :at at :sender sender :kind kind :in-reply-to in-reply-to)))


(defk section-of [source]
  {:pre [(: source (| OperatorChat OperatorAnswer Verdict Request Mail Notice))]
   :post [(: % str)]}
  "出どころ → 置く区切り(input_source.Section)。検収は依頼者で分ける(operator の検収は operator の発言・会話の検収は郵便)。"
  (cond
    (isinstance source #(OperatorChat OperatorAnswer)) "operator"
    (isinstance source Verdict) (if source.from-operator "operator" "mail")
    (isinstance source Request) "request"
    (isinstance source Mail) "mail"
    (isinstance source Notice) "notice"
    True (raise (TypeError f"出どころの型が網羅されていない: {(type source)}"))))


(defk verdict-line-of [source]
  {:pre [(: source Verdict)]
   :post [(: % str)]}
  "検収・差し戻し・取り下げの項の見出し(何への返事か + 返し方)。"
  (setv target (if (is source.target None) NONE-WORD source.target))
  (<- at-text str (at-text-of source.at))
  (setv stamp (+ source.message-id "・at=" at-text))
  (cond
    (= source.verdict KIND-ACCEPT)
    (+ "(報告 " target " の検収: 受け入れ〔accept〕・" stamp ")— 返事は要りません。")
    (= source.verdict KIND-SEND-BACK)
    (+ "(報告 " target " の差し戻し〔send-back〕・" stamp ")— 本文の指摘を直してから、元の依頼へ"
       " `ai reply <元の依頼の郵便 id> --kind report` で報告し直してください。")
    True
    (+ "(依頼 " target " の取り下げ・" stamp ")— その依頼の作業を止めてください。返事は要りません。")))


(defk item-heading-of [source]
  {:pre [(: source (| OperatorChat OperatorAnswer Verdict Request Mail Notice))]
   :post [(: % str)]}
  "出どころ → 項の見出し 1 行(本文の前に付ける)。綴りはここ 1 点。
   - operator の文: `(<id>・at=<JST>)` と、添えた参照(`・郵便 <id> について`・`・参照 <refs>`)。返し方は区切りの見出しが言う。
   - 依頼書への答え: `(依頼書 <refs> の問い <問いの id> への答え・<id>・at=…)`。
   - 依頼: `[依頼 <id>・class=…・依頼者=…・parent=…・at=…・完了は ai reply <id> --kind report]`。
   - 他の会話からの郵便: `[郵便 <id>・kind=…・from=…・inReplyTo=…・at=…・返事は ai tell --to <from> --in-reply-to <id> --kind note]`。
   - 報せ: `[報せ <id>・理由=…・from=…・at=…・返事は要りません]`。"
  (<- at-text str (at-text-of source.at))
  (cond
    (isinstance source OperatorChat)
    (+ "(" source.message-id "・at=" at-text
       (if (is source.in-reply-to None) "" (+ "・郵便 " source.in-reply-to " について"))
       (if source.refs (+ "・参照 " (.join " " source.refs)) "")
       ")")
    (isinstance source OperatorAnswer)
    (+ "(依頼書 " (if source.refs (.join " " source.refs) NONE-WORD)
       " の問い " (if (is source.question None) NONE-WORD source.question) " への答え・"
       source.message-id "・at=" at-text ")")
    (isinstance source Verdict)
    (do
      (<- line str (verdict-line-of source))
      (if source.from-operator
          line
          (+ "[郵便 " source.message-id "・依頼者 " source.requester " から] " line)))
    (isinstance source Request)
    (+ "[依頼 " source.message-id
       "・class=" (if (is source.served-class None) NONE-WORD source.served-class)
       "・依頼者=" source.requester
       "・parent=" (if (is source.parent None) NONE-WORD source.parent)
       "・at=" at-text
       "・完了は ai reply " source.message-id " --kind report]")
    (isinstance source Mail)
    (do
      (setv sender (if (is source.sender None) NONE-WORD source.sender))
      (+ "[郵便 " source.message-id
         "・kind=" (if (is source.kind None) NONE-WORD source.kind)
         "・from=" sender
         "・inReplyTo=" (if (is source.in-reply-to None) NONE-WORD source.in-reply-to)
         "・at=" at-text
         "・返事は ai tell --to " sender " --in-reply-to " source.message-id " --kind note]"))
    (isinstance source Notice)
    (+ "[報せ " source.message-id
       "・理由=" source.reason
       "・from=" (if (is source.sender None) NONE-WORD source.sender)
       "・at=" at-text
       "・返事は要りません]")
    True (raise (TypeError f"出どころの型が網羅されていない: {(type source)}"))))


(defk turn-input-text-of [message-id spec body [status None]]
  {:pre [(: message-id str) (: spec dict) (: body str) (: status (| dict None))]
   :post [(: % TurnInputText)]}
  "郵便の行 1 つと本文 → 手番へ渡す 1 項(区切り + 項の見出し 1 行 + 本文)。1 手番目に畳む腕・温かい session への send・割り込みの
   注入の 3 つの路が同じ項を運ぶ。"
  (<- source (| OperatorChat OperatorAnswer Verdict Request Mail Notice) (input-source-of message-id spec status))
  (<- section str (section-of source))
  (<- heading str (item-heading-of source))
  (TurnInputText :section section :text (+ heading "\n" body)))


(defk inputs-text-of [items]
  {:pre [(: items tuple)]
   :post [(: % str)]}
  "手番へ渡す項の列 → 区切りつきの 1 本の文。区切りは SECTION-ORDER の順(operator の発言を先・郵便を後・報せを最後)、各区切りの
   見出しは 1 回だけ、区切りの中は届いた順。項は空行で区切る。項が無ければ空文字。
   ⚠ 項の並びを入れ替えても、どれが先に来たかは各項の at で読める(設計 §3)。"
  (setv blocks [])
  (for [section SECTION-ORDER]
    (setv texts (lfor item items :if (= item.section section) item.text))
    (when texts
      (.append blocks (.join "\n\n" (+ [(get SECTION-HEADINGS section)] texts)))))
  (setv known (set SECTION-ORDER))
  (for [item items]
    (when (not-in item.section known)
      (raise (ValueError f"区切りの閉語彙の外: {item.section}"))))
  (.join "\n\n" blocks))


(defk preamble-text-of [charter-prompt]
  {:pre [(: charter-prompt str)]
   :post [(: % str)]}
  "前置き(charter の prompt)を区切りの見出しで囲んだ文。空白だけなら空文字(区切りも出さない)。"
  (if (.strip charter-prompt)
      (+ PREAMBLE-HEADING "\n" charter-prompt)
      ""))
