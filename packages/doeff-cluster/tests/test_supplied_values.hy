;;; job が要る値を、worker の中の file の path でなく cluster の ConfigMap・Secret の参照で受ける部品(doeff_cluster.shared.protocol.supplied_values)
;;; と、その要求を区画の CA で検める答え手(doeff_cluster.foundation.in_cluster_api の in-cluster-api-routed)の検 — 使い手の repo の job が
;;; 同じ定義元から値を受ける(使い手の repo に在った同じ部品をここへ移した)。
;;;
;;;   綴り              宣言の :environ の値の綴り「<種類>:<namespace>/<名>/<キー>」と「<種類>:<namespace>/<名>」を参照へ読む。path や、種類・
;;;                     区切りの違う綴りは綴りを挙げて断る(失敗ケース)
;;;   値を得る          ConfigMap のキーは字のまま・Secret のキーは base64 を解いた字のまま(前後の空白も落とさない)
;;;   宣言で読む物     入口 supplied-setting・supplied-directory は、その job の宣言の :environ に書いた設定の名でだけ値を得る(宣言に無い名は
;;;                     API server に問う前に落ちる・path を書いた設定は設定の名を挙げて断る — 失敗ケース)
;;;   書き出す          物 1 つ丸ごとを <作業の根>/supplied/<種類>/<namespace>/<名> に、キーごとに 1 file(mode 0600)で書き、置き直すと cluster に
;;;                     無いキーと余所の file は消える
;;;   断る(失敗ケース)  権限の無い名(403)・無い名(404)・無いキー・名乗れない token(401)・読めない token の file・届かない API server・作業の根の
;;;                     事実が無い worker・file の名に成らないキーは、参照の綴りと理由を挙げて断る。断りの文に Secret の値と token は載らない
;;;   振り分け          宛先が自区画の API server の要求だけを区画の CA の口へ回し、ほかの宛先は外側の答え手へ流す。CA の file が無い worker では
;;;                     API server 宛ての要求を出さずに断る(失敗ケース)
;;; 外の世界は模擬の API server の相手役(doeff_cluster.sim.k8s_supplied)と memory の file。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff [with_handlers])
(import doeff_vm [UnhandledEffect])
(import doeff_core_effects.file_effects [MemoryFile MemoryFiles ReadMemoryFiles])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.process_effects [EnvEntry ReadEnvironment])
(import doeff_cluster.foundation.host_contract [environ-table-reader])
(import doeff_cluster.foundation.in_cluster_api [CLUSTER-API SERVICE-ACCOUNT-CA-PATH ClusterCaMissing cluster-ca-answer in-cluster-api-routed])
(import doeff_cluster.shared.intent.supplied_model [SuppliedKind SuppliedObjectRef SuppliedRef SuppliedValueUnavailable WorkerFactMissing])
(import doeff_cluster.shared.protocol.supplied_values [KUBE-API SERVICE-ACCOUNT-CA-FILE SERVICE-ACCOUNT-TOKEN-FILE supplied-directory
                                                      supplied-object-ref-of supplied-ref-of supplied-ref-text supplied-setting supplied-value])
(import doeff_cluster.sim.k8s_supplied [HeldEntry HeldObject ReadGrant SuppliedCluster k8s-supplied-peer])

