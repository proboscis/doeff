(require doeff-hy.macros [defk <-])

;;; 送信待ちの列(kind conversation-input)の、agentd 側の判定の 1 点(設計 herdr-hud docs/design-checks/direct-chat-2026-09-24/
;;; design.md 段 2〜3・実装仕様の決定 E1〜E8・card acp:kanban-issue:ki-0bb4104cd8c2)。
;;;
;;; 形: operator が画面から送った文は、入力の行(kind conversation-input・id `ci-…`)と、それを運ぶ郵便(運搬郵便 — refs に ci id を
;;; 1 つ持つ)の 2 つになる。ACP の Messaging は郵便を今どおり運び、入力の行の意味を 1 bit も知らない(E1)。取り消し・編集・
;;; 「今すぐ送る」との競合は、agentd が本文を読む拍に入力の行へ CAS で書く「取った(taken)」が 1 つに決める(E4・E5)。
;;;
;;; ここは純関数だけ(I/O は agentd.hy の take-carried-input / mark-inputs-read)。判定の規則:
;;;   - 郵便が ci id を名指さない・行が無い・行の形が読めない → NoInputRow(本文をそのまま運ぶ — 旧い経路と競合の窓・E8)
;;;   - pending、または taken / read で carrier.mail がこの郵便 → CarryInput(最新の版の本文)
;;;   - withdrawn / answered / failed、または taken / read で carrier.mail が別の郵便 → SkipInput(運ばない・扱い済みとして報告)

(import re)
(import doeff_agents.sessionhost.acp.effects [AcpRow])
(import doeff_agents.sessionhost.acp.input_source [NoInputRow CarryInput SkipInput])

