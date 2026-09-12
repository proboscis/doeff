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
;;; wire の綴り(env の名・host の flag・schema・閉語彙)は effects.py だけが持ち、ここは import する。
;;; I/O は 1 つも無い(file の読みは composition root・metadata の読みは handlers.py)。

(require doeff-hy.macros [defk <-])

(import doeff_agents.sessionhost.acp.effects [
  ACP-TOKEN-FILE-ENV
  ACP-URL-ENV
  ACP-VALVE-ENV
  BORROWER-KEY-PATH-ENV
  CUSTODY-URL-ENV
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
  JOIN-SCHEMA
  JOIN-SESSION-HOOKS-DEFAULT
  JOIN-SOCKET-FILE
  JOIN-STATE-DIR-DEFAULT
  JoinArgv
  JoinDeclaration
  JoinPlan
  JoinSpec
  NODE-NAME-ENV
  OWNERSHIP-ENV
  OWNERSHIP-GRADES
  OWNERSHIP-PROOF-DECLARED
  OWNERSHIP-PROOF-ENV
  OWNERSHIP-PROOF-GCE-PREFIX
  Ownership
  OwnershipProbe
  ProbeAnswer
  SESSION-HOOKS-ENV])


;; ---------------------------------------------------------------------------
;; 宣言の綴り(flag の名 = 宣言 file の鍵の名・k3s の config と同じ規律)
;; ---------------------------------------------------------------------------

;; 宣言 file の表の名。
(setv TABLE-AGENTD "agentd")
(setv TABLE-CUSTODY "custody")
;; `[agentd]` の鍵(= flag の名の `-` を `_` にしたもの)。
(setv KEY-SERVER "server")
(setv KEY-TOKEN-FILE "token_file")
(setv KEY-NODE-NAME "node_name")
(setv KEY-STATE-DIR "state_dir")
(setv KEY-BACKEND "backend")
(setv KEY-SESSION-HOOKS "session_hooks")
(setv KEY-OWNERSHIP "ownership")
(setv KEY-OWNERSHIP-PROOF "ownership_proof")
;; `[custody]` の鍵。
(setv KEY-CUSTODY-URL "url")
(setv KEY-BORROWER-KEY-FILE "borrower_key_file")
;; 表 → 許す鍵(宣言に無い鍵は誤りとして名指す — 黙って読み飛ばさない)。
(setv AGENTD-KEYS #{KEY-SERVER KEY-TOKEN-FILE KEY-NODE-NAME KEY-STATE-DIR KEY-BACKEND
                    KEY-SESSION-HOOKS KEY-OWNERSHIP KEY-OWNERSHIP-PROOF})
(setv CUSTODY-KEYS #{KEY-CUSTODY-URL KEY-BORROWER-KEY-FILE})
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
(setv FLAG-CUSTODY "--custody")
(setv FLAG-BORROWER-KEY-FILE "--borrower-key-file")
;; flag → (表 . 鍵)。値を取る flag はこれで全部(それ以外は unknown argument)。
(setv FLAG-KEYS {FLAG-SERVER #(TABLE-AGENTD KEY-SERVER)
                 FLAG-TOKEN-FILE #(TABLE-AGENTD KEY-TOKEN-FILE)
                 FLAG-NODE-NAME #(TABLE-AGENTD KEY-NODE-NAME)
                 FLAG-STATE-DIR #(TABLE-AGENTD KEY-STATE-DIR)
                 FLAG-BACKEND #(TABLE-AGENTD KEY-BACKEND)
                 FLAG-SESSION-HOOKS #(TABLE-AGENTD KEY-SESSION-HOOKS)
                 FLAG-OWNERSHIP #(TABLE-AGENTD KEY-OWNERSHIP)
                 FLAG-OWNERSHIP-PROOF #(TABLE-AGENTD KEY-OWNERSHIP-PROOF)
                 FLAG-CUSTODY #(TABLE-CUSTODY KEY-CUSTODY-URL)
                 FLAG-BORROWER-KEY-FILE #(TABLE-CUSTODY KEY-BORROWER-KEY-FILE)})


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
  (setv values {TABLE-AGENTD {} TABLE-CUSTODY {}})
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
  (for [[table allowed] [[TABLE-AGENTD AGENTD-KEYS] [TABLE-CUSTODY CUSTODY-KEYS]]]
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
  (dfor table [TABLE-AGENTD TABLE-CUSTODY]
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
  (JoinSpec
    :server server
    :token-file token-file
    :node-name (.get agentd KEY-NODE-NAME)
    :state-dir (.get agentd KEY-STATE-DIR (+ state-home "/" JOIN-STATE-DIR-DEFAULT))
    :backend backend
    :session-hooks (.get agentd KEY-SESSION-HOOKS JOIN-SESSION-HOOKS-DEFAULT)
    :custody-url (.get custody KEY-CUSTODY-URL)
    :borrower-key-file (.get custody KEY-BORROWER-KEY-FILE)
    :ownership ownership))


;; ---------------------------------------------------------------------------
;; 宣言 → 起動の形(host の argv と env の束)
;; ---------------------------------------------------------------------------

(defk join-plan-of [spec]
  {:pre [(: spec JoinSpec)]
   :post [(: % JoinPlan)]}
  "JoinSpec → JoinPlan。env の束は今日の serve --acp の起動が読む名ちょうど(弁 on・ACP の URL と札・
   node の名・backend と headless の置き場・hooks・custody・所有)。名乗らない値は env に現れない
   (handler の既定に任せる)。host の argv は db / socket(置き場の下)・上限なし・backend・serve。"
  (setv env [#(ACP-VALVE-ENV "on")
             #(ACP-URL-ENV spec.server)
             #(ACP-TOKEN-FILE-ENV spec.token-file)])
  (when (is-not spec.node-name None)
    (.append env #(NODE-NAME-ENV spec.node-name)))
  (.extend env [#(HOST-BACKEND-ENV spec.backend)
                #(HEADLESS-DIR-ENV (+ spec.state-dir "/" JOIN-HEADLESS-DIR))
                #(SESSION-HOOKS-ENV spec.session-hooks)])
  (when (is-not spec.custody-url None)
    (.append env #(CUSTODY-URL-ENV spec.custody-url)))
  (when (is-not spec.borrower-key-file None)
    (.append env #(BORROWER-KEY-PATH-ENV spec.borrower-key-file)))
  (when (is-not spec.ownership None)
    (.extend env [#(OWNERSHIP-ENV spec.ownership.grade)
                  #(OWNERSHIP-PROOF-ENV spec.ownership.proof)]))
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
