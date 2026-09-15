;;; agentd の 1 命令の参加(`doeff-sessionhost join --server <URL> --token-file <札>`)の判断
;;; (段 6 lane 6f・agora-redesign #26・設計 第 12.6 節 決定 23)。
;;;
;;; 既知の形: agentd = runner(kubelet / CI runner)。参加は k3s の `k3s agent --server --token` と
;;; 同じ 1 命令で、Mac(launchd)・Linux(systemd)・GCP node・runner pod のどれでも同じ体験にする。
;;; 宣言は k3s の config.yaml と同じ規律 — flag と同じ名の鍵を持つ file(toml・schema
;;; doeff.agentd-join.v1)を `--config` で読め、flag が優先する。判断の座は 1 つ:
;;;   * join-spec-of   argv + 宣言 file の表 → JoinSpec(flag > toml > 既定)。
;;;   * join-plan-of   JoinSpec → JoinPlan(host の argv と、今日の serve --acp の起動が読む env の束)。
;;;                    env の束は宣言から導く — 読み手(runtime.settings_from_env / valve /
;;;                    host.hy)は増えない・変わらない。
;;;   * ownership-verdict / ownership-preflight  所有の等級の検: 宣言(grade + proof)と機体の証拠
;;;                    (OwnershipProbe の答え)の突合。不一致は ValueError(runtime が fail-closed に
;;;                    写す — 会社 profile の API 呼び出しは会社所有の機体だけ・CLAUDE.md の境界)。
;;;   * capacity-of    node の capacity の読み(段 10 lane 10d・agora-redesign #85): 宣言 file の [agentd].capacity /
;;;                    flag --capacity(10 進の非負の整数)。無い・読めない agentd は参加しない(ValueError)— agentd は
;;;                    自分の node の行をこの値から名乗る(既知の形 = kubelet の Node の自己登記)。
;;;   * record-sink-of 本文の行き先の検(段 9f lane 9f-6・agora-redesign #59): 会話の記録の service の
;;;                    宛先を持たない agentd は参加を断る(ValueError — runtime が fail-closed に写す)。
;;;                    宣言された状態で断り、推測しない。宛先が在って届かないのは spool が受ける。
;;; wire の綴り(env の名・host の flag・schema・閉語彙)は effects.py だけが持ち、ここは import する。
;;; I/O は 1 つも無い(file の読みは composition root・metadata の読みは handlers.py)。

(require doeff-hy.macros [defk <-])

(import doeff_agents.sessionhost.acp.effects [
  ACP-TOKEN-FILE-ENV
  ACP-URL-ENV
  ACP-VALVE-ENV
  BORROWER-KEY-PATH-ENV
  CUSTODY-SA-TOKEN-PATH-ENV
  DECLARATION-SHA256-ENV
  WORK-ROOTS-ENV
  WORK-ROOTS-MAX
  WORK-ROOTS-SEPARATOR
  WorkRoots
  AGENTD-PLACES
  CAPACITY-ENV
  CUSTODY-URL-ENV
  CustodyHealth
  HEADLESS-DIR-ENV
  HOST-BACKEND-ENV
  HOST-BACKEND-FLAG
  HOST-BACKENDS
  HOST-DB-FLAG
  HOST-MAX-RUNNING-FLAG
  HOST-MAX-RUNNING-UNLIMITED
  HOST-SERVE-COMMAND
  HOST-SOCKET-FLAG
  BACKEND-HEADLESS
  JOIN-DB-FILE
  JOIN-HEADLESS-DIR
  JOIN-RECORD-SPOOL-DIR
  JOIN-SCHEMA
  JOIN-SESSION-HOOKS-DEFAULT
  JOIN-SOCKET-FILE
  JOIN-STATE-DIR-DEFAULT
  JoinArgv
  JoinDeclaration
  JoinPlan
  JoinSpec
  NODE-NAME-ENV
  PLACE-ENV
  OWNERSHIP-ENV
  OWNERSHIP-GRADES
  OWNERSHIP-PROOF-DECLARED
  OWNERSHIP-PROOF-ENV
  OWNERSHIP-PROOF-GCE-PREFIX
  Ownership
  OwnershipProbe
  ProbeAnswer
  RECORD-SPOOL-DIR-ENV
  RECORD-URL-ENV
  SESSION-HOOKS-ENV])


;; ---------------------------------------------------------------------------
;; 宣言の綴り(flag の名 = 宣言 file の鍵の名・k3s の config と同じ規律)
;; ---------------------------------------------------------------------------

;; 宣言 file の表の名。
(setv TABLE-AGENTD "agentd")
(setv TABLE-CUSTODY "custody")
;; 会話の記録の service の宛先(段 9f lane 9f-2・agora-redesign #59)。
(setv TABLE-RECORD "record")
;; `[agentd]` の鍵(= flag の名の `-` を `_` にしたもの)。
(setv KEY-SERVER "server")
(setv KEY-TOKEN-FILE "token_file")
(setv KEY-NODE-NAME "node_name")
(setv KEY-STATE-DIR "state_dir")
(setv KEY-BACKEND "backend")
(setv KEY-SESSION-HOOKS "session_hooks")
(setv KEY-OWNERSHIP "ownership")
(setv KEY-OWNERSHIP-PROOF "ownership_proof")
;; node の spec.capacity(同時に走らせられる手番の数 — 段 10 lane 10d・必須)。
(setv KEY-CAPACITY "capacity")
;; 機体の置き場(company | personal — 段 10 lane 10d 便 2・必須)。node の spec.labels.place に名乗る。
(setv KEY-PLACE "place")
;; node が持つ作業場の根(段 10 lane 10y 案 C・任意)。node の spec.workRoots に名乗る。
(setv KEY-WORK-ROOTS "work_roots")
;; `[custody]` の鍵。
(setv KEY-CUSTODY-URL "url")
(setv KEY-BORROWER-KEY-FILE "borrower_key_file")
;; pod の ServiceAccount の token の file(段 10 lane 10y・agora-redesign #110)— 預かり所へ Bearer で名乗る。
(setv KEY-SERVICE-ACCOUNT-TOKEN-FILE "service_account_token_file")
;; `[record]` の鍵(札は [agentd].token_file の再利用 — 名簿の agentd が service の書き手なので鍵は宛先だけ)。
(setv KEY-RECORD-URL "url")
;; 表 → 許す鍵(宣言に無い鍵は誤りとして名指す — 黙って読み飛ばさない)。
(setv AGENTD-KEYS #{KEY-SERVER KEY-TOKEN-FILE KEY-NODE-NAME KEY-STATE-DIR KEY-BACKEND
                    KEY-SESSION-HOOKS KEY-OWNERSHIP KEY-OWNERSHIP-PROOF KEY-CAPACITY KEY-PLACE KEY-WORK-ROOTS})
(setv CUSTODY-KEYS #{KEY-CUSTODY-URL KEY-BORROWER-KEY-FILE KEY-SERVICE-ACCOUNT-TOKEN-FILE})
(setv RECORD-KEYS #{KEY-RECORD-URL})
;; flag の綴り(`--config` は composition root が先に読む — config-path-of)。
(setv FLAG-CONFIG "--config")
(setv FLAG-SERVER "--server")
(setv FLAG-TOKEN-FILE "--token-file")
(setv FLAG-NODE-NAME "--node-name")
(setv FLAG-STATE-DIR "--state-dir")
(setv FLAG-BACKEND "--backend")
(setv FLAG-SESSION-HOOKS "--session-hooks")
(setv FLAG-OWNERSHIP "--ownership")
(setv FLAG-OWNERSHIP-PROOF "--ownership-proof")
(setv FLAG-CAPACITY "--capacity")
(setv FLAG-PLACE "--place")
(setv FLAG-WORK-ROOTS "--work-roots")
(setv FLAG-CUSTODY "--custody")
(setv FLAG-BORROWER-KEY-FILE "--borrower-key-file")
(setv FLAG-SERVICE-ACCOUNT-TOKEN-FILE "--service-account-token-file")
(setv FLAG-RECORD "--record")
;; flag → (表 . 鍵)。値を取る flag はこれで全部(それ以外は unknown argument)。
(setv FLAG-KEYS {FLAG-SERVER #(TABLE-AGENTD KEY-SERVER)
                 FLAG-TOKEN-FILE #(TABLE-AGENTD KEY-TOKEN-FILE)
                 FLAG-NODE-NAME #(TABLE-AGENTD KEY-NODE-NAME)
                 FLAG-STATE-DIR #(TABLE-AGENTD KEY-STATE-DIR)
                 FLAG-BACKEND #(TABLE-AGENTD KEY-BACKEND)
                 FLAG-SESSION-HOOKS #(TABLE-AGENTD KEY-SESSION-HOOKS)
                 FLAG-OWNERSHIP #(TABLE-AGENTD KEY-OWNERSHIP)
                 FLAG-OWNERSHIP-PROOF #(TABLE-AGENTD KEY-OWNERSHIP-PROOF)
                 FLAG-CAPACITY #(TABLE-AGENTD KEY-CAPACITY)
                 FLAG-PLACE #(TABLE-AGENTD KEY-PLACE)
                 FLAG-WORK-ROOTS #(TABLE-AGENTD KEY-WORK-ROOTS)
                 FLAG-CUSTODY #(TABLE-CUSTODY KEY-CUSTODY-URL)
                 FLAG-BORROWER-KEY-FILE #(TABLE-CUSTODY KEY-BORROWER-KEY-FILE)
                 FLAG-SERVICE-ACCOUNT-TOKEN-FILE #(TABLE-CUSTODY KEY-SERVICE-ACCOUNT-TOKEN-FILE)
                 FLAG-RECORD #(TABLE-RECORD KEY-RECORD-URL)})


;; ---------------------------------------------------------------------------
;; argv と宣言 file の読み(純関数)
;; ---------------------------------------------------------------------------

(defk config-path-of [argv]
  {:pre [(: argv JoinArgv)]
   :post [(: % (| str None))]}
  "join の argv から宣言 file の path(`--config <path>`)。無ければ None。値が無ければ断る。"
  (setv items argv.items)
  (setv found None)
  (setv index 0)
  (while (< index (len items))
    (when (= (get items index) FLAG-CONFIG)
      (when (>= (+ index 1) (len items))
        (raise (ValueError f"{FLAG-CONFIG} requires a value")))
      (setv found (get items (+ index 1))))
    (+= index 1))
  found)


(defk flag-values-of [argv]
  {:pre [(: argv JoinArgv)]
   :post [(: % dict)]}
  "join の argv → {表 {鍵 値}}(flag の値だけ・`--config` は除く)。未知の flag・値の無い flag は断る。"
  (setv items argv.items)
  (setv values {TABLE-AGENTD {} TABLE-CUSTODY {} TABLE-RECORD {}})
  (setv index 0)
  (while (< index (len items))
    (setv arg (get items index))
    (cond
      (= arg FLAG-CONFIG)
      (do (+= index 1)
          (when (>= index (len items))
            (raise (ValueError f"{FLAG-CONFIG} requires a value"))))
      (in arg FLAG-KEYS)
      (do (+= index 1)
          (when (>= index (len items))
            (raise (ValueError f"{arg} requires a value")))
          (setv [table key] (get FLAG-KEYS arg))
          (setv (get (get values table) key) (get items index)))
      True
      (raise (ValueError f"unknown argument: {arg}")))
    (+= index 1))
  values)


(defk declared-values-of [declaration]
  {:pre [(: declaration JoinDeclaration)]
   :post [(: % dict)]}
  "宣言 file の表(toml を読んだ木・tables が空 = file なし)→ {表 {鍵 値}}。schema・表の形・
   鍵の名・値の型(文字列)を検め、外れは名指して断る。"
  (setv tables declaration.tables)
  (when (and tables (!= (.get tables "schema") JOIN-SCHEMA))
    (raise (ValueError (+ "schema が " JOIN-SCHEMA " でない: " (repr (.get tables "schema"))))))
  (setv values {})
  (for [[table allowed] [[TABLE-AGENTD AGENTD-KEYS] [TABLE-CUSTODY CUSTODY-KEYS] [TABLE-RECORD RECORD-KEYS]]]
    (setv found (.get tables table {}))
    (when (not (isinstance found dict))
      (raise (ValueError f"[{table}] は表であること")))
    (for [[key value] (.items found)]
      (when (not-in key allowed)
        (raise (ValueError f"[{table}].{key} は宣言に無い鍵")))
      (when (not (isinstance value str))
        (raise (ValueError f"[{table}].{key} は文字列であること"))))
    (setv (get values table) (dfor [key value] (.items found) (str key) (str value))))
  values)


(defk merged-values-of [flags declared]
  {:pre [(: flags dict) (: declared dict)]
   :post [(: % dict)]}
  "flag > 宣言 file(鍵ごとに flag が勝つ)。"
  (dfor table [TABLE-AGENTD TABLE-CUSTODY TABLE-RECORD]
        table (| (.get declared table {}) (.get flags table {}))))


(defk ownership-of [grade proof]
  {:pre [(: grade (| str None)) (: proof (| str None))]
   :post [(: % (| Ownership None))]}
  "等級と検の方法 → Ownership(どちらも無ければ None)。等級を名乗るなら検の方法も要り(検なしは
   declared と明示)、語彙の外は断る。"
  (cond
    (and (is grade None) (is proof None)) None
    (is grade None)
    (raise (ValueError f"{FLAG-OWNERSHIP-PROOF} は {FLAG-OWNERSHIP} と対で宣言する"))
    (not-in grade OWNERSHIP-GRADES)
    (raise (ValueError (+ f"{FLAG-OWNERSHIP} は " (.join "|" (sorted OWNERSHIP-GRADES))
                          f" のどれか: {grade !r}")))
    (is proof None)
    (raise (ValueError (+ f"{FLAG-OWNERSHIP} {grade} には {FLAG-OWNERSHIP-PROOF} が要る "
                          f"({OWNERSHIP-PROOF-GCE-PREFIX}<project-id> か {OWNERSHIP-PROOF-DECLARED})")))
    (not (or (= proof OWNERSHIP-PROOF-DECLARED)
             (and (.startswith proof OWNERSHIP-PROOF-GCE-PREFIX)
                  (> (len proof) (len OWNERSHIP-PROOF-GCE-PREFIX)))))
    (raise (ValueError (+ f"{FLAG-OWNERSHIP-PROOF} は {OWNERSHIP-PROOF-GCE-PREFIX}<project-id> か "
                          f"{OWNERSHIP-PROOF-DECLARED}: {proof !r}")))
    True (Ownership :grade grade :proof proof)))


(defk capacity-of [text]
  {:pre [(: text (| str None))]
   :post [(: % int)]}
  "node の capacity の読み(段 10 lane 10d・agora-redesign #85): 宣言 file の [agentd].capacity / flag --capacity の
   文字列(10 進の非負の整数)→ int。無い・空・読めない値は ValueError(参加しない — 名乗らない node は配車の
   候補にならない)。値は家(profile の置き場)の数から導かない — 資格は預かり所の貸与なので、同時に走らせる
   手番の数は機体の宣言ちょうど。"
  (setv word (if (is text None) "" (.strip text)))
  (when (not word)
    (raise (ValueError (+ "node の capacity が宣言されていない — 宣言 file の [" TABLE-AGENTD "]." KEY-CAPACITY
                          " か flag " FLAG-CAPACITY " で同時に走らせられる手番の数を名乗る(段 10 lane 10d・"
                          "agora-redesign #85: 名乗らない agentd は参加しない)"))))
  (when (not (and (.isascii word) (.isdigit word)))
    (raise (ValueError f"[{TABLE-AGENTD}].{KEY-CAPACITY} は 0 以上の整数(10 進の数字)であること: {word !r}")))
  (int word))


(defk work-roots-of [text]
  {:pre [(: text (| str None))]
   :post [(: % (| WorkRoots None))]}
  "node が持つ作業場の根の読み(段 10 lane 10y・agora-redesign #110・依頼者の裁定 2026-09-15 案 C): 宣言 file の [agentd].work_roots /
   flag --work-roots / env の , 区切りの 1 つの文字列 → 根の tuple(宣言の順・重複は 1 つ)。無い・空 = None(名乗らない)。各根は
   `~/` か `/` で始まり `/` で終わる形だけ — 相対・`~user`・終わりの `/` の無い根(/Users/s2 が /Users/s22625 に当たる接頭辞の曖昧さ)は
   ValueError(参加しない — 篩う材料を嘘で名乗らない)。"
  (setv word (if (is text None) "" (.strip text)))
  (when (not word)
    (return None))
  (setv roots [])
  (for [part (.split word WORK-ROOTS-SEPARATOR)]
    (setv root (.strip part))
    (when (not (and (or (.startswith root "~/") (.startswith root "/")) (.endswith root "/")))
      (raise (ValueError (+ f"[{TABLE-AGENTD}].{KEY-WORK-ROOTS} の各根は ~/ か / で始まり / で終わる path であること: {root !r}"
                            f"(宣言 {word !r})"))))
    (when (not-in root roots)
      (.append roots root)))
  (when (> (len roots) WORK-ROOTS-MAX)
    (raise (ValueError (+ f"[{TABLE-AGENTD}].{KEY-WORK-ROOTS} の根は {WORK-ROOTS-MAX} 本まで(契約 node.spec.workRoots.maxItems)"
                          f": {(len roots)} 本"))))
  (WorkRoots :roots (tuple roots)))


(defk declaration-sha256-of [text]
  {:pre [(: text (| str None))]
   :post [(: % (| str None))]}
  "読んだ宣言 file の指紋の読み(段 10 lane 10y・agora-redesign #110): sha256 の小文字の hex 64 桁 → そのまま。無い・空 = None
   (宣言 file なしの参加 — node の capacity は書けない)。形の違う値は ValueError(参加しない — 指紋を名乗り損ねた agentd が
   ACP の 400 declaration-fingerprint-invalid で毎拍断られる形を、起動の門で先に止める)。"
  (setv word (if (is text None) "" (.strip text)))
  (when (not word)
    (return None))
  (when (not (and (= (len word) 64) (all (gfor ch word (in ch "0123456789abcdef")))))
    (raise (ValueError f"{DECLARATION-SHA256-ENV} は sha256 の小文字の hex 64 桁であること: {word !r}")))
  word)


(defk place-of [text]
  {:pre [(: text (| str None))]
   :post [(: % str)]}
  "機体の置き場の読み(段 10 lane 10d 便 2・agora-redesign #85): 宣言 file の [agentd].place / flag --place の
   文字列 → 閉語彙 company | personal。無い・空・語彙の外は ValueError(参加しない — 名乗らない agentd は
   配車の候補にならない)。綴りは ACP の契約 agora-kinds.json の profile.spec.boundary と同じ(新しい語を作らない)。
   置き場は機体の所有で切る区画で、会社の資格は company の機体の外へ出ない(不変条件 I1)。"
  (setv word (if (is text None) "" (.strip text)))
  (when (not word)
    (raise (ValueError (+ "機体の置き場が宣言されていない — 宣言 file の [" TABLE-AGENTD "]." KEY-PLACE
                          " か flag " FLAG-PLACE " で " (.join " | " (sorted AGENTD-PLACES))
                          " を名乗る(段 10 lane 10d 便 2・agora-redesign #85: 名乗らない agentd は参加しない)"))))
  (when (not-in word AGENTD-PLACES)
    (raise (ValueError (+ f"[{TABLE-AGENTD}].{KEY-PLACE} は " (.join " | " (sorted AGENTD-PLACES))
                          f" のどれか: {word !r}"))))
  word)


(defk join-spec-of [argv declaration state-home]
  {:pre [(: argv JoinArgv) (: declaration JoinDeclaration) (: state-home str)]
   :post [(: % JoinSpec)]}
  "join の宣言の 1 点: argv(join の後の列)と宣言 file の木(file が無ければ空)と state の根
   (composition root が渡す・既定の置き場の材料)→ JoinSpec。flag > toml > 既定。
   server と token_file は必須。"
  (<- flags dict (flag-values-of argv))
  (<- declared dict (declared-values-of declaration))
  (<- values dict (merged-values-of flags declared))
  (setv agentd (get values TABLE-AGENTD))
  (setv custody (get values TABLE-CUSTODY))
  (setv record (get values TABLE-RECORD))
  (setv server (.get agentd KEY-SERVER))
  (when (not server)
    (raise (ValueError f"{FLAG-SERVER} <ACP の URL> が要る(flag か宣言 file の [{TABLE-AGENTD}].{KEY-SERVER})")))
  (setv token-file (.get agentd KEY-TOKEN-FILE))
  (when (not token-file)
    (raise (ValueError f"{FLAG-TOKEN-FILE} <名簿の agentd の札の file> が要る(flag か宣言 file の [{TABLE-AGENTD}].{KEY-TOKEN-FILE})")))
  (setv backend (.get agentd KEY-BACKEND BACKEND-HEADLESS))
  (when (not-in backend HOST-BACKENDS)
    (raise (ValueError (+ f"{FLAG-BACKEND} は " (.join "|" (sorted HOST-BACKENDS)) f" のどれか: {backend !r}"))))
  ;; 空文字は「名乗らない」(宣言 file で欄を空にして外せる — runtime の env の読みと同じ)。
  (<- ownership (| Ownership None)
      (ownership-of (or (.get agentd KEY-OWNERSHIP) None) (or (.get agentd KEY-OWNERSHIP-PROOF) None)))
  ;; node の capacity(段 10 lane 10d)— 他の宣言の誤りを先に名指してから読む。
  (<- capacity int (capacity-of (.get agentd KEY-CAPACITY)))
  ;; 機体の置き場(段 10 lane 10d 便 2)。
  (<- place str (place-of (.get agentd KEY-PLACE)))
  ;; node が持つ作業場の根(段 10 lane 10y 案 C・任意)。
  (<- declared-roots (| WorkRoots None) (work-roots-of (.get agentd KEY-WORK-ROOTS)))
  (JoinSpec
    :server server
    :token-file token-file
    :node-name (.get agentd KEY-NODE-NAME)
    :state-dir (.get agentd KEY-STATE-DIR (+ state-home "/" JOIN-STATE-DIR-DEFAULT))
    :backend backend
    :session-hooks (.get agentd KEY-SESSION-HOOKS JOIN-SESSION-HOOKS-DEFAULT)
    :custody-url (.get custody KEY-CUSTODY-URL)
    :borrower-key-file (.get custody KEY-BORROWER-KEY-FILE)
    ;; 空文字は「名乗らない」(宣言 file で欄を空にして外せる)。
    :service-account-token-file (or (.get custody KEY-SERVICE-ACCOUNT-TOKEN-FILE) None)
    ;; 読んだ宣言 file の指紋(段 10 lane 10y)— file の bytes から composition root が導いた値をそのまま運ぶ。
    :declaration-sha256 declaration.sha256
    ;; node が持つ作業場の根(段 10 lane 10y 案 C)— 形の検は work-roots-of の 1 点(形違いは参加しない)。
    :work-roots (if (is declared-roots None) None declared-roots.roots)
    :ownership ownership
    :capacity capacity
    :place place
    ;; 空文字は「名乗らない」= env に現れない(参加の門 record-sink-of が読みの 1 点で断る — 段 9f lane 9f-6)。
    :record-url (or (.get record KEY-RECORD-URL) None)))


;; ---------------------------------------------------------------------------
;; 本文の行き先の検(参加の門・段 9f lane 9f-6)
;; ---------------------------------------------------------------------------

(defk record-sink-of [url]
  {:pre [(: url (| str None))]
   :post [(: % str)]}
  "参加の門の 1 点(宣言 → 参加可否): 会話の記録の service の宛先(宣言 file の [record].url / flag
   --record / env RECORD_SERVICE_URL — 綴りは join-plan-of が導く同じ 1 点)が無い agentd は参加を断る
   (ValueError・理由つき — runtime.settings_from_env が AgentdPreflightError に写し、process は exit 2 で
   宿〔launchd / k8s〕に再起動される = 宣言が直るまで参加しない)。本文の行き先を持たない agentd は ACP の
   turn-record に見出しだけを書いて本文をどこにも残さない(設計の穴・lane 9f-4 の残上流 3)ので、宣言された
   状態で断り、推測しない。宛先が在って届かないのは spool が受ける(参加は断らない — 検は record の deftest)。"
  (setv sink (if (is url None) "" (.strip url)))
  (when (not sink)
    (raise (ValueError (+ "会話の記録の service の宛先が無い — 本文の行き先を持たない agentd は参加しない"
                          "(ACP の turn-record に見出しだけを書いて本文を失う)。宣言 file の ["
                          TABLE-RECORD "]." KEY-RECORD-URL " か flag " FLAG-RECORD " か env " RECORD-URL-ENV
                          " で名乗る(段 9f lane 9f-6・agora-redesign #59)"))))
  sink)


;; ---------------------------------------------------------------------------
;; 宣言 → 起動の形(host の argv と env の束)
;; ---------------------------------------------------------------------------

(defk join-plan-of [spec]
  {:pre [(: spec JoinSpec)]
   :post [(: % JoinPlan)]}
  "JoinSpec → JoinPlan。env の束は今日の serve --acp の起動が読む名ちょうど(弁 on・ACP の URL と札・
   node の名・backend と headless の置き場・hooks・custody・所有・会話の記録の service の宛先と spool)。名乗らない値は env に現れない
   (handler の既定に任せる)。host の argv は db / socket(置き場の下)・上限なし・backend・serve。"
  (setv env [#(ACP-VALVE-ENV "on")
             #(ACP-URL-ENV spec.server)
             #(ACP-TOKEN-FILE-ENV spec.token-file)])
  (when (is-not spec.node-name None)
    (.append env #(NODE-NAME-ENV spec.node-name)))
  (.append env #(CAPACITY-ENV (str spec.capacity)))
  (.append env #(PLACE-ENV spec.place))
  (.extend env [#(HOST-BACKEND-ENV spec.backend)
                #(HEADLESS-DIR-ENV (+ spec.state-dir "/" JOIN-HEADLESS-DIR))
                #(SESSION-HOOKS-ENV spec.session-hooks)])
  (when (is-not spec.custody-url None)
    (.append env #(CUSTODY-URL-ENV spec.custody-url)))
  (when (is-not spec.borrower-key-file None)
    (.append env #(BORROWER-KEY-PATH-ENV spec.borrower-key-file)))
  (when (is-not spec.service-account-token-file None)
    (.append env #(CUSTODY-SA-TOKEN-PATH-ENV spec.service-account-token-file)))
  (when (is-not spec.declaration-sha256 None)
    (.append env #(DECLARATION-SHA256-ENV spec.declaration-sha256)))
  (when (is-not spec.work-roots None)
    (.append env #(WORK-ROOTS-ENV (.join WORK-ROOTS-SEPARATOR spec.work-roots))))
  (when (is-not spec.ownership None)
    (.extend env [#(OWNERSHIP-ENV spec.ownership.grade)
                  #(OWNERSHIP-PROOF-ENV spec.ownership.proof)]))
  (when (is-not spec.record-url None)
    (.extend env [#(RECORD-URL-ENV spec.record-url)
                  #(RECORD-SPOOL-DIR-ENV (+ spec.state-dir "/" JOIN-RECORD-SPOOL-DIR))]))
  (JoinPlan
    :host-argv #(HOST-DB-FLAG (+ spec.state-dir "/" JOIN-DB-FILE)
                 HOST-SOCKET-FLAG (+ spec.state-dir "/" JOIN-SOCKET-FILE)
                 HOST-MAX-RUNNING-FLAG HOST-MAX-RUNNING-UNLIMITED
                 HOST-BACKEND-FLAG spec.backend
                 HOST-SERVE-COMMAND)
    :env (tuple env)))


;; ---------------------------------------------------------------------------
;; 所有の等級の検(宣言 × 機体の証拠)
;; ---------------------------------------------------------------------------

(defk custody-contract-refusal [answer spoken]
  {:pre [(: answer (| dict None)) (: spoken int)]
   :post [(: % (| str None))]}
  "預かり所の /health の答えから「この走行係はこの預かり所と話せるか」を判じる 1 点
   (段 10 lane 10d 便 4・agora-redesign #85・依頼者の裁定 2026-09-15)。
   None = 話せる。文字列 = 参加しない理由(そのまま起動の断りの文になる)。

   ⚠ 版の**定義点は預かり所**(/health の欄 contract)で、走行係は読むだけ。
   答えが無い(届かない)・欄が無い(版 1 の預かり所)・数が違う、のどれも参加しない:
   起点の実弾 2026-09-15 01:48〜02:37 = 版 2 を話す agentd が版 1 の預かり所より**先に**本番へ出て、
   貸与の答えを malformed grant と読み、手番が 49 分間 1 つも走らなかった。順は
   **預かり所(server)が先・走行係(client)が後**で、それを機械で守る材料がこの名乗り。"
  (setv order "順は預かり所(server)が先・走行係(client)が後 — 預かり所を先に上げてから機体を入れ替える")
  (cond
    (is answer None)
    (+ "預かり所の /health が読めない(届かないか 200 でない)— 版を確かめられない機体は参加しない。" order)
    (not (isinstance (.get answer "contract") int))
    (+ f"預かり所が契約の版を名乗らない(/health に contract の欄が無い = 版 {spoken} より前の預かり所)。" order)
    (!= (get answer "contract") spoken)
    (do
      (setv named (get answer "contract"))
      (+ f"預かり所の契約の版 {named} とこの走行係が話せる版 {spoken} が違う。" order))
    True
    None))


(defk ownership-verdict [ownership answer]
  {:pre [(: ownership Ownership) (: answer ProbeAnswer)]
   :post [(: % Ownership)]}
  "宣言と証拠の突合の 1 点。declared = 宣言をそのまま(検なし)。gce-project:<id> = metadata の
   project-id が id と一致する時だけ通す(読めない・違う = ValueError — 参加しない)。"
  (cond
    (= ownership.proof OWNERSHIP-PROOF-DECLARED) ownership
    (.startswith ownership.proof OWNERSHIP-PROOF-GCE-PREFIX)
    (do
      (setv expected (cut ownership.proof (len OWNERSHIP-PROOF-GCE-PREFIX) None))
      (cond
        (is answer.value None)
        (raise (ValueError (+ f"ownership {ownership.grade} claims {ownership.proof} but the GCE metadata "
                              "server did not answer a project-id (not a GCE VM, or metadata unreachable)")))
        (!= answer.value expected)
        (raise (ValueError (+ f"ownership {ownership.grade} claims {ownership.proof} but the GCE metadata "
                              f"project-id is {answer.value !r} (expected {expected !r})")))
        True ownership))
    True
    (raise (ValueError f"ownership proof {ownership.proof !r} is not a known method"))))


(defk custody-contract-preflight [spoken]
  {:pre [(: spoken int)]
   :post [(: % (| str None))]}
  "起動の前に 1 回撃つ検(段 10 lane 10d 便 4・agora-redesign #85): 預かり所が /health で名乗る
   契約の版を読み、この走行係が話せるかを判じる。None = 参加してよい・文字列 = 参加しない理由。
   判断は上の custody-contract-refusal の 1 点で、ここは読みを運ぶだけ(参加の拍の判定は
   ownership-verdict と同じくこの module に住む — 段 10 lane 10d 便 4 の初版は judgment に
   置いていたが、judgment の持ち分は『自分に結ばれた job か・欄の写し・transcript の畳み』で
   参加の可否ではなかった)。"
  (<- answer (| dict None) (CustodyHealth))
  (<- refusal (| str None) (custody-contract-refusal answer spoken))
  refusal)


(defk ownership-preflight [ownership]
  {:pre [(: ownership Ownership)]
   :post [(: % Ownership)]}
  "起動の前に 1 回撃つ検: 検の方法が証拠を要るなら OwnershipProbe を撃ち(declared は撃たない)、
   ownership-verdict で突合する。"
  (if (= ownership.proof OWNERSHIP-PROOF-DECLARED)
      (<- verdict Ownership (ownership-verdict ownership (ProbeAnswer :value None)))
      (do
        (<- answer ProbeAnswer (OwnershipProbe :proof ownership.proof))
        (<- verdict Ownership (ownership-verdict ownership answer))))
  verdict)
