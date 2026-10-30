;;; API server が止まっても、置き直された job が Secret・ConfigMap の値を得て起動できる事の検(ADR-DOE-CLUSTER-004)。
;;;
;;; 失敗ケース(10-29 の実例): API server の在る機体が止まり、そこで動いていた job がもう 1 台の worker へ置き直された。job は起動で宣言の
;;; 参照の値を API server に問い、届かずに SuppliedValueUnavailable で終わり、16 分で 17 回落ちた — 移り先が、移る原因と同じ障害の範囲に
;;; 入っていた。
;;;
;;;   写しで起動する    API server が答えていた間に worker が写しを持っていれば、止まった後に置かれた job の supplied-setting と
;;;                     supplied-directory が写しの値で答え、参照の綴りと写しを読んだ刻を warning の 1 行に出す
;;;   写しで答えない    API server が届いて 403・404 で断った参照・写しの無い参照・別の worker の写しは、写しで答えない(失敗ケース)
;;;   写しを合わせる    宣言から外れた参照の写しと、403・404 で断られた物の写しは消し、届かない間は前の写しを残す。値の参照は宣言が
;;;                     名指したキーだけを持つ
;;;   値を出さない      写しの file は 0600・dir は作業の根から下の全部の段が 0700。log の行と coordinator の返事に値は載らない(綴りだけ)
;;; 外の世界は模擬の API server の相手役(doeff_cluster._sim.k8s_supplied — down の世界は届かない)と memory の file(mode を確かめる検だけ
;;; 本物の file)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(import base64)
(import datetime [datetime timezone])
(import json)
(import os)
(import stat)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects [Listen MemoryFile MemoryFiles ReadMemoryFiles SlogEffect listen-handler slog-discard-handler state])
(import doeff_core_effects [memory-file-handler os-file-handler])
(import doeff_core_effects [EnvEntry ReadEnvironment])
(import doeff_time [sim-time-handler])
(import doeff_cluster [environ-table-reader])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.supplied_model [SuppliedKind SuppliedRef SuppliedValueUnavailable])
(import doeff_cluster.shared.protocol.supplied_values [HoldTally SERVICE-ACCOUNT-TOKEN-FILE hold-supplied supplied-directory supplied-setting
                                                      supplied-value])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob ClusterState HeartbeatReply WorkerInfo])
(import doeff_cluster.coordinator.core.cluster_policy [heartbeat-reply supplied-for])
(import doeff_cluster.coordinator.protocol.replies [reply-json])
(import doeff_cluster [HeldEntry HeldObject ReadGrant SuppliedCluster k8s-supplied-peer service-account-token-file])
(import tests.state_rows [table-from-rows])