;; 契約 agora-kinds.json kinds.conversation-input の綴り(ここ 1 点)。
(setv CONVERSATION-INPUT-KIND "conversation-input")
(setv INPUT-ID-PATTERN (re.compile r"^ci-[0-9A-HJKMNP-TV-Z]{26}$"))
(setv INPUT-STATE-PENDING "pending")
(setv INPUT-STATE-TAKEN "taken")
(setv INPUT-STATE-READ "read")
(setv INPUT-STATES-SKIPPED #{"withdrawn" "answered" "failed"})
(setv SKIP-CARRIED-BY-ANOTHER-MAIL "carried-by-another-mail")
;; status.readEvidence の閉語彙(E6)。
(setv READ-EVIDENCE-HANDED "handed-to-turn")
(setv READ-EVIDENCE-INTERJECTED "interjected")
(setv READ-EVIDENCES #{READ-EVIDENCE-HANDED READ-EVIDENCE-INTERJECTED})


(defn _whole-number? [value]
  (and (isinstance value int) (not (isinstance value bool))))


(defk carried-input-id-of [spec]
  {:pre [(: spec dict)]
   :post [(: % (| str None))]}
  "郵便の spec.refs が名指す入力の行の id(`ci-` + Crockford base32 26 字)。ちょうど 1 つの時だけ返す — 0 個は旧い経路、
   2 個以上は形の違反で、どちらも None(本文をそのまま運ぶ)。"
  (setv refs (.get spec "refs"))
  (when (not (isinstance refs list))
    (return None))
  (setv ids (lfor ref refs :if (and (isinstance ref str) (.match INPUT-ID-PATTERN ref)) ref))
  (if (= (len (set ids)) 1) (get ids 0) None))


(defk input-ref? [ref]
  {:pre [(: ref str)]
   :post [(: % bool)]}
  "refs の 1 項が入力の行の id か(見出しの「参照」に出さない項 — reply_channel.item-heading-of が読む)。"
  (is-not (.match INPUT-ID-PATTERN ref) None))


(defk input-key-of [namespace input-id]
  {:pre [(: namespace str) (: input-id str)]
   :post [(: % str)]}
  "入力の行の鍵(identityKey = id・区画は運搬郵便と同じ)。"
  f"{namespace}:{CONVERSATION-INPUT-KIND}:{input-id}")


(defk latest-revision-of [spec]
  {:pre [(: spec dict)]
   :post [(: % (| tuple None))]}
  "spec.revisions の最新の版 #(rev text)(rev の最も大きい項)。読める版が 1 つも無ければ None。"
  (setv revisions (.get spec "revisions"))
  (when (not (isinstance revisions list))
    (return None))
  (setv best None)
  (for [item revisions]
    (when (and (isinstance item dict) (_whole-number? (.get item "rev")) (isinstance (.get item "text") str))
      (when (or (is best None) (> (get item "rev") (get best 0)))
        (setv best #((get item "rev") (get item "text"))))))
  best)


(defk carrier-mail-of [status]
  {:pre [(: status dict)]
   :post [(: % (| str None))]}
  "status.carrier.mail(この入力を運んでいる郵便の id)。"
  (setv carrier (.get status "carrier"))
  (setv mail (if (isinstance carrier dict) (.get carrier "mail") None))
  (if (isinstance mail str) mail None))


(defk input-carry-verdict-of [input-row mail-id]
  {:pre [(: input-row (| AcpRow None)) (: mail-id str)]
   :post [(: % (| NoInputRow CarryInput SkipInput))]}
  "入力の行(無ければ None)と運搬郵便の id → 運ぶか(判定はここ 1 点・規則は file の頭)。純関数。"
  (when (is input-row None)
    (return (NoInputRow)))
  (setv status (if (isinstance input-row.status dict) input-row.status {}))
  (setv state (.get status "state"))
  (when (in state INPUT-STATES-SKIPPED)
    (return (SkipInput :reason state)))
  (<- latest (| tuple None) (latest-revision-of input-row.spec))
  (when (is latest None)
    (return (NoInputRow)))
  (setv [rev text] latest)
  (cond
    (= state INPUT-STATE-PENDING)
    (CarryInput :text text :rev rev)
    (in state #{INPUT-STATE-TAKEN INPUT-STATE-READ})
    (do
      (<- carrier (| str None) (carrier-mail-of status))
      (if (= carrier mail-id)
          (CarryInput :text text :rev rev)
          (SkipInput :reason SKIP-CARRIED-BY-ANOTHER-MAIL)))
    True
    (NoInputRow)))


(defk taken-status-of [status job-id mail-id rev now-ms]
  {:pre [(: status dict) (: job-id str) (: mail-id str) (: rev int) (: now-ms int)]
   :post [(: % (| dict None))]}
  "取った印の status(state taken・carrier {job, mail}・rev・takenAt)。他の欄は写す。書かなくてよい拍は None:
   - 既にこの job とこの郵便が取っている(借りのやり直しが start-claimed を撃ち直した拍 — 冪等)
   - 既に read(渡した印を taken へ戻さない)
   同じ郵便を別の job が取っていた拍(前の job が借りで終わり Messaging が同じ郵便を運び直した)は carrier.job を書き換え、
   takenAt は最初に取った刻のまま。"
  (setv state (.get status "state"))
  (<- carrier-mail (| str None) (carrier-mail-of status))
  (when (= state INPUT-STATE-READ)
    (return None))
  (setv carrier (.get status "carrier"))
  (setv carrier-job (if (isinstance carrier dict) (.get carrier "job") None))
  (setv same-mail (and (= state INPUT-STATE-TAKEN) (= carrier-mail mail-id)))
  (when (and same-mail (= carrier-job job-id) (= (.get status "rev") rev))
    (return None))
  (setv next (dict status))
  (setv (get next "state") INPUT-STATE-TAKEN)
  (setv (get next "carrier") {"job" job-id "mail" mail-id})
  (setv (get next "rev") rev)
  (setv (get next "takenAt") (if (and same-mail (_whole-number? (.get status "takenAt"))) (get status "takenAt") now-ms))
  next)


(defk read-status-of [status mail-id evidence now-ms]
  {:pre [(: status dict) (: mail-id str) (: evidence str) (: now-ms int)
         (in evidence READ-EVIDENCES)]
   :post [(: % (| dict None))]}
  "読まれた印の status(state read・readAt・readEvidence)。他の欄(carrier・rev・takenAt)は写す。この郵便が取った行
   (state taken・carrier.mail がこの郵便)だけに書く — それ以外(既に read・別の郵便が取った・取り消し)は None。"
  (<- carrier-mail (| str None) (carrier-mail-of status))
  (when (or (!= (.get status "state") INPUT-STATE-TAKEN) (!= carrier-mail mail-id))
    (return None))
  (setv next (dict status))
  (setv (get next "state") INPUT-STATE-READ)
  (setv (get next "readAt") now-ms)
  (setv (get next "readEvidence") evidence)
  next)
