;;; agora-redesign #1909 のテストの宣言 — Jev の規則 DOEFF201・202・205 の線引き 5 つと、線引きごとの鳴る例・鳴らない例 1 つずつ(計 10)。
;;; 線引きの文 = agora-controllers の architecture.hy の註の節「Jev の規則 DOEFF201 / 202 / 205 の線引き」(main 8bad3b59f・agora-redesign #1908)の
;;; 原文のまま。決められなかった行で採った 2 点は、1 点目を線引き 5・2 点目を線引き 4 の文の後ろに足した。例 = 文書
;;; analysis-jev-judgments-47-by-lines-2026-10-01.html(sha256 4c93c656…)の「47 件の実物の例」の定義を同じ main から取った抜粋(:tags・docstring・
;;; 註を落とし、略した所は `;; …`)。層の説明は同じ main の core と protocol のコピー。
;;; 線引き 4 の 2 例は `{:code … :why …}` の組(agora-redesign #1995 — why が問いに code と組で載ること・:code と :why の並びを問わないこと)、
;;; 残りの 8 例は code の文字列だけの形(前の形のまま読めること)。
(defarchitecture lines-sample
  :root "app"
  :layers [(layer core :summary "業務の判断と Program" :knows "業務の判断(いつ・誰に・何を)"
                         :does-not-know "相手が誰か、どう通信するか" :question "通信の方法が変わってもこのコードは変わらないか?" :roles [type judgment program])
           (layer protocol :summary "intent を相手の話し方へ訳す handler" :knows "相手の話し方(要求を汎用の effect へ言い換える)"
                         :does-not-know "本物か模擬か" :question "要求を別の汎用の effect に言い換えているだけか?" :roles [protocol])]
  :semantic-lines [(line "線引き 1" :rules [DOEFF201]
                     :text "protocol の定義がしてよいのは、外から来た値の形の確認(型に読む・欄の有無と形式を確かめる)、読み書き、探した物が無い時のエラー値、判断の関数の答えを値に直すことだけとする。業務の状態(記録の値・期限・持ち主・状態の移り方・もう済んだか)を見て、返す答え(渡す・断る・書く・どの結果か)を決めるのは業務の判断で、core の純粋な関数に置く。architecture.hy に書いてある 9/30 の決定(翻訳の handler は読み書きだけを持ち、判断は core の純粋な関数を呼ぶ)と同じ線。custody には core が無いので、採ると core を作る仕事になる。9/29 に誤判定とした同じ形の行(record-end-precondition)も違反に変わる。"
                     :fires [#[code[(defk access-of-held [ledger lease-id]
  {:pre [(: ledger CustodyHttpLedger) (: lease-id str)] :post [(: % (| TurnCredential TurnCredentialUnavailable))]}
  (<- at datetime (GetTime))
  (setv held (.get ledger.held lease-id) now (! (epoch-ms-of-time at)))
  (cond
    (is held None) (TurnCredentialUnavailable :reason (+ "lease " lease-id " は借りていないか、もう返した"))
    (<= (min held.access-expires-at held.expires-at) now) (TurnCredentialUnavailable :reason (+ "lease " lease-id " の token は期限を過ぎた"))
    True (TurnCredential held.access-token)))]code]]
                     :silent [#[code[(defk send-task [placement job assignment workers]
  {:pre [(: placement TaskPlacement) (: job TaskJob) (: assignment TaskAssignment) (: workers tuple)]
   :post [(: % (| TaskSent TaskRefused TaskUnreachable))]}
  (val worker (next (gfor w workers :if (= w.name assignment.node) w) None))
  (var worker-open False)
  (when (is-not worker None)
    (<- ok bool (open-worker? worker))
    (:= worker-open ok))
  (cond
    (and (is-not worker None) worker-open) (do (<- sent (| TaskSent TaskUnreachable) (submit-task placement job assignment))
                                                sent)
    (or (is worker None) (not worker.live)) (TaskRefused :reason (+ "機体が居ない: " assignment.node))
    True (TaskRefused :reason (+ "機体が drain 中: " assignment.node))))]code]])
                   (line "線引き 2" :rules [DOEFF202]
                     :text "core は JSON の綴りを知らない。JSON の文字列を作る・読む(json.dumps・json.loads・dump-json)のは protocol か入口の部品で、core は型の値だけを受け取り、返す。本文の JSON から計算する値(ハッシュ・byte 数)と、手元の契約の文書の読み込みも同じ。9/29 の判定で classifier の core の JSON の正規化(RFC 8785)を違反としたのと同じ線。元の判定と違うのは、手元の契約の文書を読む行 33 だけ。"
                     :fires [#[code[(defk tag-families-of [core-text]
  {:pre [(: core-text str)]
   :post [(: % TagFamilies)]}
  (setv document (json.loads core-text))
  (when (not (isinstance document dict))
    (raise (ValueError "kanban-core.json が object でない")))
  (setv section (.get (.get document CORE-PARTICIPATION {}) CORE-TAG-FAMILIES))
  (when (not (isinstance section dict))
    (raise (ValueError f"kanban-core.json に {CORE-PARTICIPATION}.{CORE-TAG-FAMILIES} が無い")))
  ;; …
  (TagFamilies :families (tuple families)
               :table rows
               :source (TagFamiliesSource :contract TAG-FAMILIES-SOURCE-FILE
                                          :sha256 (.hexdigest (hashlib.sha256 (.encode core-text "utf-8"))))))]code]]
                     :silent [#[code[(defk beat-status [row owner now ttl-ms]
  {:pre [(: row (| RunRecord Malformed None)) (: owner str) (: now int) (: ttl-ms int)] :post [(: % (| RunStatus None))]}
  (when (not (isinstance row RunRecord))
    (return None))
  (val lease row.status.lease)
  (when (or (is lease None) (!= lease.owner owner))
    (return None))
  (<- next RunStatus (renewed-status row.status owner now ttl-ms))
  next)]code]])
                   (line "線引き 3" :rules [DOEFF202]
                     :text "core は HTTP を知らない。URL・path・query を組む・持つこと、HTTP の method・status・ヘッダーを選ぶ・持つこと、外の相手の URL の形(GitHub の commit の URL など)を知ることは protocol でする。HTTP の中継や他の service の HTTP の契約の確認のように、通信そのものを仕事にする service でも同じで、core は「どこへ」「準備できたか」「どの項目が合わないか」を型の値で持つ。元の判定と違うのは、他の service の HTTP の契約を確かめる型(行 2・迷いだった)と、GitHub の commit のリンクを組む行 32。"
                     :fires [#[code[(defk relay-url-of [row target]
  {:pre [(: row RelayRow) (: target str)] :post [(: % str)]}
  (val base (.rstrip row.upstream "/"))
  (val rest (cut target (len (.rstrip row.prefix "/")) None))
  (if (or (= rest "") (.startswith rest "?"))
      (+ base "/" rest)
      (+ base rest)))]code]]
                     :silent [#[code[(defk admit-writer [raw-key]
  {:pre [(: raw-key (| str None))] :post [(: % (| WriterAdmitted WriterRefused BorrowerBookUnreadable))]}
  (<- key (| str None) (presented-key raw-key))
  (when (is key None)
    (return (WriterRefused :refusal WriterRefusal.NO-KEY)))
  (<- book (| BorrowerBook ObservationUnavailable) (ReadBorrowerBook))
  (when (isinstance book ObservationUnavailable)
    (return (BorrowerBookUnreadable :reason book.reason)))
  (<- verdict (| WriterAdmitted WriterRefused) (writer-verdict key book))
  verdict)]code]])
                   (line "線引き 4" :rules [DOEFF205]
                     :text "入力の形の確認とは、型の無い値(dict・JSON の値・生の文字列)から欄を読む・isinstance で型を確かめる・欄の有無・空・長さ・形式・語彙を確かめることを言う。型に読んだ後の欄でも、空・長さ・形式・語彙の確認は形の確認に数える。型の union の枝分けは形の確認に数えない。形の確認は入口(protocol か、型へ読む 1 つの関数)で済ませ、判断の定義には持ち込まない。9/30 に manager が決めた「型に読んだ後の欄の制限は形の確認に数えない」を逆にする(厳しい側)。これで行 35・37・44 が誤判定から違反に変わる。code の中の定数や自分の集計の控えのような、外から来ていない型の無い dict は、線引き 4 の「入力」に数えない。"
                     :fires [{:why "型に読んだ要求の id の形を正規表現で確かめる形の確認と、担当を決める判断が同じ定義に在る。"
                              :code #[code[(defk classify-message [request]
  {:pre [(: request ClassifyRequest)] :post [(: % (| Accepted Rejected Unavailable))]}
  (when (not (MESSAGE-ID-RE.match request.message))
    (return (Rejected :reason "payload-invalid" :detail "id is not lt- + 26 Crockford characters")))
  (<- policy (| DeliveryPolicy None Unavailable) (ReadDeliveryPolicy))
  (when (isinstance policy Unavailable)
    (return policy))
  (<- message (| Message MessageAbsent Unavailable) (ReadMessageRecord request.message))
  ;; …
  (val answered (replace message :request-class request.request-class))
  (<- rule (| ReceptionRule None) (machine-rule-for policy answered None))
  (when (is rule None)
    (return (Rejected :reason REFUSE-CLASS-NOT-IN-RULES :detail (+ "class " request.request-class " has no open rule"))))
  ;; …
  (<- forwarded Answer (forward-message (ForwardRequest :by MESSAGING-PRINCIPAL :message request.message :to cid)))
  (if (isinstance forwarded Accepted)
      (Accepted :subject cid :changed forwarded.changed)
      forwarded))]code]}]
                     :silent [{:code #[code[(defk join-chat [request]
  {:pre [(: request JoinRequest)] :post [(: % (| Joined Rejected Unavailable))]
   :effects [ReadDeliveryPolicy GetTime ReadChatRoomRecord ReadChatParticipants WriteChatParticipant]}
  (<- problem (| str None) (join-problem request))
  (when problem
    (return (Rejected :reason "payload-invalid" :detail problem)))
  (<- allowed (| bool Unavailable) (may-manage? request.by))
  (match allowed
    (Unavailable) (return allowed)
    False (return (Rejected :reason "opener-not-allowed" :detail (+ "principal " request.by " may not add participants"))))
  (<- now int (now-ms))
  (<- joined (| Joined Rejected Unavailable)
      (join-record (ParticipantRecord :chat request.chat :agent request.agent :origin request.origin :joined-at now :kind request.kind
                                      :defaults request.defaults)))
  joined)]code]
                               :why "形の確認は join-problem に任せ、その答えで断りの値を返すだけ。形の確認が無いので混ざらない。"}])
                   (line "線引き 5" :rules [DOEFF205 DOEFF201]
                     :text "業務の判断とは、業務の決まりで答えを決めること(誰が何をしてよいか・どの結果にするか・何を書くか)を言う。読めなかった値をどの値として扱うか(既定の値に倒す・飛ばす)と、画面や集計のための組み立て(投影・集計)も業務の判断に数える。「読めない値を既定に倒すのは判断」は規則 201 にも効く(protocol が読めない値を既定に倒せば違反)。読めない値を「無い(None)」で表して判断の関数へ渡す事は、線引き 5 の「読めなかった値をどの値として扱うか」に含めない。"
                     :fires [#[code[(defk progress-items [material args limit offset now]
  {:pre [(: material ProgressMaterial) (: args dict) (: limit int) (: offset int) (: now int)]
   :post [(: % (| EntityItems EntityRefused))]}
  (setv board material.board
        namespace (.get args ENTITY-ARG-NAMESPACE))
  (when (not (isinstance namespace str))
    (return (EntityRefused :reason (+ KANBAN-ENTITY-PROGRESS " には args." ENTITY-ARG-NAMESPACE "(card の名前空間)が要る"))))
  (<- since (| int None EntityRefused) (since-of args))
  (when (isinstance since EntityRefused)
    (return since))
  (<- members dict (milestone-members (list (.values board.relations))))
  (for [[key milestone] (.items board.milestones)]
    (setv own (.get members key []))
    (when (not (any (gfor card own
                          :if (in card board.cards)
                          (= (. (get board.cards card) namespace) namespace))))
      (continue))
    ;; …
    (when (and (not open?) (or (is since None) (< last-at since)))
      (continue))
    ;; …
    )
  (setv ordered (sorted items :key (fn [item] #((get item "order") (get item "id")))))
  (<- page EntityItems (page-of (lfor item ordered (OpaqueJson.of item)) offset limit))
  ;; …
  (EntityItems :items page.items :total page.total :landed (OpaqueJson.of landed-json)))]code]]
                     :silent [#[code[(defk list-limit-of [text]
  {:pre [(: text (| str None))] :post [(: % (| int RequestRefused))]}
  (val limit (cond
               (is text None) LIST-DEFAULT-LIMIT
               (.fullmatch DECIMAL text) (int text)
               True None))
  (if (and (is-not limit None) (<= 1 limit LIST-MAX-LIMIT))
      limit
      (RequestRefused :refusal RequestRefusal.LIMIT-OUT-OF-RANGE :detail (str LIST-MAX-LIMIT))))]code]])])
(defservice lines {:layers [core protocol]})