(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(val TOKEN "worker-identity-token")
(val PG-URL "postgresql://ledger@pg.apps.svc:5432/ledger\n")
(val PG-PASSWORD "ledger-password-only-the-job-may-see")
(val DOTFILES-KEY "-----BEGIN OPENSSH PRIVATE KEY-----\ndotfiles-only\n-----END OPENSSH PRIVATE KEY-----\n")
(val HOOKS-KEY "-----BEGIN OPENSSH PRIVATE KEY-----\nhooks-only\n-----END OPENSSH PRIVATE KEY-----\n")
(val VALUES #(PG-URL PG-PASSWORD DOTFILES-KEY HOOKS-KEY))
(val KEYS-DIR "/work/supplied/secret/apps/deploy-keys")
(val HELD-AT (datetime 2026 10 29 14 0 0 :tzinfo timezone.utc))
;; job の宣言の :environ: 値 1 つの参照(ConfigMap)・物 1 つ丸ごとの参照(Secret)。
(val DECLARED {"RECORDS_PG_URL_REF" "configmap:apps/ledger-pg/url"
               "LAND_KEYS_REF" "secret:apps/deploy-keys"})
(val SPELLINGS #("configmap:apps/ledger-pg/url" "secret:apps/deploy-keys"))
(val PG-OBJECT (HeldObject :resource "configmaps" :namespace "apps" :name "ledger-pg"
                           :entries #((HeldEntry :key "url" :value PG-URL) (HeldEntry :key "password" :value PG-PASSWORD))))
(val KEYS-OBJECT (HeldObject :resource "secrets" :namespace "apps" :name "deploy-keys"
                             :entries #((HeldEntry :key "dotfiles" :value DOTFILES-KEY) (HeldEntry :key "agent-hooks" :value HOOKS-KEY))))
(val GRANTS #((ReadGrant :resource "configmaps" :namespace "apps" :name None) (ReadGrant :resource "secrets" :namespace "apps" :name None)))
(val UP (SuppliedCluster :token TOKEN :objects #(PG-OBJECT KEYS-OBJECT) :grants GRANTS))
(val DOWN (SuppliedCluster :token TOKEN :objects #(PG-OBJECT KEYS-OBJECT) :grants GRANTS :down True))
(val FRESH (MemoryFiles :files #((MemoryFile :path SERVICE-ACCOUNT-TOKEN-FILE :content (.encode (+ TOKEN "\n") "utf-8"))) :dirs #("/work")))
;; 写しを持つ worker(atlas)と、その上の job の環境変数(作業の根と worker の名 — worker が子へ渡す)。
(val ATLAS {"WORK_DIR" "/work" "DOEFF_WORKER_NAME" "atlas"})
(val ATLAS-COPY "/work/supplied-held/atlas/configmap/apps/ledger-pg.json")
(val KEYS-COPY "/work/supplied-held/atlas/secret/apps/deploy-keys.json")


(defhandler worker-environ [#^ dict environ]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: worker の環境変数は筋書きごとの外の世界の形(写しを持つ worker・別の worker)。
  ;; 機体の事実の読み(ReadEnvironment)に、表に在る名だけを答える(本番は worker と子の os.environ)。
  (ReadEnvironment [names]
    (resume (tuple (gfor name names :if (in name environ) (EnvEntry :name name :value (get environ name)))))))


(defk held-under [cluster files spellings]
  {:pre [(: cluster SuppliedCluster) (: files MemoryFiles) (: spellings tuple)] :post [(: % tuple)]}
  "cluster の世界で worker atlas が参照 spellings の写しを 1 回 持ち直した数えと、その後の file の置き場と、出した log の行を得るため。"
  (<- heard tuple (with_handlers [slog-discard-handler (state) (sim-time-handler :start-time HELD-AT) (worker-environ {"WORK_DIR" "/work"})
                                  (k8s-supplied-peer cluster) (memory-file-handler files) listen-handler]
                    (Listen (held-and-read spellings) :types #(SlogEffect))))
  #(#* (get heard 0) (get heard 1)))


(defk held-and-read [spellings]
  {:pre [(: spellings tuple)] :post [(: % tuple)]}
  "写しを 1 回 持ち直し、数えとその後の file の置き場を得るため(置き場は同じ答え手の中で読む)。"
  (<- tally HoldTally (hold-supplied "atlas" spellings))
  (<- store MemoryFiles (ReadMemoryFiles))
  #(tally store))


(defk job-read [cluster files environ body]
  {:pre [(: cluster SuppliedCluster) (: files MemoryFiles) (: environ dict) (: body (| Program EffectBase))] :post [(: % tuple)]}
  "cluster の世界で、環境変数が environ の worker の上の宣言 DECLARED の job が body を回した答え(断れば断りの文)と、出した log の行を得るため。"
  (<- heard tuple (with_handlers [slog-discard-handler (state) (environ-table-reader DECLARED) (worker-environ environ)
                                  (k8s-supplied-peer cluster) (memory-file-handler files) listen-handler]
                    (Listen (answer-or-refusal body) :types #(SlogEffect))))
  heard)


(defk answer-or-refusal [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % str)]}
  "job の読み body の答えか、断りの文(SuppliedValueUnavailable)を得るため。"
  (try
    (<- answer str body)
    answer
    (except [error SuppliedValueUnavailable]
      (+ "refused: " (str error)))))


(defk dir-files [files dir]
  {:pre [(: files MemoryFiles) (: dir str)] :post [(: % dict)]}
  "置き場 files の dir の直下の file を、名 → 中身の字にするため。"
  (dfor f files.files :if (= (get (.rpartition f.path "/") 0) dir) (get (.rpartition f.path "/") 2) (.decode f.content "utf-8")))


(defk copy-keys [files path]
  {:pre [(: files MemoryFiles) (: path str)] :post [(: % (| list None))]}
  "置き場 files の写し path が持つキーの名を並べるため(値は読まない — 無ければ None)。"
  (val found (lfor f files.files :if (= f.path path) f))
  (if found (sorted (get (json.loads (.decode (. (get found 0) content) "utf-8")) "data")) None))


(defk lines-text [lines]
  {:pre [(: lines list)] :post [(: % str)]}
  "log の行(SlogEffect の列)を、値が載っていないかを調べる 1 つの字にするため(msg と欄の全部)。"
  (.join "\n" (gfor line lines (+ line.msg " " (repr line.kwargs)))))


(defk without-values? [text]
  {:pre [(: text str)] :post [(: % bool)]}
  "字 text に、どの値も字のままでも base64 でも載っていないかを判じるため。"
  (not (any (gfor value VALUES
                  (or (in (.strip value) text) (in (.decode (base64.b64encode (.encode value "utf-8")) "ascii") text))))))


(deftest test-a-job-placed-while-the-api-server-is-down-starts-from-the-worker-copy
  ;; API server が答えている間に、worker atlas が自分に置かれうる job の参照の写しを持つ。
  (<- held tuple (held-under UP FRESH SPELLINGS))
  (setv #(tally store _) held)
  (assert (= tally (HoldTally :held 2 :kept 0 :dropped 0 :pruned 0)) tally)
  ;; API server が止まった後に atlas へ置かれた job は、値 1 つも物 1 つ丸ごとも写しで得て起動する。
  (<- url-read tuple (job-read DOWN store ATLAS (supplied-setting "RECORDS_PG_URL_REF")))
  (assert (= (get url-read 0) PG-URL) url-read)
  (<- dir-read tuple (job-read DOWN store ATLAS (supplied-directory "LAND_KEYS_REF")))
  (assert (= (get dir-read 0) KEYS-DIR) dir-read)
  ;; 写しで答えた読みは、参照の綴りと写しを読んだ刻を warning の 1 行に出す(値は出さない)。
  (val warned (get url-read 1))
  (assert (and (= (len warned) 1) (= (.get (. (get warned 0) kwargs) "level") "warning")
               (= (.get (. (get warned 0) kwargs) "ref") "configmap:apps/ledger-pg/url")
               (= (.get (. (get warned 0) kwargs) "read_at" (.get (. (get warned 0) kwargs) "read-at")) (.isoformat HELD-AT)))
          warned))


(deftest test-a-directory-written-from-the-copy-has-every-held-key
  (<- held tuple (held-under UP FRESH SPELLINGS))
  (<- whole tuple (with_handlers [(state) (environ-table-reader DECLARED) (worker-environ ATLAS) (k8s-supplied-peer DOWN)
                                  (memory-file-handler (get held 1)) slog-discard-handler]
                    (written-keys "LAND_KEYS_REF")))
  (assert (= (get whole 0) {"dotfiles" DOTFILES-KEY "agent-hooks" HOOKS-KEY}) whole))


(defk written-keys [name]
  {:pre [(: name str)] :post [(: % tuple)]}
  "設定 name の dir を書き出し、その dir の直下の file の名 → 中身を得るため。"
  (<- directory str (supplied-directory name))
  (<- store MemoryFiles (ReadMemoryFiles))
  (<- files dict (dir-files store directory))
  #(files))


(deftest test-a-refused-or-unheld-reference-is-not-answered-from-a-copy
  (<- held tuple (held-under UP FRESH SPELLINGS))
  (val store (get held 1))
  (val url-ref (SuppliedRef :kind SuppliedKind.CONFIGMAP :namespace "apps" :name "ledger-pg" :key "url"))
  ;; 失敗ケース 1: API server は届き、物が消されていた(404)— 写しが在っても写しで答えない。
  (<- deleted tuple (job-read (SuppliedCluster :token TOKEN :objects #(KEYS-OBJECT) :grants GRANTS) store ATLAS (supplied-value url-ref)))
  (assert (and (.startswith (get deleted 0) "refused: ") (in "404" (get deleted 0))) deleted)
  ;; 失敗ケース 2: 権限を外されていた(403)— 写しで答えない。
  (<- revoked tuple (job-read (SuppliedCluster :token TOKEN :objects #(PG-OBJECT KEYS-OBJECT) :grants #()) store ATLAS (supplied-value url-ref)))
  (assert (and (.startswith (get revoked 0) "refused: ") (in "403" (get revoked 0))) revoked)
  ;; 失敗ケース 3: 届かず、写しも無い worker(まだ写しを持っていない)— 今と同じく SuppliedValueUnavailable で断る。
  (<- unheld tuple (job-read DOWN FRESH ATLAS (supplied-value url-ref)))
  (assert (and (.startswith (get unheld 0) "refused: ") (in "届かない" (get unheld 0)) (in "写しも無い" (get unheld 0))) unheld)
  ;; 失敗ケース 4: 届かず、写しは別の worker(atlas)の物 — eos の上の job は atlas の写しで答えない。
  (<- elsewhere tuple (job-read DOWN store {"WORK_DIR" "/work" "DOEFF_WORKER_NAME" "eos"} (supplied-value url-ref)))
  (assert (.startswith (get elsewhere 0) "refused: ") elsewhere)
  ;; 失敗ケース 5: 届かず、写しの物は在るが宣言が名指していないキー — 写しは名指したキーしか持たないので断る。
  (<- unnamed tuple (job-read DOWN store ATLAS (supplied-value (SuppliedRef :kind SuppliedKind.CONFIGMAP :namespace "apps" :name "ledger-pg"
                                                                          :key "password"))))
  (assert (and (.startswith (get unnamed 0) "refused: ") (not-in PG-PASSWORD (get unnamed 0))) unnamed)
  ;; 判定が常に断る物でないこと: 届けば API server の値で答え、写しの 1 行は出さない。
  (<- fine tuple (job-read UP store ATLAS (supplied-value url-ref)))
  (assert (and (= (get fine 0) PG-URL) (= (get fine 1) [])) fine))


(deftest test-the-copy-follows-the-declarations-and-the-api-server-answers
  (<- first tuple (held-under UP FRESH SPELLINGS))
  ;; 値 1 つの参照は、宣言が名指したキーだけを持つ(物の password は持たない)。物 1 つ丸ごとの参照は全部のキー。
  (<- url-keys (| list None) (copy-keys (get first 1) ATLAS-COPY))
  (<- whole-keys (| list None) (copy-keys (get first 1) KEYS-COPY))
  (assert (and (= url-keys ["url"]) (= whole-keys ["agent-hooks" "dotfiles"])) #(url-keys whole-keys))
  ;; 届かない間は前の写しを残す(消さない・書き直さない)。
  (<- kept tuple (held-under DOWN (get first 1) SPELLINGS))
  (<- kept-keys (| list None) (copy-keys (get kept 1) ATLAS-COPY))
  (assert (and (= (get kept 0) (HoldTally :held 0 :kept 2 :dropped 0 :pruned 0)) (= kept-keys ["url"])) kept)
  ;; 宣言から外れた参照の写しは消す。
  (<- narrowed tuple (held-under UP (get first 1) #("secret:apps/deploy-keys")))
  (<- gone (| list None) (copy-keys (get narrowed 1) ATLAS-COPY))
  (assert (and (= (get narrowed 0) (HoldTally :held 1 :kept 0 :dropped 0 :pruned 1)) (is gone None)) narrowed)
  ;; 403・404 で断られた物の写しは消す(消された・権限を外された値を使い続けない)。
  (<- deleted tuple (held-under (SuppliedCluster :token TOKEN :objects #(PG-OBJECT) :grants GRANTS) (get first 1) SPELLINGS))
  (<- deleted-keys (| list None) (copy-keys (get deleted 1) KEYS-COPY))
  (assert (and (= (get deleted 0) (HoldTally :held 1 :kept 0 :dropped 1 :pruned 0)) (is deleted-keys None)) deleted))


(deftest test-the-copy-is-private-and-its-values-stay-out-of-the-log [tmp-path]
  ;; 本物の file で mode を確かめる(memory の置き場は dir の mode を持たない)。
  (val root (str tmp-path))
  (<- tally HoldTally (with_handlers [slog-discard-handler (state) (sim-time-handler :start-time HELD-AT) (worker-environ {"WORK_DIR" root})
                                      (k8s-supplied-peer UP) os-file-handler (service-account-token-file TOKEN)]
                        (hold-supplied "atlas" SPELLINGS)))
  (assert (= tally.held 2) tally)
  (val held-root (os.path.join root "supplied-held"))
  (var dirs [])
  (var files [])
  (for [#(dir _ names) (os.walk held-root)]
    (:= dirs (+ dirs [dir]))
    (:= files (+ files (lfor name names (os.path.join dir name)))))
  (val dir-modes (sfor d dirs (stat.S-IMODE (. (os.stat d) st-mode))))
  (val file-modes (sfor f files (stat.S-IMODE (. (os.stat f) st-mode))))
  (assert (and (= (len dirs) 6) (= dir-modes #{0o700})) #(dirs dir-modes))
  (assert (and (= (len files) 2) (= file-modes #{0o600})) #(files file-modes))
  ;; log の行(写しの持ち直しと、写しで答えた読み)に値は載らない — 字のままでも base64 でも。
  (<- held tuple (held-under UP FRESH SPELLINGS))
  (<- url-read tuple (job-read DOWN (get held 1) ATLAS (supplied-setting "RECORDS_PG_URL_REF")))
  (<- dir-read tuple (job-read DOWN (get held 1) ATLAS (supplied-directory "LAND_KEYS_REF")))
  (<- text str (lines-text (+ (get held 2) (get url-read 1) (get dir-read 1))))
  (<- clean bool (without-values? text))
  (assert (and clean (in "configmap:apps/ledger-pg/url" text)) text))


(deftest test-the-coordinator-names-only-the-spellings-of-jobs-the-worker-could-take
  ;; atlas と eos は能力 pool-a を名乗り、zeus は名乗らない。job a(pool-a が要る)は atlas か eos に置かれうる — 今どちらに置かれていても、
  ;; 両方の返事に a の参照の綴りが載る。job z(pool-z が要る)の参照は atlas に載らない。参照でない値(URL)は載らない。
  (val a (ClusterJob (JobSpec "a" "m" #() "rev" :environ #(#("KEYS_REF" "secret:apps/deploy-keys") #("PG_REF" "configmap:apps/ledger-pg/url")
                                                         #("PLAIN" "https://example.invalid/x")))
                     :needs #("pool-a")))
  (val z (ClusterJob (JobSpec "z" "m" #() "rev" :environ #(#("Z_REF" "secret:apps/z-only/key"))) :needs #("pool-z")))
  (val state (ClusterState #(a z) (! (table-from-rows {"atlas" (WorkerInfo "atlas" #("pool-a") 10 0 :task-reserve 0)
                                                       "eos" (WorkerInfo "eos" #("pool-a") 10 0 :task-reserve 0)
                                                       "zeus" (WorkerInfo "zeus" #("pool-z") 10 0 :task-reserve 0)}))))
  (<- atlas (| tuple None) (supplied-for state "atlas"))
  (<- eos (| tuple None) (supplied-for state "eos"))
  (<- zeus (| tuple None) (supplied-for state "zeus"))
  (assert (= atlas eos #("configmap:apps/ledger-pg/url" "secret:apps/deploy-keys")) #(atlas eos))
  (assert (= zeus #("secret:apps/z-only/key")) zeus)
  ;; 返事の JSON は綴りだけを運ぶ。名乗っていない worker への返事には欄を書かない(空の列を渡すと写しを全部消す)。
  (<- reply HeartbeatReply (heartbeat-reply state "atlas" (ClusterTiming)))
  (<- body dict (reply-json reply))
  (assert (= (get body "supplied") ["configmap:apps/ledger-pg/url" "secret:apps/deploy-keys"]) body)
  (<- unknown HeartbeatReply (heartbeat-reply state "nobody" (ClusterTiming)))
  (<- unknown-body dict (reply-json unknown))
  (assert (not-in "supplied" unknown-body) (sorted unknown-body)))