(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(val TOKEN "worker-identity-token")
(val SECRET-VALUE "-----BEGIN KEY-----\nonly-the-job-may-see-this\n-----END KEY-----\n")
(val PG-URL "postgresql://ledger@pg.apps.svc:5432/ledger\n")
(val DOTFILES-KEY "-----BEGIN OPENSSH PRIVATE KEY-----\ndotfiles-only\n-----END OPENSSH PRIVATE KEY-----\n")
(val HOOKS-KEY "-----BEGIN OPENSSH PRIVATE KEY-----\nhooks-only\n-----END OPENSSH PRIVATE KEY-----\n")
(val KEYS-DIR "/work/supplied/secret/apps/deploy-keys")
;; cluster が持つ物: 権限の在る ConfigMap と Secret 2 つ・権限の無い Secret(身元の get の外)。
(val CLUSTER (SuppliedCluster
               :token TOKEN
               :objects #((HeldObject :resource "configmaps" :namespace "apps" :name "ledger-pg-url" :entries #((HeldEntry :key "url" :value PG-URL)))
                          (HeldObject :resource "secrets" :namespace "apps" :name "repo-read-key" :entries #((HeldEntry :key "id" :value SECRET-VALUE)))
                          (HeldObject :resource "secrets" :namespace "apps" :name "deploy-keys"
                                      :entries #((HeldEntry :key "dotfiles" :value DOTFILES-KEY) (HeldEntry :key "agent-hooks" :value HOOKS-KEY)))
                          (HeldObject :resource "secrets" :namespace "vault" :name "master-key" :entries #((HeldEntry :key "key" :value "never-readable"))))
               :grants #((ReadGrant :resource "configmaps" :namespace "apps" :name None)
                         (ReadGrant :resource "secrets" :namespace "apps" :name None))))
(val WORKER-FILES (MemoryFiles :files #((MemoryFile :path SERVICE-ACCOUNT-TOKEN-FILE :content (.encode (+ TOKEN "\n") "utf-8"))) :dirs #("/work")))
(val WORKER {"WORK_DIR" "/work"})
(val URL-REF (SuppliedRef :kind SuppliedKind.CONFIGMAP :namespace "apps" :name "ledger-pg-url" :key "url"))
(val KEY-REF (SuppliedRef :kind SuppliedKind.SECRET :namespace "apps" :name "repo-read-key" :key "id"))
;; job の宣言の :environ の見本: 値 1 つの参照・物 1 つ丸ごとの参照・権限の無い物・worker の中の path を書いてしまった設定。
(val DECLARED {"RECORDS_PG_URL_REF" "configmap:apps/ledger-pg-url/url"
               "LAND_KEYS_REF" "secret:apps/deploy-keys"
               "MASTER_KEY_REF" "secret:vault/master-key"
               "RECORDS_PG_URL_FILE" "/etc/ledger-pg/url"})


(defhandler worker-environ [#^ dict environ]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: worker の環境変数は筋書きごとの外の世界の形(作業の根の事実が在る worker・無い worker)。
  ;; 機体の事実の読み(ReadEnvironment)に、表に在る名だけを答える(本番は worker の子の os.environ)。
  (ReadEnvironment [names]
    (resume (tuple (gfor name names :if (in name environ) (EnvEntry :name name :value (get environ name)))))))


(defk refusal-of [files cluster ref]
  {:pre [(: files MemoryFiles) (: cluster SuppliedCluster) (: ref SuppliedRef)] :post [(: % (| str None))]}
  "worker の file が files・cluster が cluster の世界で参照 ref の値を問うた時の断りの文を得るため(断らなければ None)。"
  (try
    (<- (with_handlers [(state) (k8s-supplied-peer cluster) (memory-file-handler files)] (supplied-value ref)))
    None
    (except [error SuppliedValueUnavailable]
      (str error))))


(defk spelling-refused? [read text]
  {:pre [(: read Callable) (: text str)] :post [(: % bool)]}
  "綴り text が読み手 read に断られ、断りの文が綴りを挙げるかを判じるため。"
  (try
    (<- (read text))
    False
    (except [error ValueError]
      (in text (str error)))))


(deftest test-a-reference-is-read-from-its-spelling-and-anything-else-is-refused
  (<- url SuppliedRef (supplied-ref-of "configmap:apps/ledger-pg-url/url"))
  (<- key SuppliedRef (supplied-ref-of "secret:apps/repo-read-key/id"))
  (assert (= #(url key) #(URL-REF KEY-REF)) #(url key))
  (<- back str (supplied-ref-text key))
  (assert (= back "secret:apps/repo-read-key/id") back)
  (<- keys SuppliedObjectRef (supplied-object-ref-of "secret:apps/deploy-keys"))
  (assert (= keys (SuppliedObjectRef :kind SuppliedKind.SECRET :namespace "apps" :name "deploy-keys")) keys)
  ;; 失敗ケース: worker の中の path・種類の無い綴り・知らない種類・キーの欠け・余った区切りは、値の参照に成らない。
  (for [text #("/etc/ledger-pg/url" "apps/ledger-pg-url/url" "file:apps/ledger-pg-url/url"
               "configmap:apps/ledger-pg-url" "configmap:apps/ledger-pg-url/url/extra" "secret:apps//key" "")]
    (<- refused bool (spelling-refused? supplied-ref-of text))
    (assert refused text))
  ;; 失敗ケース: キーつき・path・名の欠けは、物の参照に成らない。
  (for [text #("secret:apps/deploy-keys/dotfiles" "/etc/deploy-keys" "secret:apps" "secret:apps/" "")]
    (<- refused bool (spelling-refused? supplied-object-ref-of text))
    (assert refused text)))


(deftest test-a-readable-key-is-answered-as-it-is-held
  ;; ConfigMap は字のまま・Secret は base64 を解いた字のまま(行末の改行も落とさない — 鍵の file は行末の改行まで要る)。
  (<- url str (with_handlers [(state) (k8s-supplied-peer CLUSTER) (memory-file-handler WORKER-FILES)] (supplied-value URL-REF)))
  (<- key str (with_handlers [(state) (k8s-supplied-peer CLUSTER) (memory-file-handler WORKER-FILES)] (supplied-value KEY-REF)))
  (assert (= #(url key) #(PG-URL SECRET-VALUE)) #(url key)))


(deftest test-a-value-that-cannot-be-had-is-refused-with-the-reference-and-the-reason
  ;; 失敗ケース 1: 身元の get の権限の外の Secret は 403 — 値は 1 字も返らない。
  (val forbidden-ref (SuppliedRef :kind SuppliedKind.SECRET :namespace "vault" :name "master-key" :key "key"))
  (<- forbidden (| str None) (refusal-of WORKER-FILES CLUSTER forbidden-ref))
  (assert (and (is-not forbidden None) (in "secret:vault/master-key/key" forbidden) (in "403" forbidden)
               (not-in "never-readable" forbidden))
          forbidden)
  ;; 失敗ケース 2: 権限は在るがまだ作られていない名は 404。
  (<- absent (| str None) (refusal-of WORKER-FILES CLUSTER (SuppliedRef :kind SuppliedKind.CONFIGMAP :namespace "apps" :name "not-created-yet" :key "url")))
  (assert (and (is-not absent None) (in "configmap:apps/not-created-yet/url" absent) (in "404" absent)) absent)
  ;; 失敗ケース 3: 在る名の、無いキー。在るキーの名は挙げるが、値は載せない。
  (<- no-key (| str None) (refusal-of WORKER-FILES CLUSTER (SuppliedRef :kind SuppliedKind.SECRET :namespace "apps" :name "repo-read-key" :key "id_rsa")))
  (assert (and (is-not no-key None) (in "id_rsa" no-key) (in "id" no-key) (not-in "only-the-job-may-see-this" no-key)) no-key)
  ;; 失敗ケース 4: API server が認めない token は 401 — 断りの文に token は載らない。
  (val stale (MemoryFiles :files #((MemoryFile :path SERVICE-ACCOUNT-TOKEN-FILE :content b"expired-token\n")) :dirs #()))
  (<- unauthorized (| str None) (refusal-of stale CLUSTER URL-REF))
  (assert (and (is-not unauthorized None) (in "401" unauthorized) (not-in "expired-token" unauthorized)) unauthorized)
  ;; 失敗ケース 5: token の file が無い worker。
  (<- no-token (| str None) (refusal-of (MemoryFiles :files #() :dirs #()) CLUSTER URL-REF))
  (assert (and (is-not no-token None) (in "configmap:apps/ledger-pg-url/url" no-token) (in "token" no-token)) no-token)
  ;; 失敗ケース 6: 届かない API server。
  (<- down (| str None) (refusal-of WORKER-FILES (SuppliedCluster :token TOKEN :objects #() :grants #() :down True) URL-REF))
  (assert (and (is-not down None) (in "configmap:apps/ledger-pg-url/url" down) (in "届かない" down)) down)
  ;; 判定が常に断る物でないこと: 揃っていれば断らない。
  (<- fine (| str None) (refusal-of WORKER-FILES CLUSTER URL-REF))
  (assert (is fine None) fine))


(defk setting-under [name]
  {:pre [(: name str)] :post [(: % str)]}
  "宣言の :environ が DECLARED の job が、設定 name の値を入口 supplied-setting で問うた答えを得るため。"
  (<- value str (with_handlers [(state) (environ-table-reader DECLARED) (k8s-supplied-peer CLUSTER) (memory-file-handler WORKER-FILES)]
                  (supplied-setting name)))
  value)


(deftest test-a-job-reads-only-the-references-its-own-declaration-names
  (<- url str (setting-under "RECORDS_PG_URL_REF"))
  (assert (= url PG-URL) url)
  ;; 失敗ケース 1: cluster が worker の身元に許している名でも、この job の宣言に無い設定の名では読めない — 宣言の :environ の問いに答えが
  ;; 無く、API server に問う前に落ちる。
  (var unanswered None)
  (try
    (<- (setting-under "READ_KEY_REF"))
    (except [error UnhandledEffect]
      (:= unanswered (str error))))
  (assert (and (is-not unanswered None) (in "READ_KEY_REF" unanswered) (not-in "only-the-job-may-see-this" unanswered)) unanswered)
  ;; 失敗ケース 2: 設定の値に worker の中の path を書いた宣言は、設定の名と綴りを挙げて断る。
  (var refused None)
  (try
    (<- (setting-under "RECORDS_PG_URL_FILE"))
    (except [error ValueError]
      (:= refused (str error))))
  (assert (and (is-not refused None) (in "RECORDS_PG_URL_FILE" refused) (in "/etc/ledger-pg/url" refused)) refused))


(defrecord Attempt
  "書き出しを 1 度試みた結果: answer = 答えた dir の path(断れば None)・refusal = 断りの文(答えれば None)・store = その後の file の置き場。"
  (#^ (| str None) answer)
  (#^ (| str None) refusal)
  (#^ MemoryFiles store))


(defrecord HeldFile
  "dir の直下の file 1 つ: content = 中身(UTF-8 の字)・mode = 権限の mode。"
  (#^ str content)
  (#^ (| int None) mode))


(defrecord Refused
  "書き出しの断り: text = 断りの文。"
  (#^ str text))


(defk directory-or-refusal [name]
  {:pre [(: name str)] :post [(: % (| str Refused))]}
  "設定 name の dir を書き出し、答えた dir の path か断りの文を得るため。"
  (try
    (<- written str (supplied-directory name))
    written
    (except [error #(SuppliedValueUnavailable ValueError WorkerFactMissing UnhandledEffect)]
      (Refused :text (str error)))))


(defk attempt [name]
  {:pre [(: name str)] :post [(: % Attempt)]}
  "設定 name の dir を書き出し、答え(断れば断りの文)と、その後の file の置き場を得るため(置き場は同じ答え手の中で読む — 断った後も)。"
  (<- result (| str Refused) (directory-or-refusal name))
  (<- store MemoryFiles (ReadMemoryFiles))
  (match result
    (Refused :text text) (Attempt :answer None :refusal text :store store)
    (str) (Attempt :answer result :refusal None :store store)))


(defk attempt-under [cluster files worker name]
  {:pre [(: cluster SuppliedCluster) (: files MemoryFiles) (: worker dict) (: name str)] :post [(: % Attempt)]}
  "worker の file が files・環境変数が worker・cluster が cluster の世界で、宣言 DECLARED の job が設定 name の dir を書き出した結果を得るため。"
  (<- outcome Attempt (with_handlers [(state) (environ-table-reader DECLARED) (worker-environ worker) (k8s-supplied-peer cluster)
                                      (memory-file-handler files)]
                        (attempt name)))
  outcome)


(defk work-files [store]
  {:pre [(: store MemoryFiles)] :post [(: % list)]}
  "置き場 store の作業の根の下の file の path の列を得るため(断った時に何も書いていない事を確かめる)。"
  (lfor f store.files :if (.startswith f.path "/work/") f.path))


(defk files-in [store dir]
  {:pre [(: store MemoryFiles) (: dir str)] :post [(: % dict)]}
  "置き場 store の dir の直下の file を、名 → 中身と mode にするため。"
  (dfor f store.files :if (= (get (.rpartition f.path "/") 0) dir)
        (get (.rpartition f.path "/") 2) (HeldFile :content (.decode f.content "utf-8") :mode f.mode)))


(deftest test-every-key-of-the-object-becomes-a-private-file-under-the-work-root
  (<- outcome Attempt (attempt-under CLUSTER WORKER-FILES WORKER "LAND_KEYS_REF"))
  (assert (and (= outcome.answer KEYS-DIR) (is outcome.refusal None)) outcome)
  ;; キーごとに 1 file・中身は持たれているまま(行末の改行も落とさない)・mode 0600(ssh は他人が読める鍵を断る)。
  (<- held dict (files-in outcome.store KEYS-DIR))
  (assert (= held {"dotfiles" (HeldFile :content DOTFILES-KEY :mode 0o600) "agent-hooks" (HeldFile :content HOOKS-KEY :mode 0o600)}) held))


(deftest test-writing-again-follows-the-object-and-drops-what-it-no-longer-holds
  (<- first Attempt (attempt-under CLUSTER WORKER-FILES WORKER "LAND_KEYS_REF"))
  (val before first.store)
  ;; dir の中に余所の file(前の書きの残り)を置き、cluster の側はキー agent-hooks を消して dotfiles の値を替える。
  (val stale (MemoryFiles :files (+ before.files #((MemoryFile :path (+ KEYS-DIR "/left-over") :content b"old" :mode 0o600)))
                          :dirs before.dirs :links before.links))
  (val rotated (SuppliedCluster :token TOKEN
                                :objects #((HeldObject :resource "secrets" :namespace "apps" :name "deploy-keys"
                                                       :entries #((HeldEntry :key "dotfiles" :value "rotated\n"))))
                                :grants CLUSTER.grants))
  (<- second Attempt (attempt-under rotated stale WORKER "LAND_KEYS_REF"))
  (<- held dict (files-in second.store KEYS-DIR))
  (assert (and (= second.answer KEYS-DIR) (= held {"dotfiles" (HeldFile :content "rotated\n" :mode 0o600)})) #(second held)))


(deftest test-a-directory-that-cannot-be-had-is-refused-and-nothing-is-written
  ;; 失敗ケース 1: この job の宣言に無い設定の名 — 宣言の :environ の問いに答えが無く、API server に問う前に落ちる。
  (<- undeclared Attempt (attempt-under CLUSTER WORKER-FILES WORKER "OTHER_KEYS_REF"))
  (<- undeclared-files list (work-files undeclared.store))
  (assert (and (is-not undeclared.refusal None) (in "OTHER_KEYS_REF" undeclared.refusal) (= undeclared-files [])) undeclared)
  ;; 失敗ケース 2: 設定がキーつきの綴り(値 1 つ)か worker の中の path — 設定の名と綴りを挙げて断る。
  (for [name #("RECORDS_PG_URL_REF" "RECORDS_PG_URL_FILE")]
    (<- refused Attempt (attempt-under CLUSTER WORKER-FILES WORKER name))
    (<- refused-files list (work-files refused.store))
    (assert (and (is-not refused.refusal None) (in name refused.refusal) (in (get DECLARED name) refused.refusal) (= refused-files []))
            refused))
  ;; 失敗ケース 3: 身元に get の権限の無い物(403)— 値は断りの文に 1 字も載らない。
  (<- forbidden Attempt (attempt-under CLUSTER WORKER-FILES WORKER "MASTER_KEY_REF"))
  (<- forbidden-files list (work-files forbidden.store))
  (assert (and (is-not forbidden.refusal None) (in "secret:vault/master-key" forbidden.refusal) (in "403" forbidden.refusal)
               (not-in "never-readable" forbidden.refusal) (= forbidden-files []))
          forbidden)
  ;; 失敗ケース 4: 作業の根の事実(環境変数 WORK_DIR)が無い worker。
  (<- rootless Attempt (attempt-under CLUSTER WORKER-FILES {} "LAND_KEYS_REF"))
  (<- rootless-files list (work-files rootless.store))
  (assert (and (is-not rootless.refusal None) (in "WORK_DIR" rootless.refusal) (= rootless-files [])) rootless)
  ;; 失敗ケース 5: file の名に成らないキー(dir の外を指す名)— どのキーも書かずに断る。
  (val escaping-cluster (SuppliedCluster :token TOKEN
                                         :objects #((HeldObject :resource "secrets" :namespace "apps" :name "deploy-keys"
                                                                :entries #((HeldEntry :key "dotfiles" :value DOTFILES-KEY) (HeldEntry :key ".." :value "x"))))
                                         :grants CLUSTER.grants))
  (<- escaping Attempt (attempt-under escaping-cluster WORKER-FILES WORKER "LAND_KEYS_REF"))
  (<- escaping-files list (work-files escaping.store))
  (assert (and (is-not escaping.refusal None) (in ".." escaping.refusal) (= escaping-files [])) escaping)
  ;; 判定が常に断る物でないこと: 揃っていれば断らない。
  (<- fine Attempt (attempt-under CLUSTER WORKER-FILES WORKER "LAND_KEYS_REF"))
  (assert (is fine.refusal None) fine))


;; --- 区画の CA の振り分け(in-cluster-api-routed)---------------------------------------------------------------------------------------

;; 在らない CA の file(cluster の Pod でない worker の姿)。
(val ABSENT-CA "/nonexistent/doeff-cluster-test/serviceaccount/ca.crt")
;; 外側の答え手が答えた印(外側へ流れた事を見分ける)。
(val OUTER-STATUS 299)


(defhandler outer-client []
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 外側の答え手の代役: 届いた要求に外側の印で答える。
  (HttpRequest [url]
    (resume (HttpResponse :status OUTER-STATUS :headers {} :content b"" :text "" :url url :elapsed-seconds 0.0))))


(defk routed-answer-of [method url]
  {:pre [(: method str) (: url str)] :post [(: % (| HttpResponse HttpFailed))]}
  "外側の代役と、その内側の in-cluster-api-routed(CA の file は在らない)の下で要求 1 本を出し、その答えを得るため。"
  (<- answer (| HttpResponse HttpFailed) (with_handlers [(outer-client) (in-cluster-api-routed CLUSTER-API ABSENT-CA)]
                                                       (HttpRequest method url :max-retries 0 :failures-as-values True)))
  answer)


(deftest test-the-routed-destination-and-ca-are-the-ones-the-value-reader-uses
  ;; 振り分ける宛先と CA の file は、値を読む部品が要求を出す宛先と k8s が置く CA の file と同じ字(層 foundation は protocol を import
  ;; できないので 2 か所に在る — 片方だけ直すと、値の要求が外側の既定の CA の client へ流れる)。
  (assert (= #(CLUSTER-API SERVICE-ACCOUNT-CA-PATH) #(KUBE-API SERVICE-ACCOUNT-CA-FILE)) #(CLUSTER-API KUBE-API)))


(deftest test-a-request-to-another-destination-is-left-to-the-outer-client
  ;; 外の系への要求は区画の CA で検めない。API server に似た名・http の綴り・別の port・port の読めない URL も API server でない。
  (for [url ["https://api.github.com/repos/example/app"
             "https://kubernetes.default.svc.example.com/api/v1/namespaces/apps/configmaps/x"
             "http://kubernetes.default.svc/api/v1/namespaces/apps/configmaps/x"
             "https://kubernetes.default.svc:6443/api/v1/namespaces/apps/configmaps/x"
             "https://kubernetes.default.svc:abc/api/v1/namespaces/apps/configmaps/x"]]
    (<- answer (| HttpResponse HttpFailed) (routed-answer-of "GET" url))
    (assert (and (isinstance answer HttpResponse) (= answer.status OUTER-STATUS)) #(url answer))))


(deftest test-a-request-to-the-api-server-is-not-left-to-the-outer-client
  ;; 失敗ケース: API server 宛ての要求(port の書き省き・:443・大文字の綴り・GET 以外)を外側の既定の CA の client へ流さない — 区画の CA で
  ;; 答える口へ回る(CA の file が無いので、その口が path を挙げて断る)。
  (for [#(method url) [#("GET" (+ CLUSTER-API "/api/v1/namespaces/apps/configmaps/ledger-pg-url"))
                       #("GET" (+ CLUSTER-API ":443/api/v1/namespaces/apps/secrets/deploy-keys"))
                       #("GET" "HTTPS://KUBERNETES.DEFAULT.SVC/api/v1/namespaces/apps/configmaps/ledger-pg-url")
                       #("PATCH" (+ CLUSTER-API "/api/v1/namespaces/apps/secrets/x"))]]
    (<- answer (| HttpResponse HttpFailed) (routed-answer-of method url))
    (assert (and (isinstance answer HttpFailed) (= answer.kind HttpFailureKind.CONNECT-FAILED) (in ABSENT-CA answer.detail))
            #(method url answer))))


(deftest test-without-the-cluster-ca-file-an-api-request-is-refused-without-being-sent
  ;; 失敗ケース: CA の file が無い worker で、API server 宛ての要求を出さない(外側の既定の CA で検め直さない)。断りは宛先と CA の file の path を挙げる。
  (val url (+ CLUSTER-API "/api/v1/namespaces/apps/configmaps/ledger-pg-url"))
  (<- failed (| HttpResponse HttpFailed) (cluster-ca-answer ABSENT-CA (HttpRequest "GET" url :max-retries 0 :failures-as-values True)))
  (assert (and (isinstance failed HttpFailed) (= failed.url url) (= failed.kind HttpFailureKind.CONNECT-FAILED) (in ABSENT-CA failed.detail))
          failed)
  (var raised None)
  (try
    (<- (cluster-ca-answer ABSENT-CA (HttpRequest "GET" url :max-retries 0)))
    (except [error ClusterCaMissing]
      (:= raised error)))
  (assert (and (isinstance raised ClusterCaMissing) (in ABSENT-CA (str raised)) (in url (str raised))) raised))
