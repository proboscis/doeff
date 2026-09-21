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
;;;                    証拠は機体の外か機体の耐久の物ちょうど(gce-project = GCE の metadata /
;;;                    file:<path>=<値> = 据え付けの側が描いた file の中身)— 所有の判定の台帳は
;;;                    doeff に無い(card ki-d6cc49cbf33f 決定 D4)。検めを撃つ引き金は『所有を
;;;                    名乗ったか』ではなく『特権の置き場(PRIVILEGED-PLACES)を名乗ったか』で
;;;                    (同 決定 D4 ③)、places に company が在る宣言は両欄が空でも declared でも
;;;                    断る — 他機体の宣言 file を写した agentd を止める錠。
;;;   * capacity-of    node の capacity の読み(段 10 lane 10d・agora-redesign #85): 宣言 file の [agentd].capacity /
;;;                    flag --capacity(10 進の非負の整数)。無い・読めない agentd は参加しない(ValueError)— agentd は
;;;                    自分の node の行をこの値から名乗る(既知の形 = kubelet の Node の自己登記)。
;;;   * record-sink-of 本文の行き先の検(段 9f lane 9f-6・agora-redesign #59): 会話の記録の service の
;;;                    宛先を持たない agentd は参加を断る(ValueError — runtime が fail-closed に写す)。
;;;                    宣言された状態で断り、推測しない。宛先が在って届かないのは spool が受ける。
;;;   * claude-settings-file-of / claude-settings-declaration-of  席の settings file(card acp:kanban-issue:
;;;                    ki-7b52bb76aa6e・ADR-DOE-AGENTS-004 R13): 宣言 [agentd].claude_settings_file の綴りの読みと、
;;;                    参加の門 (d)(b)(c)(session_hooks = inherit / JSON の object / doeff の鍵を含まない)。
;;;                    file の読みは composition root(runtime.join_plan)で、**読めない = 断らない**(不在は参加して
;;;                    名乗る — 依頼書 §10-2 の訂正)。断るのは宣言そのものの誤りだけ。
;;; wire の綴り(env の名・host の flag・schema・閉語彙)は effects.py だけが持ち、ここは import する。
;;; I/O は 1 つも無い(file の読みは composition root・metadata の読みは handlers.py)。

(require doeff-hy.macros [defk <-])

(import doeff_agents.sessionhost.policy [CARRIED-INSTRUCTION-SOURCES seat-env-credential-shaped-offenders session-env-admission-error])
;; 席の settings の鍵の家(card acp:kanban-issue:ki-7b52bb76aa6e): doeff が `--settings` に置く鍵の集合は argv の合流点
;; (impls/claude_code.hy)が 1 点で持ち、参加の門 (c) はそれを読む — 綴りを写さない。
(import doeff_agents.sessionhost.impls.claude_code [CLAUDE-SETTINGS-OWNED-KEYS])

(import doeff_agents.sessionhost.acp.effects [
  ACP-TOKEN-FILE-ENV
  ACP-URL-ENV
  ACP-VALVE-ENV
  BORROWER-KEY-PATH-ENV
  CUSTODY-SA-TOKEN-PATH-ENV
  CONVERSATION-ID-ENV
  SEAT-OPENER-ENV
  SEAT-ENV-ENV
  SEAT-ENV-SEPARATOR
  CLAUDE-SETTINGS-FILE-ENV
  DECLARATION-SHA256-ENV
  WORK-ROOTS-ENV
  WORK-ROOTS-MAX
  WORK-ROOTS-SEPARATOR
  WorkRoots
  WORK-DIRS-ENV
  WORK-DIRS-MAX
  WORK-DIRS-SEPARATOR
  WorkDirs
  WORK-DIR-ROOTS-ENV
  WORK-DIR-ROOTS-MAX
  WORK-DIR-ROOTS-SEPARATOR
  WorkDirRoots
  CUSTODY-BORROWER-SA-PREFIX
  CUSTODY-BORROWER-KEY-PREFIX
  CUSTODY-BORROWER-KEY-HEX-CHARS
  CUSTODY-SA-NAMESPACE-CLAIM
  CUSTODY-SA-NAME-CLAIM
  Places
  SeatEnv
  AGENTD-PLACES
  CAPACITY-ENV
  DRAIN-SECONDS-ENV
  AGENTD-REVISION-ENV
  AGENTD-BUILD-ENV
  CUSTODY-URL-ENV
  CustodyHealth
  HEADLESS-DIR-ENV
  HOST-BACKEND-ENV
  HOST-BACKEND-FLAG
  HOST-BACKENDS
  HOST-DB-FLAG
  HOST-ALLOW-METERED-BILLING-FLAG
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
  PLACES-ENV
  PLACES-SEPARATOR
  OWNERSHIP-ENV
  OWNERSHIP-GRADE-COMPANY
  OWNERSHIP-GRADES
  OWNERSHIP-PROOF-DECLARED
  OWNERSHIP-PROOF-ENV
  OWNERSHIP-PROOF-FILE-PREFIX
  OWNERSHIP-PROOF-GCE-PREFIX
  ownership-proof-file-parts
  Ownership
  OwnershipProbe
  PRIVILEGED-PLACES
  ProbeAnswer
  RECORD-SPOOL-DIR-ENV
  RECORD-URL-ENV
  SESSION-HOOKS-ENV])

;; card acp:kanban-issue:ki-40021864e62f: 借り手の等価鍵の導出に使う純粋な計算ちょうど
;; (札の digest・JWT の claims の読み)。I/O は 1 つも無い(file の読みは composition root)。
(import base64)
(import hashlib)
(import json)


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
;; 機体が仕える置き場の集合(company / personal の , 区切り — 段 11 lane 11u・agora-redesign #224・必須で空でない)。
;; node の spec.places に名乗る。1 値の鍵 place(段 10 lane 10d 便 2)は退役 — 宣言に残っていれば「宣言に無い鍵」として断る。
(setv KEY-PLACES "places")
;; node が持つ作業場の根(段 10 lane 10y 案 C・任意)。node の spec.workRoots に名乗る。
(setv KEY-WORK-ROOTS "work_roots")
;; 停止(SIGTERM)の排水の上限(秒・任意・段 12 lane 12j・agora-redesign #304 便 2)。0 / 無し = 排水しない(今日どおり)。
(setv KEY-DRAIN-SECONDS "drain_seconds")
;; 段 12 lane 12j(agora-redesign #367): agentd の版の刻印(任意)— 据え付けの側が書く(git sha と image の tag か local)。
(setv KEY-REVISION "revision")
(setv KEY-BUILD "build")
;; 席へ運ぶ env の宣言(agora-redesign #520・任意)— 改行区切りの `NAME=value` の行を 1 つの文字列に詰める
;; (places / work_roots と同じ作法。`declared-values-of` の不変条件「宣言 file の値は全部文字列」を割らないため、
;; 表〔dict〕にはしない)。この鍵を知らない agentd は「宣言に無い鍵」で参加を断る(fail-closed — 旧い機体は
;; 宣言された宛先を黙って落とさない)。
(setv KEY-SEAT-ENV "seat_env")
;; 席の settings file(card acp:kanban-issue:ki-7b52bb76aa6e・ADR-DOE-AGENTS-004 R13・任意): 機体の参加の宣言が名指す
;; 「無人席へ hook を届ける settings」(dotfiles claude-hooks/seat-settings.json = proxy 登録 1 枚)の path(絶対 か `~/…`・
;; `~` は agentd の HOME で composition root が展開)。seat_env と同じ kubelet 型: 宣言 → JoinSpec → env → 起動の拍ごとに読む。
;; この鍵を知らない agentd は「宣言に無い鍵」で参加を断る(fail-closed — 旧い機体は名指された file を黙って落とさない)。
(setv KEY-CLAUDE-SETTINGS-FILE "claude_settings_file")
;; 従量課金の binding kind を受けるか(従量課金の便 lane A・任意・既定 false)。
;; 閉語彙 "true" | "false" の文字列 — 宣言 file の値は全部文字列(declared-values-of の
;; 1 つの不変条件)なので、bool を 1 つだけ足して読み手に 2 つ目の型の分岐を作らない。
(setv KEY-ALLOW-METERED-BILLING "allow_metered_billing")
;; `[custody]` の鍵。
(setv KEY-CUSTODY-URL "url")
(setv KEY-BORROWER-KEY-FILE "borrower_key_file")
;; pod の ServiceAccount の token の file(段 10 lane 10y・agora-redesign #110)— 預かり所へ Bearer で名乗る。
(setv KEY-SERVICE-ACCOUNT-TOKEN-FILE "service_account_token_file")
;; `[record]` の鍵(札は [agentd].token_file の再利用 — 名簿の agentd が service の書き手なので鍵は宛先だけ)。
(setv KEY-RECORD-URL "url")
;; 表 → 許す鍵(宣言に無い鍵は誤りとして名指す — 黙って読み飛ばさない)。
;; card acp:kanban-issue:ki-62aa1f4e9c9c(決定 D11): 席の家へ運ぶ共通の指示の鍵は**名簿から導く** —
;; 手で書き足さない。名簿に 1 行足した日に、この集合と env と据え付けが**同時に**追随する
;; (書き足す形は、足し忘れた鍵を「宣言に無い鍵」で断る = その機体が参加できない形で失敗する)。
(setv AGENTD-KEYS (| #{KEY-SERVER KEY-TOKEN-FILE KEY-NODE-NAME KEY-STATE-DIR KEY-BACKEND
                       KEY-SESSION-HOOKS KEY-OWNERSHIP KEY-OWNERSHIP-PROOF KEY-CAPACITY KEY-PLACES KEY-WORK-ROOTS KEY-DRAIN-SECONDS
                       KEY-ALLOW-METERED-BILLING KEY-REVISION KEY-BUILD KEY-SEAT-ENV
                       KEY-CLAUDE-SETTINGS-FILE}
                     (sfor source CARRIED-INSTRUCTION-SOURCES source.key)))
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
(setv FLAG-PLACES "--places")
(setv FLAG-WORK-ROOTS "--work-roots")
(setv FLAG-ALLOW-METERED-BILLING "--allow-metered-billing")
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
                 FLAG-PLACES #(TABLE-AGENTD KEY-PLACES)
                 FLAG-WORK-ROOTS #(TABLE-AGENTD KEY-WORK-ROOTS)
                 FLAG-ALLOW-METERED-BILLING #(TABLE-AGENTD KEY-ALLOW-METERED-BILLING)
                 FLAG-CUSTODY #(TABLE-CUSTODY KEY-CUSTODY-URL)
                 FLAG-BORROWER-KEY-FILE #(TABLE-CUSTODY KEY-BORROWER-KEY-FILE)
                 FLAG-SERVICE-ACCOUNT-TOKEN-FILE #(TABLE-CUSTODY KEY-SERVICE-ACCOUNT-TOKEN-FILE)
                 FLAG-RECORD #(TABLE-RECORD KEY-RECORD-URL)})

;; join の flag の一覧 = `doeff-sessionhost join --help` の usage の生成元(並びがそのまま
;; help の並び)。#(flag 値の見出し 説明)— 綴りは上の FLAG-* の 1 点を読み、手で写した
;; 一覧をどこにも作らない。表と受け付ける flag(FLAG-KEYS + FLAG-CONFIG)の集合が一致する
;; ことは deftest test-join-flag-specs-cover-the-accepted-flags が守る。
(setv JOIN-FLAG-SPECS
  [#(FLAG-CONFIG "<path>"
     (+ "Declaration file (TOML, schema " JOIN-SCHEMA "). A flag overrides the "
        "file, the file overrides the defaults. Keys carry the flag's name "
        "without the leading dashes, under [agentd] / [custody] / [record]."))
   #(FLAG-SERVER "<URL>"
     (+ "Control-plane engine this node joins. Required (flag or ["
        TABLE-AGENTD "]." KEY-SERVER ")."))
   #(FLAG-TOKEN-FILE "<path>"
     (+ "File holding this node's cluster token. Required (flag or ["
        TABLE-AGENTD "]." KEY-TOKEN-FILE ")."))
   #(FLAG-CAPACITY "<n>"
     "How many concurrent turns this node accepts. Required, a non-negative integer.")
   #(FLAG-PLACES (+ "<" (.join PLACES-SEPARATOR (sorted AGENTD-PLACES)) ">")
     (+ "Which places this machine serves — a comma-separated, non-empty subset of "
        (.join PLACES-SEPARATOR (sorted AGENTD-PLACES)) ". Required: the placement binds a "
        "profile only to a node whose set holds its boundary (company credentials never "
        "leave a company machine), so an unnamed node is not a dispatch candidate."))
   #(FLAG-RECORD "<URL>"
     (+ "Conversation-record service that stores turn bodies. Required — a node "
        "without a sink would leave headline-only turns in the control plane."))
   #(FLAG-NODE-NAME "<name>"
     "Name this node registers under. Defaults to the machine's host name.")
   #(FLAG-STATE-DIR "<path>"
     (+ "State directory holding " JOIN-DB-FILE ", " JOIN-SOCKET-FILE ", "
        JOIN-HEADLESS-DIR "/ and " JOIN-RECORD-SPOOL-DIR "/. Default: "
        "$XDG_STATE_HOME/" JOIN-STATE-DIR-DEFAULT "."))
   #(FLAG-BACKEND (+ "<" (.join "|" (sorted HOST-BACKENDS)) ">")
     (+ "Substrate the host carries sessions on. Default: " BACKEND-HEADLESS "."))
   #(FLAG-SESSION-HOOKS "<disabled|inherit>"
     (+ "Whether agent sessions inherit the config directory owner's hooks. "
        "Default: " JOIN-SESSION-HOOKS-DEFAULT "."))
   #(FLAG-OWNERSHIP (+ "<" (.join "|" (sorted OWNERSHIP-GRADES)) ">")
     (+ "Declared ownership grade of this machine. Optional, but it must come "
        "with " FLAG-OWNERSHIP-PROOF " and the proof is checked before joining."))
   #(FLAG-OWNERSHIP-PROOF
     (+ "<" OWNERSHIP-PROOF-GCE-PREFIX "<project-id>|" OWNERSHIP-PROOF-DECLARED ">")
     (+ "How the ownership grade is verified: the GCE metadata project must match, "
        "or " OWNERSHIP-PROOF-DECLARED " for a declaration this host does not verify."))
   #(FLAG-WORK-ROOTS "<root,...>"
     (+ "Work roots this node offers, up to " (str WORK-ROOTS-MAX) ", separated by "
        "`" WORK-ROOTS-SEPARATOR "`. Each root starts with `/` or `~/` and ends "
        "with `/`. Dispatch filters absolute work directories through them."))
   #(FLAG-ALLOW-METERED-BILLING "<true|false>"
     (+ "Whether this host admits metered binding kinds. Default: false. Unlike "
        "the host's own flag this one takes a value, because a declaration file "
        "carries strings."))
   #(FLAG-CUSTODY "<URL>"
     "Credential custody service that lends agent homes. Optional.")
   #(FLAG-BORROWER-KEY-FILE "<path>"
     "File holding this node's borrower key for the custody service.")
   #(FLAG-SERVICE-ACCOUNT-TOKEN-FILE "<path>"
     (+ "File holding the pod's ServiceAccount token, presented to the custody "
        "service as a bearer token."))])


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
   declared と明示)、語彙の外は断る。検の方法は 3 つ: gce-project:<project-id>(GCE の metadata)/
   file:<path>=<値>(据え付けの側が描いた機体の耐久の file の中身)/ declared(検なし)。"
  (cond
    (and (is grade None) (is proof None)) None
    (is grade None)
    (raise (ValueError f"{FLAG-OWNERSHIP-PROOF} は {FLAG-OWNERSHIP} と対で宣言する"))
    (not-in grade OWNERSHIP-GRADES)
    (raise (ValueError (+ f"{FLAG-OWNERSHIP} は " (.join "|" (sorted OWNERSHIP-GRADES))
                          f" のどれか: {grade !r}")))
    (is proof None)
    (raise (ValueError (+ f"{FLAG-OWNERSHIP} {grade} には {FLAG-OWNERSHIP-PROOF} が要る "
                          f"({OWNERSHIP-PROOF-GCE-PREFIX}<project-id> か "
                          f"{OWNERSHIP-PROOF-FILE-PREFIX}<path>=<value> か "
                          f"{OWNERSHIP-PROOF-DECLARED})")))
    (not (or (= proof OWNERSHIP-PROOF-DECLARED)
             (and (.startswith proof OWNERSHIP-PROOF-GCE-PREFIX)
                  (> (len proof) (len OWNERSHIP-PROOF-GCE-PREFIX)))
             (is-not (ownership-proof-file-parts proof) None)))
    (raise (ValueError (+ f"{FLAG-OWNERSHIP-PROOF} は {OWNERSHIP-PROOF-GCE-PREFIX}<project-id> か "
                          f"{OWNERSHIP-PROOF-FILE-PREFIX}<path>=<value> か "
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


(defk work-dirs-of [text]
  {:pre [(: text (| str None))]
   :post [(: % (| WorkDirs None))]}
  "node が持つ作業場の読み(段 12 lane 12j・agora-redesign #575 便 2): env DOEFF_AGENTD_WORK_DIRS の , 区切りの 1 つの文字列 → 家からの
   相対の作業場の tuple(綴りの順・重複は 1 つ)。None(env 無し)= 導いていない(欄を書かない — 配車は篩わない)。空文字 = 「何も
   持たない」の宣言(空の tuple — checkout の無い pod)。各作業場は `~` か `~/…` の形ちょうど(契約 node.spec.workDirs)— 形の外・
   上限超えは ValueError(参加しない — 篩う材料を嘘で名乗らない)。"
  (when (is text None)
    (return None))
  (setv dirs [])
  (for [part (.split (.strip text) WORK-DIRS-SEPARATOR)]
    (setv held (.strip part))
    (when (not held)
      (continue))
    (when (not (or (= held "~") (.startswith held "~/")))
      (raise (ValueError f"{WORK-DIRS-ENV} の各作業場は ~ か ~/… の形(家からの相対)であること: {held !r}")))
    (when (not-in held dirs)
      (.append dirs held)))
  (when (> (len dirs) WORK-DIRS-MAX)
    (raise (ValueError f"{WORK-DIRS-ENV} の作業場は {WORK-DIRS-MAX} までであること(契約 node.spec.workDirs.maxItems): {(len dirs)}")))
  (WorkDirs :dirs (tuple dirs)))


(defk held-work-dirs-of [entries]
  {:pre [(: entries tuple)]
   :post [(: % WorkDirs)]}
  "家の一覧 → 持つ作業場(段 12 lane 12j・agora-redesign #575 便 2)。entries = composition root(runtime.join_plan)が読んだ
   #(親 名 .git の有無) の列(親 = \"\" が ~ の直下・\"repos\" が ~/repos の直下 — effects.WORK-DIRS-SCAN-PARENTS)。.git を持つ
   dir だけを `~/<名>` / `~/repos/<名>` で名乗り、隠し dir(.worktrees 等)は数えない。判断はここ 1 点で I/O は無い。"
  (setv found [])
  (for [[parent name has-git] entries]
    (when (or (not has-git) (.startswith name "."))
      (continue))
    (setv held (if parent (+ "~/" parent "/" name) (+ "~/" name)))
    (when (not-in held found)
      (.append found held)))
  (WorkDirs :dirs (tuple (sorted found))))


(defk work-dir-root-shaped [root]
  {:pre [(: root str)]
   :post [(: % bool)]}
  "根の形の検の**1 点**(段 12 lane 12j 追補・card acp:kanban-issue:ki-3bfe48a9d5dc): `~/<名>/` ちょうど —— 家からの相対で、
   `/` で終わり(境界の曖昧さを綴りで塞ぐ: 根 `~/.worktrees/` は `~/.worktreesX/y` を持たない)、**家そのもの `~/` は根に
   しない**(『家を持つ = 家の下の何でも持つ』は checkout を持たない機体へ手番を飛ばす — この軸が避けている誤配)。
   読む口(env)と導く口(実勢)の両方がこの 1 点を読む(第 2 の判定点を作らない)。"
  (and (.startswith root "~/") (.endswith root "/") (!= root "~/")))


(defk work-dir-roots-of [text]
  {:pre [(: text (| str None))]
   :post [(: % (| WorkDirRoots None))]}
  "node が持つ作業場の**根**の読み(段 12 lane 12j 追補・card acp:kanban-issue:ki-3bfe48a9d5dc): env DOEFF_AGENTD_WORK_DIR_ROOTS の
   , 区切りの 1 つの文字列 → 根の tuple(綴りの順・重複は 1 つ)。None(env 無し)= 導いていない(欄を書かない — 配車は今日どおり
   名簿だけで篩う)。空文字 = 根が 1 つも無い(空の tuple)。各根は `~/<名>/` の形ちょうど(契約 node.spec.workDirRoots — `/` で
   終わる〔境界の曖昧さを塞ぐ〕・**家そのもの `~/` は根にしない**〔家を持つ = 家の下の何でも持つ、は checkout の無い機体へ
   手番を飛ばす〕)。形の外・上限超えは ValueError(参加しない — 篩う材料を嘘で名乗らない)。"
  (when (is text None)
    (return None))
  (setv roots [])
  (for [part (.split (.strip text) WORK-DIR-ROOTS-SEPARATOR)]
    (setv root (.strip part))
    (when (not root)
      (continue))
    (<- shaped bool (work-dir-root-shaped root))
    (when (not shaped)
      (raise (ValueError f"{WORK-DIR-ROOTS-ENV} の各根は ~/<名>/ の形(家からの相対・`/` で終わる・家そのものは根にしない)であること: {root !r}")))
    (when (not-in root roots)
      (.append roots root)))
  (when (> (len roots) WORK-DIR-ROOTS-MAX)
    (raise (ValueError f"{WORK-DIR-ROOTS-ENV} の根は {WORK-DIR-ROOTS-MAX} までであること(契約 node.spec.workDirRoots.maxItems): {(len roots)}")))
  (WorkDirRoots :roots (tuple roots)))


(defk held-work-dir-roots-of [entries]
  {:pre [(: entries tuple)]
   :post [(: % WorkDirRoots)]}
  "候補の根の在否 → 持っている根(段 12 lane 12j 追補・card acp:kanban-issue:ki-3bfe48a9d5dc)。entries = composition root
   (runtime.join_plan)が読んだ #(候補の根 その dir が在るか) の列(候補 = effects.WORK-DIR-ROOT-CANDIDATES)。⚠ 根は**実勢から
   導く** —— **現に在る dir だけ**を名乗り、宣言 file の値は 1 つも混ぜない。名簿(held-work-dirs-of)と違って根の下は
   1 つも列挙しない(会社 Mac の ~/.worktrees/ は 3,105 で、契約の名簿の上限 512 を遥かに越える)。形と上限の検は
   work-dir-roots-of と同じ規則で、外れは ValueError(参加しない)。判断はここ 1 点で I/O は無い。"
  (setv found [])
  (for [[root exists] entries]
    (when (not exists)
      (continue))
    (<- shaped bool (work-dir-root-shaped root))
    (when (not shaped)
      (raise (ValueError f"候補の根は ~/<名>/ の形(家からの相対・`/` で終わる・家そのものは根にしない)であること: {root !r}")))
    (when (not-in root found)
      (.append found root)))
  (when (> (len found) WORK-DIR-ROOTS-MAX)
    (raise (ValueError f"持つ根は {WORK-DIR-ROOTS-MAX} までであること(契約 node.spec.workDirRoots.maxItems): {(len found)}")))
  (WorkDirRoots :roots (tuple found)))


(defk service-account-borrower-of [token]
  {:pre [(: token (| str None))]
   :post [(: % (| str None))]}
  "pod の ServiceAccount の token → 借り手の等価鍵 `sa:<namespace>/<serviceaccount>`(card
   acp:kanban-issue:ki-40021864e62f)。読むのは JWT の payload の claims 2 つちょうど(CUSTODY-SA-NAMESPACE-CLAIM /
   CUSTODY-SA-NAME-CLAIM — 預かり所の backend が TokenReview で解く借り手名 ns/sa と 1 対 1)。
   ⚠ **署名は検めない**: この値は『自分は誰として借りるか』の名乗りで、認証するのは預かり所の側(TokenReview)。
   agentd が自分の token の claims を偽っても、預かり所が貸すのは TokenReview の答えの借り手にだけなので、
   嘘は『配車の束ねが間違う』ではなく『借りが 409 で断られる』に落ちる(嘘の得は無い)。
   読めない token(段が 3 つでない・payload が base64url でない・JSON でない・claims が無い・空)は **None**
   —— 推測しない(名乗らなければ配車は node 名で束ねる = この軸が無かった時と同じ・安全側)。"
  (when (is token None)
    (return None))
  (setv word (.strip token))
  (when (not word)
    (return None))
  (setv parts (.split word "."))
  (when (!= (len parts) 3)
    (return None))
  (setv payload (get parts 1))
  ;; base64url の padding は JWT が落とすので補って解く(長さの余りから足す)。
  (setv padded (+ payload (* "=" (% (- 4 (% (len payload) 4)) 4))))
  (try
    (setv claims (json.loads (.decode (base64.urlsafe-b64decode padded) "utf-8")))
    (except [Exception]
      (return None)))
  (when (not (isinstance claims dict))
    (return None))
  (setv namespace (.get claims CUSTODY-SA-NAMESPACE-CLAIM))
  (setv name (.get claims CUSTODY-SA-NAME-CLAIM))
  (when (or (not (isinstance namespace str)) (not (isinstance name str)))
    (return None))
  (when (or (not (.strip namespace)) (not (.strip name)))
    (return None))
  (+ CUSTODY-BORROWER-SA-PREFIX (.strip namespace) "/" (.strip name)))


(defk borrower-key-digest-of [key]
  {:pre [(: key (| str None))]
   :post [(: % (| str None))]}
  "借り手札 → 借り手の等価鍵 `key:<sha256(札) の先頭 16 hex>`(card acp:kanban-issue:ki-40021864e62f)。
   ⚠ **札の実値は 1 byte も鍵に入らない**(node の行は誰でも読める)。同じ札を持つ機体は同じ鍵になり、
   違う札なら違う鍵になる —— 預かり所の錠が借り手名で分かれるのと同じ分かれ方。無い・空 = None(名乗らない)。"
  (when (is key None)
    (return None))
  (setv word (.strip key))
  (when (not word)
    (return None))
  (setv digest (.hexdigest (hashlib.sha256 (.encode word "utf-8"))))
  (+ CUSTODY-BORROWER-KEY-PREFIX (cut digest 0 CUSTODY-BORROWER-KEY-HEX-CHARS)))


(defk custody-borrower-of [borrower-key service-account-token]
  {:pre [(: borrower-key (| str None)) (: service-account-token (| str None))]
   :post [(: % (| str None))]}
  "預かり所へ名乗る借り手の身元の**等価鍵**の判断の 1 点(card acp:kanban-issue:ki-40021864e62f・ACP 側の依頼
   lt-FMEPYFTCRQSKV4V8V0A82VQQFC)。材料は預かり所へ名乗る身元ちょうど 2 つで、handlers.CustodyHttp._identity_headers が
   header に組むのと**同じ材料**(第 2 の身元を発明しない)。I/O は無い —— file の読みは composition root
   (runtime.settings_from_env が handlers.read-secret-file で読む)。

   * SA token だけを宣言(k3s の pool の pod)→ `sa:<ns>/<sa>`(service-account-borrower-of)。
   * 借り手札だけを宣言(Mac の agentd)→ `key:<sha256 の先頭 16 hex>`(borrower-key-digest-of)。
   * どちらも無い → None(名乗らない — 預かり所を宣言していない機体)。
   * ⚠ **両方を宣言している機体は None**(名乗らない): 預かり所はその拍に 2 つの身元を受け取り、錠を
     どちらで分けるかを決めるのは**預かり所の側**。ACP は預かり所の知識を持たないので、ここで片方を
     選ぶのは推測になり、外した時は『別の借り手の機体を同じ借り手と読む』= 借りが 409 で断られて手番が
     何も始めないまま死ぬ(2026-09-15 13:51 JST の実弾そのもの)。名乗らなければ配車は node 名で束ね、
     今日と 1 bit も変わらない(安全側)。⇒ 本番の機体はどちらか一方だけを宣言している(pod = SA token・
     Mac = 借り手札)ので、この枝は据え付けの誤りの受け皿。

   答えは**名乗る値か None** で、途中の形の誤り(読めない token・空の札)も None —— 参加は断らない
   (この欄は任意で、名乗らない機体は今日どおり手番を受ける)。"
  (<- from-token (| str None) (service-account-borrower-of service-account-token))
  (<- from-key (| str None) (borrower-key-digest-of borrower-key))
  (cond
    (and (is-not from-token None) (is-not from-key None)) None
    (is-not from-token None) from-token
    (is-not from-key None) from-key
    True None))


(defk revision-of [text]
  {:pre [(: text (| str None))]
   :post [(: % (| str None))]}
  "agentd の版の刻印 revision の読み(段 12 lane 12j・agora-redesign #367): 宣言 file の [agentd].revision(doeff-agents の git sha —
   契約 node.spec.agentd.revision は 7〜64 字)。無い・空 = None(名乗らない — agentd は unstamped を名乗る)。長さの外は
   ValueError(参加しない — 黙って捨てない)。"
  (setv word (if (is text None) "" (.strip text)))
  (when (not word)
    (return None))
  (when (or (< (len word) 7) (> (len word) 64))
    (raise (ValueError f"[{TABLE-AGENTD}].{KEY-REVISION} は 7〜64 字(git sha)であること: {word !r}")))
  word)


(defk build-of [text]
  {:pre [(: text (| str None))]
   :post [(: % (| str None))]}
  "agentd の版の刻印 build の読み(段 12 lane 12j・agora-redesign #367): 宣言 file の [agentd].build(image の tag か local・
   契約は 128 字まで)。無い・空 = None(agentd は local を名乗る)。長すぎれば ValueError。"
  (setv word (if (is text None) "" (.strip text)))
  (when (not word)
    (return None))
  (when (> (len word) 128)
    (raise (ValueError f"[{TABLE-AGENTD}].{KEY-BUILD} は 128 字まで: {(cut word 0 40) !r}…")))
  word)


(defk drain-seconds-of [text]
  {:pre [(: text (| str None))]
   :post [(: % int)]}
  "停止(SIGTERM)の排水の上限の読み(段 12 lane 12j・agora-redesign #304 便 2): 宣言 file の [agentd].drain_seconds の文字列
   (10 進の非負の整数)→ int。無い・空 = 0(排水しない — 今日どおり即座に閉じる)。読めない値は ValueError(参加しない —
   黙って 0 に倒さない)。値は pod の terminationGracePeriodSeconds より小さく取る(超えると SIGKILL が先に来る)。"
  (setv word (if (is text None) "" (.strip text)))
  (when (not word)
    (return 0))
  (when (not (and (.isascii word) (.isdigit word)))
    (raise (ValueError f"[{TABLE-AGENTD}].{KEY-DRAIN-SECONDS} は 0 以上の整数(10 進の数字)であること: {word !r}")))
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


(defk places-of [text]
  {:pre [(: text (| str None))]
   :post [(: % Places)]}
  "機体が仕える置き場の集合の読み(段 11 lane 11u・agora-redesign #224・依頼者の裁定 2026-09-16): 宣言 file の
   [agentd].places / flag --places / env の , 区切りの文字列 → 閉語彙 company | personal の語の tuple(宣言の順・
   重複なし)。無い・空・語彙の外・同じ語の重複は ValueError(参加しない — 名乗らない agentd は配車の候補にならない)。
   綴りは ACP の契約 agora-kinds.json の profile.spec.boundary / node.spec.places.items と同じ(新しい語を作らない)。
   置き場は機体の所有で切る区画で、会社の資格は company を名乗る機体の外へ出ない(不変条件 I1)。会社 Mac は
   両方を名乗って会社と個人の worker として寄与する(operator 決定 2026-09-05)— 1 値(段 10 lane 10d 便 2)では
   言えなかった形。1 値の宣言を 1 要素の集合と読み替える互換は置かない(鍵の綴りが places へ移る)。"
  (setv words (lfor part (.split (if (is text None) "" text) PLACES-SEPARATOR)
                    :if (.strip part) (.strip part)))
  (when (not words)
    (raise (ValueError (+ "機体の置き場の集合が宣言されていない — 宣言 file の [" TABLE-AGENTD "]." KEY-PLACES
                          " か flag " FLAG-PLACES " で " (.join PLACES-SEPARATOR (sorted AGENTD-PLACES))
                          " の空でない部分集合を , 区切りで名乗る(段 11 lane 11u・agora-redesign #224: 名乗らない agentd は参加しない)"))))
  (for [word words]
    (when (not-in word AGENTD-PLACES)
      (raise (ValueError (+ f"[{TABLE-AGENTD}].{KEY-PLACES} の語は " (.join " | " (sorted AGENTD-PLACES))
                            f" のどれか: {word !r}")))))
  (when (!= (len (set words)) (len words))
    (raise (ValueError f"[{TABLE-AGENTD}].{KEY-PLACES} に同じ語が 2 度: {words !r}(集合なので 1 度だけ)")))
  (Places :words (tuple words)))


(defk allow-metered-billing-of [text]
  {:pre [(: text (| str None))]
   :post [(: % bool)]}
  "従量課金の binding kind を受けるかの読み(従量課金の便 lane A): 宣言 file の
   [agentd].allow_metered_billing / flag --allow-metered-billing の文字列 → bool。
   無い・空 = False(受けない — 既定は fail-closed)。閉語彙 \"true\" | \"false\" の外は
   ValueError(参加しない)。綴り違いを黙って False にしない — 黙った False は「許したはず
   なのに全部断られる」を無音で作り、黙った True は課金を無音で開ける
   (session-hooks-mode と同じ流儀: 語彙の外は fail-loud)。"
  (setv word (if (is text None) "" (.strip text)))
  (when (not word)
    (return False))
  (when (not-in word #{"true" "false"})
    (raise (ValueError (+ f"[{TABLE-AGENTD}].{KEY-ALLOW-METERED-BILLING} は true か false "
                          f"のどちらか(flag {FLAG-ALLOW-METERED-BILLING}): {word !r}"))))
  (= word "true"))


(defk seat-env-of [text]
  {:pre [(: text (| str None))]
   :post [(: % SeatEnv)]}
  "席へ運ぶ env の宣言の読み(段 12・agora-redesign #520・既知の形 = kubelet が node 局所の宣言を workload の env へ
   具現化する〔k3s の config.yaml → kubelet → 容器の env / systemd の EnvironmentFile=〕): 宣言 file の
   [agentd].seat_env(改行区切りの `NAME=value` の行)→ SeatEnv(#(名 値) の tuple・宣言の順)。
   無い・空 = 空の SeatEnv(宣言しない機体は今日どおり)。

   規則: 行の前後の空白は落とす / 空行と `#` で始まる行は飛ばす / `=` の無い行は ValueError /
   名が空・`[A-Za-z_][A-Za-z0-9_]*` の外(`NAME = value` の空白も形の外)は ValueError / 同じ名が 2 度は
   ValueError(どちらが勝つかを黙って決めない)/ 値は最初の `=` の後を**逐語** — 引用の剥がしも `${}` の
   展開もしない(第 2 の置換の言語を作らない)。

   参加の門(fail-closed)は 3 つの線で、どれも**名の形ちょうど**(値は 1 byte も見ない):
     (a) **家の関所 1 点の再利用** — 解いた表を policy.session-env-admission-error(launch の口と session.send の口が
         通るのと同じ 1 点 —「運ぶ口が増えても判定を並行実装しない」がその docstring)へ verb `join.seat_env` で通し、
         断りの文が返れば ValueError(参加しない)。線は binding 所有の auth env〔CLAUDE_CONFIG_DIR / CODEX_HOME〕と
         従量課金の形〔正規化して `_API_KEY` で終わる名と既知の別名〕。⚠ この判定を join.hy へ写さない(判定点が
         2 つになって片方だけが育つ)。贈り物でもある — CLAUDE_CONFIG_DIR が断られるので、node 全体の宣言で
         会話ごとの家を上書きする道が門の側からも塞がる。
     (b) **この口だけの線**(policy.seat-env-credential-shaped-offenders)— (a) の上に重ねる: 正規化した名を `_` で
         割り、区間のどれかが KEY / TOKEN なら断る + SECRET / PASSWORD / CREDENTIAL の部分一致で断る。末尾に錨を
         打たない(`_KEY` で終わる形だと `ANTHROPIC_API_KEY_PERSONAL` / `anthropic_api_key__personal` が通る)。
         (a) の線は 1 語も動かさない — 広げると launch / session.send の判定が同時に変わる(別 card)。
         札の path の綴り(`*_TOKEN_FILE` / `AGORA_BORROWER_KEY_PATH`)もここで断る: 札は家の既定の置き場への
         file の mount が唯一の形で、この口が運ぶのは宛先ちょうど。
     (c) 会話の身元が所有する名(effects.CONVERSATION-ID-ENV / SEAT-OPENER-ENV)は断る
         (置く点は手番ごとの judgment.charter-with-conversation-env の 1 点)。"
  (setv where f"[{TABLE-AGENTD}].{KEY-SEAT-ENV}")
  (setv pairs [])
  (setv seen [])
  (for [raw (.split (if (is text None) "" text) SEAT-ENV-SEPARATOR)]
    (setv line (.strip raw))
    (when (or (not line) (.startswith line "#"))
      (continue))
    (setv #(name separator value) (.partition line "="))
    (when (not separator)
      (raise (ValueError f"{where} の行は NAME=value であること(`=` が無い): {line !r}")))
    (when (not (and name
                    (.isascii name)
                    (or (.isalpha (get name 0)) (= (get name 0) "_"))
                    (all (gfor ch name (or (.isalnum ch) (= ch "_"))))))
      (raise (ValueError f"{where} の名は [A-Za-z_][A-Za-z0-9_]* であること: {name !r}(行 {line !r})")))
    (when (in name seen)
      (raise (ValueError f"{where} に同じ名が 2 度: {name !r}(どちらが勝つかを黙って決めない)")))
    (.append seen name)
    (.append pairs #(name value)))
  ;; (a) 資格の関所は家の 1 点(policy)— ここに判定を写さない(R30 (4)・R51 (1))。
  (setv admission (session-env-admission-error (dict pairs) "join.seat_env"))
  (when (is-not admission None)
    (raise (ValueError admission)))
  ;; (b) この口だけの線(policy の純粋の 1 点)— 区間 KEY / TOKEN と SECRET / PASSWORD / CREDENTIAL の部分一致。
  (<- shaped list (seat-env-credential-shaped-offenders (dict pairs)))
  (when shaped
    (raise (ValueError (+ f"{where} は宛先を運ぶ口で、資格の輸送路ではない(資格の形の名: "
                          f"{(.join ", " shaped)})。札は家の既定の置き場への file の mount で置き、"
                          "env では渡さない(ADR-DOE-AGENTS-012 R30 (4) / R51 (1))"))))
  (setv owned (sorted (lfor name seen :if (in name #{CONVERSATION-ID-ENV SEAT-OPENER-ENV}) name)))
  (when owned
    (raise (ValueError (+ f"{where} は会話の身元の名を宣言できない({(.join ", " owned)})— "
                          "置く点は手番ごとの judgment.charter-with-conversation-env の 1 点"))))
  (SeatEnv :pairs (tuple pairs)))


(defk claude-settings-file-of [text]
  {:pre [(: text (| str None))]
   :post [(: % (| str None))]}
  "席の settings file の名指しの読み(card acp:kanban-issue:ki-7b52bb76aa6e): 宣言 file の [agentd].claude_settings_file
   の文字列 → 綴り(strip)。無い・空 = None(名乗らない = 今日どおり)。形は絶対 path か `~` / `~/…`(agentd の HOME で
   composition root が展開)— cwd に依る相対 path は断る(どの cwd で読むかを黙って決めない)。file の読みと 3 つの門は
   runtime.join_plan(I/O)+ claude-settings-declaration-of(判断)で、**読めない file は断らない**(不在は名乗って参加する
   — R13 の訂正・依頼書 §10-2)。"
  (setv word (if (is text None) "" (.strip text)))
  (when (not word)
    (return None))
  (when (not (or (.startswith word "/") (= word "~") (.startswith word "~/")))
    (raise (ValueError (+ f"[{TABLE-AGENTD}].{KEY-CLAUDE-SETTINGS-FILE} は絶対 path か ~/… であること"
                          f"(cwd 相対は断る): {word !r}"))))
  word)


(defk instruction-sources-of [agentd]
  {:pre [(: agentd dict)]
   :post [(: % tuple)]}
  "席の家へ運ぶ共通の指示の名指しの読み(card acp:kanban-issue:ki-62aa1f4e9c9c 決定 D4 / D11):
   宣言 file の [agentd] の表 → (名簿の鍵, 綴り)の対の列(**名簿の順**・名指した種だけ)。
   名簿は policy.CARRIED-INSTRUCTION-SOURCES の 1 点で、ここは**回る**だけ — 種ごとの枝を持たない
   (1 種足す操作がこの関数を 1 行も動かさないことが D11 の守りたい形)。

   形は claude-settings-file-of と同じ 1 つの規則: 絶対 path か `~` / `~/…`(`~` は agentd の HOME で
   composition root が展開)。cwd に依る相対 path は断る(どの cwd で読むかを黙って決めない)。
   無い・空 = 名乗らない(その種は対の列に現れない = env にも現れない = 今日どおり)。
   現物の在否は**ここでは見ない**(D5: 不在は参加も起動も断らない — 名乗りは行の labels と
   起動の拍の 1 行)。"
  (setv pairs [])
  (for [source CARRIED-INSTRUCTION-SOURCES]
    (setv text (.get agentd source.key))
    (setv word (if (is text None) "" (.strip text)))
    (when word
      (when (not (or (.startswith word "/") (= word "~") (.startswith word "~/")))
        (raise (ValueError (+ f"[{TABLE-AGENTD}].{source.key} は絶対 path か ~/… であること"
                              f"(cwd 相対は断る): {word !r}"))))
      (.append pairs #(source.key word))))
  (tuple pairs))


(defk claude-settings-declaration-of [text session-hooks]
  {:pre [(: text (| str None)) (: session-hooks str)]
   :post [(: % (| dict None))]}
  "参加の門の 1 点(card acp:kanban-issue:ki-7b52bb76aa6e・ADR-DOE-AGENTS-004 R13): 宣言が名指した席の settings file の
   本文(読めたもの — **読めない = None**・読みは composition root)と宣言の session_hooks → 席へ渡す settings(dict)。
   外れは ValueError(参加しない — 宣言そのものの誤りだけを断る):
     (d) session_hooks が inherit であること — disabled の宣言に settings を足しても disableAllHooks が勝って hook は
         配られない(file を効かせる前提条件)。**file の在否に依らない**(宣言 file の 2 行の食い違いなので、
         degrade で file が消えた日にも同じく誤り)。だから先に検める。
     (b) JSON の object であること(配列・数・文字列は settings ではない)
     (c) doeff が置く鍵(impls/claude_code.hy CLAUDE-SETTINGS-OWNED-KEYS = disableAllHooks と記憶の置き場の鍵)を
         **含まない**こと — 含むと argv の合流で衝突し、黙って後勝ちにすれば hook か記憶の置き場のどちらかが無音で消える
   ⚠ **file が無い(text = None)は断らない**(R13 の訂正・依頼書 §10-2): 宿の入口は「先端で揃えられない日は image の
   下限へ戻して立つ」正規の degrade を持ち、その日の checkout に file は無い。そこで参加を断ると degrade が
   **pool 全体の capacity 0** に化ける(今日の欠陥より悪い)。不在は参加して名乗る = None を返し、名乗りは
   起動の拍の 1 行(launch.claude-settings-declaration)と node の行(judgment.node-labels-of)。
   宣言しない機体(名指し無し)はこの門を通らない(今日どおり)。"
  (setv where f"[{TABLE-AGENTD}].{KEY-CLAUDE-SETTINGS-FILE}")
  ;; (d) は宣言どうしの食い違いなので file を読む前に検める(不在の日も同じく誤り)。
  (when (!= session-hooks JOIN-SESSION-HOOKS-DEFAULT)
    (raise (ValueError (+ f"{where} は [{TABLE-AGENTD}].{KEY-SESSION-HOOKS} = {JOIN-SESSION-HOOKS-DEFAULT !r} の宣言にだけ"
                          f"効く(いま {session-hooks !r})— disableAllHooks が勝って名指した hook は 1 本も配られない。"
                          "hook を配らないなら名指しの行を消す"))))
  ;; 不在 = 非致命(依頼書 §10-2)。読めた時だけ中身を検める。
  (when (is text None)
    (return None))
  (try
    (setv parsed (json.loads text))
    (except [error ValueError]
      (raise (ValueError f"{where} が名指す file は JSON であること: {error}"))))
  (when (not (isinstance parsed dict))
    (raise (ValueError (+ f"{where} が名指す file は JSON の object(settings の表)であること: "
                          (. (type parsed) __name__)))))
  (setv owned (sorted (lfor key parsed :if (in key CLAUDE-SETTINGS-OWNED-KEYS) key)))
  (when owned
    (raise (ValueError (+ f"{where} が名指す file は doeff が置く鍵を持てない({(.join ", " owned)})— "
                          "hook の無効化と記憶の置き場は doeff が `--settings` の合流点で自分で置く"
                          "(ADR-DOE-AGENTS-004 R13・衝突は黙って後勝ちにしない)"))))
  parsed)


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
  ;; 停止の排水の上限(段 12 lane 12j・#304 便 2・任意)。
  (<- drain-seconds int (drain-seconds-of (.get agentd KEY-DRAIN-SECONDS)))
  ;; 機体が仕える置き場の集合(段 11 lane 11u — 1 値の place は退役)。
  (<- declared-places Places (places-of (.get agentd KEY-PLACES)))
  ;; node が持つ作業場の根(段 10 lane 10y 案 C・任意)。
  (<- declared-roots (| WorkRoots None) (work-roots-of (.get agentd KEY-WORK-ROOTS)))
  ;; agentd の版の刻印(段 12 lane 12j・#367・任意)。
  (<- revision (| str None) (revision-of (.get agentd KEY-REVISION)))
  (<- build (| str None) (build-of (.get agentd KEY-BUILD)))
  ;; 従量課金の binding kind を受けるか(従量課金の便 lane A・任意・既定 false)。
  (<- allow-metered bool (allow-metered-billing-of (.get agentd KEY-ALLOW-METERED-BILLING)))
  ;; 席へ運ぶ env の宣言(段 12・agora-redesign #520・任意)— 解釈と参加の門は seat-env-of の 1 点。
  (<- declared-seat-env SeatEnv (seat-env-of (.get agentd KEY-SEAT-ENV)))
  ;; 席の settings file の名指し(card ki-7b52bb76aa6e・任意)— 綴りだけ。読みと 3 つの門は runtime.join_plan(I/O)+
  ;; claude-settings-declaration-of(判断)で、**読めた日も読めない日も同じ**絶対 path が composition root からこの欄へ
  ;; 据え直される(不在は断らない — R13 の訂正・依頼書 §10-2)。
  (<- declared-settings-file (| str None) (claude-settings-file-of (.get agentd KEY-CLAUDE-SETTINGS-FILE)))
  ;; 席の家へ運ぶ共通の指示の名指し(card ki-62aa1f4e9c9c D4 / D11・任意)— 綴りと形だけ。
  ;; `~` の展開は composition root(runtime.join_plan)で、現物の在否はここでは見ない(D5)。
  (<- declared-instruction-sources tuple (instruction-sources-of agentd))
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
    :revision revision
    :build build
    ;; 席へ運ぶ env の対(agora-redesign #520)— 形と資格の締め出しは seat-env-of の 1 点(宣言しない = #())。
    :seat-env declared-seat-env.pairs
    :claude-settings-file declared-settings-file
    ;; 名簿の順・名指した種だけ(名簿を回った結果 — 種ごとの欄を持たない: D11)。
    :instruction-sources declared-instruction-sources
    :ownership ownership
    :capacity capacity
    :drain-seconds drain-seconds
    :places declared-places.words
    ;; 空文字は「名乗らない」= env に現れない(参加の門 record-sink-of が読みの 1 点で断る — 段 9f lane 9f-6)。
    ;; 従量課金の binding kind を受けるか(従量課金の便 lane A)— 真のときだけ host の argv に旗が立つ。
    :allow-metered-billing allow-metered
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
  ;; 段 12 lane 12j(#304 便 2): 排水の上限は名乗った時だけ env に現れる(0 = 既定 = 排水しない)。
  (when (> spec.drain-seconds 0)
    (.append env #(DRAIN-SECONDS-ENV (str spec.drain-seconds))))
  ;; 段 11 lane 11u: 置き場の集合は , 区切りの 1 文字列で運ぶ(読みは runtime の places-of の 1 点)。
  (.append env #(PLACES-ENV (.join PLACES-SEPARATOR spec.places)))
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
  ;; 段 12 lane 12j(#575 便 2): 持つ作業場は導いた時だけ env に現れる — 空の tuple も ""(何も持たない)として運ぶ。
  (when (is-not spec.work-dirs None)
    (.append env #(WORK-DIRS-ENV (.join WORK-DIRS-SEPARATOR spec.work-dirs))))
  (when (is-not spec.work-dir-roots None)
    (.append env #(WORK-DIR-ROOTS-ENV (.join WORK-DIR-ROOTS-SEPARATOR spec.work-dir-roots))))
  ;; 段 12(agora-redesign #520): 席へ運ぶ env は宣言した時だけ現れる(宣言の順・同じ改行区切りの綴りで運び、
  ;; 読み直しは同じ seat-env-of の 1 点 — 第 2 の解釈を作らない)。
  (when spec.seat-env
    (.append env #(SEAT-ENV-ENV (.join SEAT-ENV-SEPARATOR
                                       (lfor #(name value) spec.seat-env f"{name}={value}")))))
  ;; card ki-7b52bb76aa6e: 席の settings file は名指した時だけ env に現れる(composition root が門を通した絶対 path —
  ;; 読み手は launch.hy / headless.hy の起動の拍で、file をそのたびに読む。daemon の memory に中身を持たない)。
  (when (is-not spec.claude-settings-file None)
    (.append env #(CLAUDE-SETTINGS-FILE-ENV spec.claude-settings-file)))
  ;; card ki-62aa1f4e9c9c(D11): 席の家へ運ぶ共通の指示も同じ形 — 名指した種だけ env に現れる。
  ;; 綴りの対応は名簿の 1 点(policy.CARRIED-INSTRUCTION-SOURCES)で、ここは**回る**だけ。
  (setv carried-env-of (dfor source CARRIED-INSTRUCTION-SOURCES source.key source.env))
  (for [#(key path) spec.instruction-sources]
    (.append env #((get carried-env-of key) path)))
  ;; 段 12 lane 12j(#367): 版の刻印は名乗った時だけ env に現れる(無ければ agentd は unstamped / local を名乗る)。
  (when (is-not spec.revision None)
    (.append env #(AGENTD-REVISION-ENV spec.revision)))
  (when (is-not spec.build None)
    (.append env #(AGENTD-BUILD-ENV spec.build)))
  (when (is-not spec.ownership None)
    (.extend env [#(OWNERSHIP-ENV spec.ownership.grade)
                  #(OWNERSHIP-PROOF-ENV spec.ownership.proof)]))
  (when (is-not spec.record-url None)
    (.extend env [#(RECORD-URL-ENV spec.record-url)
                  #(RECORD-SPOOL-DIR-ENV (+ spec.state-dir "/" JOIN-RECORD-SPOOL-DIR))]))
  ;; 課金の階級の方針は host の argv だけが運ぶ(env の束に同名の名を足さない —
  ;; 方針の定義点は 1 つ = この旗)。false のときは旗を立てない(今日どおりの起動の形ちょうど)。
  (setv metered-argv
        (if spec.allow-metered-billing #(HOST-ALLOW-METERED-BILLING-FLAG) #()))
  (JoinPlan
    :host-argv (+ #(HOST-DB-FLAG (+ spec.state-dir "/" JOIN-DB-FILE)
                    HOST-SOCKET-FLAG (+ spec.state-dir "/" JOIN-SOCKET-FILE)
                    HOST-MAX-RUNNING-FLAG HOST-MAX-RUNNING-UNLIMITED
                    HOST-BACKEND-FLAG spec.backend)
                 metered-argv
                 #(HOST-SERVE-COMMAND))
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


(defk ownership-verdict [places ownership answer]
  {:pre [(: places tuple) (: ownership (| Ownership None)) (: answer ProbeAnswer)]
   :post [(: % (| Ownership None))]}
  "宣言と証拠の突合の 1 点。gce-project:<id> = metadata の project-id が id と一致する時だけ通す
   (読めない・違う = ValueError — 参加しない)。file:<path>=<値> = その file の中身が値と一致する時だけ
   通す(読めない・違う = ValueError)— 証拠を描いたのは据え付けの側で、ここは台帳を持たず(hostname も
   置き場も**所有の等級の判定**には使わない)証拠が動いていないことだけを検める。

   検めの引き金(card acp:kanban-issue:ki-d6cc49cbf33f 決定 D4 ③)は『所有を名乗ったか』ではなく
   『**特権の場所を名乗ったか**』: 宣言 file を他機体から写した agentd は places も node_name も一緒に
   写すので、両欄(ownership / ownership_proof)を空にすれば検めを 1 度も撃たずに company を名乗れた
   (実弾 2026-09-18 21:57: 個人 MacBook が会社 Mac の宣言 file で起動し 86 秒 company の行として
   配車された — 断ったのは custody の借り手の門だけ)。⇒ places に PRIVILEGED-PLACES の語が在るか、
   等級が company の宣言は、証拠(gce-project: / file:)が要る。declared も、所有を 1 欄も名乗らない
   宣言も断る。

   ⚠ R17 との区別: places から**等級を導く**形は今日も禁じられている(等級は宣言の grade ちょうど)。
   places が決めるのは『証拠が要るかどうか』だけで、判定の材料ではない — agentd は所有の台帳を持たない
   まま(描かれた証拠が動いていないことだけを検める)。非特権の語(personal / cluster)の扱いは今日のまま。"
  ;; 引き金(上の 2 節)。privileged = 宣言が名乗った特権の語(宣言の順)。
  (setv privileged (tuple (lfor word places :if (in word PRIVILEGED-PLACES) word)))
  (setv claims-company (and (is-not ownership None) (= ownership.grade OWNERSHIP-GRADE-COMPANY)))
  (setv needs-evidence (or (bool privileged) claims-company))
  (when needs-evidence
    (setv named (if privileged (.join PLACES-SEPARATOR privileged) OWNERSHIP-GRADE-COMPANY))
    (setv why (if privileged
                  f"[{TABLE-AGENTD}].{KEY-PLACES} が特権の置き場 {named} を名乗っている"
                  f"[{TABLE-AGENTD}].{KEY-OWNERSHIP} が {OWNERSHIP-GRADE-COMPANY} を名乗っている"))
    (setv how (+ f"{why}のに所有の証拠が無い(いま "
                 (if (is ownership None)
                     f"[{TABLE-AGENTD}].{KEY-OWNERSHIP} も [{TABLE-AGENTD}].{KEY-OWNERSHIP-PROOF} も空"
                     f"[{TABLE-AGENTD}].{KEY-OWNERSHIP-PROOF} = {OWNERSHIP-PROOF-DECLARED !r} = 検なし")
                 f")。特権の置き場を名乗る宣言は [{TABLE-AGENTD}].{KEY-OWNERSHIP-PROOF} に "
                 f"`{OWNERSHIP-PROOF-FILE-PREFIX}<絶対 path>=<値>` か "
                 f"`{OWNERSHIP-PROOF-GCE-PREFIX}<project-id>` が要る。"))
    (setv fix (+ "この 1 手で直る: dotfiles の checkout で `sh agent/venv-run.sh \"$PWD/agentcli\" hy "
                 "cron_management/acp_single_mac.hy --declaration <この機体の宣言> install --only agentd`"
                 "(証拠は据え付けの側が描く。宣言 file は機体ごと — 他機体の宣言 file で参加しない)。"))
    (when (or (is ownership None) (= ownership.proof OWNERSHIP-PROOF-DECLARED))
      (raise (ValueError (+ how fix)))))
  (when (is ownership None)
    (return None))
  (setv parts (ownership-proof-file-parts ownership.proof))
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
    (is-not parts None)
    (do
      (setv [path expected] parts)
      (cond
        (is answer.value None)
        (raise (ValueError (+ f"ownership {ownership.grade} claims {ownership.proof} but the file "
                              f"{path} could not be read (absent, unreadable or empty)")))
        (!= answer.value expected)
        (raise (ValueError (+ f"ownership {ownership.grade} claims {ownership.proof} but the file "
                              f"{path} holds {answer.value !r} (expected {expected !r})")))
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


(defk ownership-preflight [places ownership]
  {:pre [(: places tuple) (: ownership (| Ownership None))]
   :post [(: % (| Ownership None))]}
  "起動の前に 1 回撃つ検: 検の方法が証拠を要るなら OwnershipProbe を撃ち(gce-project も
   file: も撃つ・declared と所有の無い宣言は撃たない)、ownership-verdict で突合する。
   ⚠ 撃つ引き金は『所有を名乗ったか』ではなく『特権の場所を名乗ったか』(D4 ③)— 両欄が空でも
   places に特権の語が在れば断る(読む物が宣言されていないので、証拠は読みに行かずに断る)。"
  (if (or (is ownership None) (= ownership.proof OWNERSHIP-PROOF-DECLARED))
      (<- verdict (| Ownership None) (ownership-verdict places ownership (ProbeAnswer :value None)))
      (do
        (<- answer ProbeAnswer (OwnershipProbe :proof ownership.proof))
        (<- verdict (| Ownership None) (ownership-verdict places ownership answer))))
  verdict)
