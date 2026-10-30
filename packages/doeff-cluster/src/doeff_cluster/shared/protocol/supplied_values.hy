;;; job が要る値を、worker の中の file の path でなく、cluster が持つ ConfigMap・Secret の参照で受ける部品(型は
;;; doeff_cluster.shared.intent.supplied_model)。
;;;
;;;   KUBE-API ほか             自区画の API server と、k8s が worker の Pod に必ず置く ServiceAccount の dir の file(token・ca.crt)
;;;   supplied-ref-of           宣言の :environ の値の綴り「<種類>:<namespace>/<名>/<キー>」→ 値の参照(形の違う綴りは綴りを挙げて断る)
;;;   supplied-object-ref-of    同じく「<種類>:<namespace>/<名>」→ 物の参照
;;;   supplied-ref-text         値の参照 → 同じ綴り
;;;   supplied-value            値の参照 → 値(その job を回す worker の身元で、自区画の API server から読む)。job の code は直に呼ばない
;;;   supplied-setting          job が値を得る入口: 宣言の :environ の設定の名 → その設定に書いた値の参照の値
;;;   supplied-directory        job が file として要る値を得る入口: 宣言の :environ の設定の名 → その設定に書いた物の data を、キーごとに 1 file
;;;                             として worker の作業の根の下に書き出した dir の path
;;;   worker-fact               機体の事実の名 → 静的な worker の宣言が置いた環境変数の値
;;;
;;; 宣言の書き方: job の宣言の :environ には値そのものも path も書かず、参照の綴りを書く
;;;   "RECORDS_PG_URL_REF"   "configmap:<namespace>/<名>/url"   (値 1 つ — supplied-setting)
;;;   "LAND_KEYS_REF"        "secret:<namespace>/<名>"          (物 1 つ丸ごと — supplied-directory)
;;; 参照は秘密の中身を運ばない(:environ は coordinator の task の行に残る — 残るのは名とキーだけ)。
;;;
;;; 宣言で読む物が分かる: 入口 2 つは設定の名を受け、その job の宣言の :environ から参照を得る(名の Ask)。だから job が読むのは自分の宣言に
;;; 書いた参照だけ — 宣言に無い名は Ask に答えが無く、API server に問う前に落ちる。どの job が何を読むかは、宣言された全部の系の :environ を
;;; 見れば分かる。これは宣言を読めば分かる形を保つための決まりで、相手ごとの権限の絞りではない(cluster の側の権限は worker の身元 1 つの get)。
;;;
;;; 仕組み: 参照を API server の GET 1 本へ言い換え、doeff の汎用の effect(token の file の ReadText と HttpRequest・書き出しは file の effect・
;;; 機体の事実は ReadEnvironment)で問う。言い換えは環境で変わらず、本番と模擬で同じこの関数が動く。環境ごとに違うのは外側だけ(本番 =
;;; worker の file と、区画の CA で API server を検める接続 doeff_cluster.foundation.in_cluster_api の in-cluster-api-routed・模擬 = memory の
;;; file と API server の相手役 doeff_cluster._sim.k8s_supplied)。区画の CA で検める接続は使い手の process の外側が並べる(宛先が API server の
;;; 要求だけを区画の CA で検め、job の本体が外の系へ出す HTTP は既定の CA の client のまま)。
;;;
;;; 値は持たれているままを返す(前後の空白を落とさない — 鍵の file は行末の改行まで要る)。URL のように空白を落として使う値は、使い手が落とす。
;;; 断りの文に載せるのは参照の綴り・HTTP の status・API server の断りの本文の先頭・在るキーの名まで。
;;;
;;; 書き出しの形(supplied-directory): dir = <作業の根>/supplied/<種類>/<namespace>/<名>(作業の根 = 機体の事実 WORK_ROOT)。dir は 0700・file は
;;; 0600(ssh は他人が読める鍵を断る)で、file は別名に書いてから置き換える(書きかけを読ませない)。同じ物をもう 1 度 書き出すと、変わった値は
;;; 新しい中身に・物から消えたキーと dir の中の余所の物は消える。書く間は dir の隣の錠の file(<dir>.lock)を取る(同じ物を 2 つの job が同時に
;;; 書き出しても、片方の消しがもう片方の書きかけを消さない)。キーが file の名に成らない時(. や .. や / を含む)は何も書かずに断る。
;;;
;;; 機体の事実(worker-fact): 事実の名を環境変数の名へ言い換え、ReadEnvironment で問う。宣言の :environ の名の Ask でなく ReadEnvironment で
;;; 問うのは、無い名を値(空の列)で受けて、事実の名を挙げて断るため。環境変数の名は静的な worker の宣言と同じ字で、worker は子の process へ
;;; 渡す名にこの名を載せる。
;;;
;;; 値の写し(ADR-DOE-CLUSTER-004 — API server が止まっても、置き直された job が起動できるため):
;;;   hold-supplied             worker が、自分に置かれうる job の宣言の参照(heartbeat の返事の supplied — coordinator が並べる)の値を、
;;;                             API server が答えている間に読み、写しとして持つ。宣言から外れた参照の写しと、API server が 403・404 で断った
;;;                             参照の写しは消す(消された・権限を外された値を使い続けない)。届かない時は前の写しを残す
;;;   held-entries の写しの読み  job の読みが API server に届かない(HttpFailed)時だけ、その worker の写しで答え、参照の綴りと写しを読んだ刻を
;;;                             warning の 1 行に出す。届いて断られた時(403・404 ほか)は写しを読まず今と同じく断る。写しも無ければ断る
;;;   写しの形: file = <作業の根>/supplied-held/<worker の名>/<種類>/<namespace>/<名>.json(0600・置き換えで書く)・dir は 0700。中身は読んだ
;;;   刻と、宣言が名指したキーだけの data(Secret も ConfigMap も base64)。値は log・coordinator・記録の店へ出さない(出すのは綴りと刻だけ)。
;;;   worker の名で分けるのは、同じ作業の根を分ける別の worker が、互いの写しを「宣言から外れた」と消し合わないため(同じ名の 2 つの世代は
;;;   同じ写しを分ける — 置き換えで書くので、片方の書きかけを読まない)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defenum defrecord defwire])
(import base64)
(import binascii)
(import dataclasses [dataclass])  ; defrecord・defwire の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import json)
(import posixpath)
(import re)
(import urllib.parse [quote :as url-quote])
(import doeff_hy._frozen [FrozenMap])
(import doeff_hy._wire [Malformed parse-json])
(import doeff_core_effects [Ask slog])
(import doeff_core_effects [AcquireLock DirEntry FileFailed ListDirectory LockHeld MakeDirectory PathKind PathStat ReadText ReleaseLock
                            RemoveTree StatPath WriteBytes])
(import doeff_core_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_core_effects [EnvEntry ReadEnvironment])
(import doeff_cluster.shared.intent.supplied_model [SuppliedKind SuppliedObjectRef SuppliedRef SuppliedValueUnavailable WorkerFactMissing
                                                   WorkerFactName])
;; 参照の綴りの定義元は shared/core(coordinator が置かれうる job の参照を並べる判断と同じ字 — ADR-DOE-CLUSTER-004)。
(import doeff_cluster.shared.core.supplied_spelling [OBJECT-SPELLING REF-SPELLING])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms now-epoch-ms])

(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})

;; 自区画の API server と、k8s が worker の Pod に必ず置く ServiceAccount の dir の file。区画の CA で検める答え手
;; (doeff_cluster.foundation.in_cluster_api)も同じ字を持つ(層 foundation は protocol を import できない — 同じ字である事は検が縛る)。
(val KUBE-API "https://kubernetes.default.svc")
(val SERVICE-ACCOUNT-DIR "/var/run/secrets/kubernetes.io/serviceaccount")
(val SERVICE-ACCOUNT-TOKEN-FILE (+ SERVICE-ACCOUNT-DIR "/token"))
(val SERVICE-ACCOUNT-CA-FILE (+ SERVICE-ACCOUNT-DIR "/ca.crt"))
;; 機体の事実の名 → 静的な worker の宣言が置く環境変数の名(対応はここ 1 か所 — job は環境変数の名を知らない)。
(val FACT-ENVIRON-NAMES (FrozenMap {WorkerFactName.NODE_NAME "NODE_NAME"
                                    WorkerFactName.SYSTEMD_ROOT "WORKER_HOST_SYSTEMD_ROOT"
                                    WorkerFactName.WORK_ROOT "WORK_DIR"
                                    WorkerFactName.WORKER_NAME "DOEFF_WORKER_NAME"}))
;; 書き出す file の名に成るキー(data のキーの字で、. と .. でない — dir の外を指さない)。
(val FILE-KEY (re.compile r"^[A-Za-z0-9._-]+$"))
(val FILE-MODE 0o600)
(val DIRECTORY-MODE 0o700)
;; 断りの文に載せる API server の本文の長さと、要求 1 本の時間切れ(秒)。
(val REFUSAL-BODY-LIMIT 300)
(val REQUEST-TIMEOUT-SECONDS 10.0)
;; 値の写しの置き場の作業の根の下の名と、写しの file の名に成る worker の名の字(. と .. でない — dir の外を指さない)。
(val HELD-DIR-NAME "supplied-held")
(val WORKER-SPELLING (re.compile r"^[A-Za-z0-9][A-Za-z0-9._-]*$"))
;; API server が「その物は読ませない・無い」と答えた status — 写しを消す(消された・権限を外された値を使い続けない)。ほかの status
;; (401 の token の断り・5xx の API server の不調)は物の答えでないので写しを残す。
(val REFUSED-STATUSES #(403 404))


(defk text-field [value]
  {:pre [(: value str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "HTTP と file の答えの欄(doeff の http_effects・file_effects は Hy の module で型検査から見えない)を字として受けるため — 字でなければ
   :pre が欄の値を挙げて落ちる(黙って字に直さない)。"
  value)


(defk status-field [value]
  {:pre [(: value int)] :post [(: % int)] :tags {:context "doeff-cluster" :role "protocol"}}
  "HTTP の答えの status の欄を数として受けるため — 数でなければ :pre が欄の値を挙げて落ちる。"
  value)


(defwire HeldData
  "API server の ConfigMap・Secret の GET の答えのうち読む欄 — data(キー → 字。Secret は base64。k8s は空の欄を省くので、無ければ None)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get dict #(str str)) None) data None))


(defwire HeldCopy
  "値の写しの file の中身(頭の註の値の写し): read-at-ms = API server から読んだ刻(epoch ms)・data = 宣言が名指したキー → 中身の base64。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ int read-at-ms)
  (#^ (get dict #(str str)) data))


(defrecord CopyPlace
  "値の写しを引く所: root = worker の作業の根・worker = worker の名(写しの dir の名に成る字)。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ str root)
  (#^ str worker))


(defrecord NoCopy
  "写しで答えられない訳(why — 写しの path と理由。値は載せない)。job の読みは届かない断りにこの訳を添える。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ str why))


(defrecord HoldTally
  "worker が値の写しを 1 回 持ち直した数え(値は載せない): held = API server から読んで写しを書き直した物・kept = 届かず(か token が
   読めず・API server が物の答えでない status を返し)前の写しを残した物・dropped = 403・404 で断られて写しを消した物・pruned = 宣言から
   外れて消した写し。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ int held)
  (#^ int kept)
  (#^ int dropped)
  (#^ int pruned))


(defk worker-fact [name]
  {:pre [(: name WorkerFactName)] :post [(: % str) (> (len %) 0)] :tags {:context "doeff-cluster" :role "protocol"}}
  "機体の事実 name の値を得るため(前後の空白は落とす)。静的な worker の宣言が置く環境変数が無い・空なら、事実の名と環境変数の名を挙げて断る。"
  (val environ-name (get FACT-ENVIRON-NAMES name))
  (<- entries (get tuple #(EnvEntry ...)) (ReadEnvironment #(environ-name)))
  (val values (lfor entry entries :if (and (= entry.name environ-name) (.strip entry.value)) (.strip entry.value)))
  (when (not values)
    (raise (WorkerFactMissing (.format "機体の事実 {} に答える環境変数 {} が無いか空 — 静的な worker の宣言に置き、子の process へ渡す名に載せる"
                                       name.value environ-name))))
  (get values 0))


(defk service-account-text [path]
  {:pre [(: path str)] :post [(: % (| str FileFailed))] :tags {:context "doeff-cluster" :role "protocol"}}
  "ServiceAccount の dir の file 1 つを読み、前後の空白を落とすため(読めなければ FileFailed のまま返す — token は回るので要求のたびに読む)。"
  (<- read (| str FileFailed) (ReadText path))
  (match read
    (FileFailed) read
    _ (.strip (! (text-field read)))))


(defk kube-headers [token content-type]
  {:pre [(: token str) (: content-type str)] :post [(: % (get dict #(str str)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "API server への要求の見出しを doeff の HttpRequest の受ける綴りへ組むため(身元は ServiceAccount の token の Bearer・本文の型は
   content-type・答えは JSON)。"
  {"authorization" (+ "Bearer " token) "content-type" content-type "accept" "application/json"})


(defk supplied-ref-of [text]
  {:pre [(: text str)] :post [(: % SuppliedRef)] :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言の :environ の値の綴りを値の参照へ読むため。形の違う綴り(worker の中の path・種類の無い綴り・キーの欠け)は綴りを挙げて断る —
   path を書いた宣言を黙って通さない。"
  (val found (.match REF-SPELLING text))
  (when (is found None)
    (raise (ValueError (.format "値の参照の綴りでない: {!r} — 「configmap:<namespace>/<名>/<キー>」か「secret:<namespace>/<名>/<キー>」で書く" text))))
  (SuppliedRef :kind (SuppliedKind (.group found 1)) :namespace (.group found 2) :name (.group found 3) :key (.group found 4)))


(defk supplied-object-ref-of [text]
  {:pre [(: text str)] :post [(: % SuppliedObjectRef)] :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言の :environ の値の綴りを物の参照へ読むため。形の違う綴り(キーつき・worker の中の path・種類の無い綴り・名の欠け)は綴りを挙げて断る。"
  (val found (.match OBJECT-SPELLING text))
  (when (is found None)
    (raise (ValueError (.format "物の参照の綴りでない: {!r} — 「configmap:<namespace>/<名>」か「secret:<namespace>/<名>」で書く" text))))
  (SuppliedObjectRef :kind (SuppliedKind (.group found 1)) :namespace (.group found 2) :name (.group found 3)))


(defk supplied-ref-text [ref]
  {:pre [(: ref SuppliedRef)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "値の参照を宣言の綴りへ戻すため(断りの文と log が、宣言に書いた字と同じ字で参照を挙げる)。"
  (+ ref.kind.value ":" ref.namespace "/" ref.name "/" ref.key))


(defk object-path [object]
  {:pre [(: object SuppliedObjectRef)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "参照の指す ConfigMap か Secret の、API server の上の path を組むため。"
  (val resource (match object.kind
                  SuppliedKind.CONFIGMAP "configmaps"
                  SuppliedKind.SECRET "secrets"))
  (+ "/api/v1/namespaces/" (url-quote object.namespace :safe "") "/" resource "/" (url-quote object.name :safe "")))


(defk held-bytes [object spelled raw]
  {:pre [(: object SuppliedObjectRef) (: spelled str) (: raw str)] :post [(: % bytes)] :tags {:context "doeff-cluster" :role "protocol"}}
  "data のキーの字 raw を中身の bytes にするため(ConfigMap は字の UTF-8・Secret は base64 を解く)。解けない Secret は参照を挙げて断る(字は載せない)。"
  (match object.kind
    SuppliedKind.CONFIGMAP (.encode raw "utf-8")
    SuppliedKind.SECRET (try
                          (base64.b64decode raw :validate True)
                          (except [binascii.Error]
                            (raise (SuppliedValueUnavailable (+ spelled " の値を得られない — Secret の data が base64 でない")))))))


(defk entries-of-answer [object spelled answer]
  {:pre [(: object SuppliedObjectRef) (: spelled str) (: answer (| HttpResponse HttpFailed))] :post [(: % (get dict #(str bytes)))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "API server の答えから物の data(キー → 中身)を得るため。届かない・200 でない・本文の形が違う時は、参照の綴りと理由を挙げて断る
   (200 の本文は理由に載せない — Secret の値を含む)。"
  (when (isinstance answer HttpFailed)
    (raise (SuppliedValueUnavailable (+ spelled " の値を得られない — API server に届かない: " (! (text-field answer.detail))))))
  (val status (! (status-field answer.status)))
  (when (!= status 200)
    (raise (SuppliedValueUnavailable (+ spelled " の値を得られない — " (str status) " "
                                        (cut (! (text-field answer.text)) 0 REFUSAL-BODY-LIMIT)))))
  (<- parsed (| HeldData Malformed) (parse-json HeldData (! (text-field answer.text))))
  (when (isinstance parsed Malformed)
    (raise (SuppliedValueUnavailable (+ spelled " の値を得られない — API server の答えの本文の形が違う"))))
  (val entries {})
  (for [#(key raw) (.items (or parsed.data {}))]
    (<- content bytes (held-bytes object spelled raw))
    (setv (get entries key) content))
  entries)


(defk object-answer [object]
  {:pre [(: object SuppliedObjectRef)] :post [(: % (| HttpResponse HttpFailed FileFailed))] :tags {:context "doeff-cluster" :role "protocol"}}
  "物 object の GET を、worker の身元(ServiceAccount の token — 回るので問うたびに読む)で自区画の API server へ 1 本だけ出し、答えをそのまま
   得るため(token の file が読めなければ FileFailed — 問わない。再試行しない)。job の読みと worker の写しの持ち直しが同じ 1 本を使う。"
  (<- token (| str FileFailed) (service-account-text SERVICE-ACCOUNT-TOKEN-FILE))
  (when (isinstance token FileFailed)
    (return token))
  (<- headers (get dict #(str str)) (kube-headers token "application/json"))
  (<- path str (object-path object))
  (<- answer (| HttpResponse HttpFailed)
      (HttpRequest "GET" (+ KUBE-API path) :headers headers :timeout-seconds REQUEST-TIMEOUT-SECONDS :max-retries 0 :failures-as-values True))
  answer)


(defk object-text [object]
  {:pre [(: object SuppliedObjectRef)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "物の参照を宣言の綴り「<種類>:<namespace>/<名>」へ戻すため(写しの持ち直しが物ごとに参照を束ね、log に物を挙げる)。"
  (+ object.kind.value ":" object.namespace "/" object.name))


(defk held-copy-path [root worker object]
  {:pre [(: root str) (: worker str) (: object SuppliedObjectRef)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "worker の名 worker の、物 object の値の写しの file の path を組むため(頭の註の写しの形 — 作業の根 root の下)。"
  (posixpath.join root HELD-DIR-NAME worker object.kind.value object.namespace (+ object.name ".json")))


(defk copy-place []
  {:pre [] :post [(: % (| CopyPlace NoCopy))] :tags {:context "doeff-cluster" :role "protocol"}}
  "job を回す worker の作業の根と名(写しを引く所)を機体の事実から得るため。どちらかが無い・名が file の名に成らない worker は写しを
   持たないので、訳つきの NoCopy(写しで答えず、届かない断りに訳を添える)。"
  (try
    (<- root str (worker-fact WorkerFactName.WORK_ROOT))
    (<- worker str (worker-fact WorkerFactName.WORKER_NAME))
    (except [error WorkerFactMissing]
      (return (NoCopy :why (str error)))))
  (if (worker-spelled? worker)
      (CopyPlace :root root :worker worker)
      (NoCopy :why (.format "worker の名 {!r} が写しの dir の名に成らない" worker))))


(defk held-copy-entries [object spelled detail]
  {:pre [(: object SuppliedObjectRef) (: spelled str) (: detail str)] :post [(: % (| (get dict #(str bytes)) NoCopy))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "API server に届かない(detail)時に、job を回す worker の写しから物 object の data を得るため(頭の註の写しの読み)。写しが在れば、参照の
   綴り spelled と写しを読んだ刻を warning の 1 行に出して答える(値は出さない)。写しが無い・読めない・形が違う時は訳つきの NoCopy
   (呼び手が届かない断りに訳を添えて断る)。"
  (<- place (| CopyPlace NoCopy) (copy-place))
  (when (isinstance place NoCopy)
    (return place))
  (<- path str (held-copy-path place.root place.worker object))
  (<- read (| str FileFailed) (ReadText path))
  (when (isinstance read FileFailed)
    (return (NoCopy :why (+ "写し " path " が読めない: " read.detail))))
  (<- parsed (| HeldCopy Malformed) (parse-json HeldCopy (! (text-field read))))
  (when (isinstance parsed Malformed)
    (return (NoCopy :why (+ "写し " path " の形が違う"))))
  (val entries {})
  (for [#(key raw) (.items parsed.data)]
    (try
      (setv (get entries key) (base64.b64decode raw :validate True))
      (except [binascii.Error]
        (return (NoCopy :why (+ "写し " path " のキー " key " が base64 でない"))))))
  (<- (slog (+ spelled " の値を worker の写しで答える — API server に届かない: " detail)
            :level "warning" :ref spelled
            :read-at (.isoformat (datetime-of-epoch-ms parsed.read-at-ms))))
  entries)


(defk held-entries [object spelled]
  {:pre [(: object SuppliedObjectRef) (: spelled str)] :post [(: % (get dict #(str bytes)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "物 object の data を、その job を回す worker の身元(ServiceAccount の token — 回るので問うたびに読む)で自区画の API server から得るため
   (得られなければ綴り spelled と理由を挙げて断る — 既定の値で埋めない・再試行しない)。API server に届かない時だけ、worker の写しで答える
   (頭の註の値の写し — 届いて断られた時は写しを読まない)。"
  (<- answer (| HttpResponse HttpFailed FileFailed) (object-answer object))
  (when (isinstance answer FileFailed)
    (raise (SuppliedValueUnavailable (+ spelled " の値を得られない — ServiceAccount の token の file が読めない: " answer.detail))))
  (when (isinstance answer HttpFailed)
    (val detail (! (text-field answer.detail)))
    (<- copied (| (get dict #(str bytes)) NoCopy) (held-copy-entries object spelled detail))
    (when (isinstance copied NoCopy)
      (raise (SuppliedValueUnavailable (+ spelled " の値を得られない — API server に届かない: " detail "(worker の写しも無い: " copied.why ")"))))
    (return copied))
  (<- entries (get dict #(str bytes)) (entries-of-answer object spelled answer))
  entries)


(defk supplied-value [ref]
  {:pre [(: ref SuppliedRef)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "値の参照 ref の値を、その job を回す worker の身元で自区画の API server から得るため。キーが無い・中身が UTF-8 の字でない時は、参照の綴りと
   理由を挙げて断る(在るキーの名は挙げ、値は載せない)。"
  (<- spelled str (supplied-ref-text ref))
  (<- entries (get dict #(str bytes))
      (held-entries (SuppliedObjectRef :kind ref.kind :namespace ref.namespace :name ref.name) spelled))
  (when (not-in ref.key entries)
    (raise (SuppliedValueUnavailable (+ spelled " の値を得られない — キー " ref.key " が無い(在るキー: " (or (.join "・" (sorted entries)) "無し") ")"))))
  (try
    (.decode (get entries ref.key) "utf-8")
    (except [UnicodeDecodeError]
      (raise (SuppliedValueUnavailable (+ spelled " の値を得られない — 中身が UTF-8 の字でない"))))))


(defk supplied-setting [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "job の宣言の :environ の設定 name に書いた値の参照の値を得るため — job が値を得る入口(頭の註の「宣言で読む物が分かる」)。宣言に無い名は
   :environ の名の Ask に答えが無く、API server に問う前に落ちる。設定の値が値の参照の綴りでなければ、設定の名と綴りを挙げて断る。"
  (<- spelled str (Ask name))
  (when (is (.match REF-SPELLING spelled) None)
    (raise (ValueError (.format "設定 {} の値が値の参照の綴りでない: {!r} — 「configmap:<namespace>/<名>/<キー>」か「secret:<namespace>/<名>/<キー>」で書く" name spelled))))
  (<- ref SuppliedRef (supplied-ref-of spelled))
  (<- value str (supplied-value ref))
  value)


(defk written-entries [directory entries]
  {:pre [(: directory str) (: entries (get dict #(str bytes)))] :post [(: % (| FileFailed None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "物の data を dir directory へ、キーごとに 1 file(mode 0600・置き換えで)書き、data に無い名の物を dir から消すため(頭の註の書き出しの形)。
   書けない・消せない時は最初の失敗を値で返す(呼び手が錠を放してから断る)。"
  (for [key (sorted entries)]
    (<- written (| FileFailed None) (WriteBytes (posixpath.join directory key) (get entries key) :mode FILE-MODE :replace True))
    (when (isinstance written FileFailed)
      (return written)))
  (<- listed (| (get tuple #(DirEntry ...)) FileFailed) (ListDirectory directory))
  (when (isinstance listed FileFailed)
    (return listed))
  (for [entry listed]
    (when (not-in entry.name entries)
      (<- removed (| FileFailed None) (RemoveTree (posixpath.join directory entry.name)))
      (when (isinstance removed FileFailed)
        (return removed))))
  None)


(defk supplied-directory [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "job の宣言の :environ の設定 name に書いた物の data を、worker の作業の根の下の dir にキーごとに 1 file として書き出し、その dir の path を
   得るため — job が file として要る値(git の子 process が読む deploy key ほか)を得る入口(頭の註の書き出しの形)。宣言に無い名は API server に
   問う前に落ちる。設定の値が物の参照の綴りでない・値を得られない・file の名に成らないキーが在る・作業の根の事実が無い時は、何も書かずに断る。"
  (<- spelled str (Ask name))
  (when (is (.match OBJECT-SPELLING spelled) None)
    (raise (ValueError (.format "設定 {} の値が物の参照の綴りでない: {!r} — 「configmap:<namespace>/<名>」か「secret:<namespace>/<名>」で書く" name spelled))))
  (<- object SuppliedObjectRef (supplied-object-ref-of spelled))
  (<- entries (get dict #(str bytes)) (held-entries object spelled))
  (val unfit (sorted (gfor key entries :if (or (is (.match FILE-KEY key) None) (in key #("." ".."))) key)))
  (when unfit
    (raise (SuppliedValueUnavailable (+ spelled " を書き出せない — file の名に成らないキー: " (.join "・" unfit)))))
  (<- root str (worker-fact WorkerFactName.WORK_ROOT))
  (val directory (posixpath.join root "supplied" object.kind.value object.namespace object.name))
  (<- made (| FileFailed None) (MakeDirectory directory :mode DIRECTORY-MODE))
  (when (isinstance made FileFailed)
    (raise (SuppliedValueUnavailable (+ spelled " を書き出せない — dir " directory " を作れない: " made.detail))))
  (<- held (| LockHeld FileFailed) (AcquireLock (+ directory ".lock")))
  (when (isinstance held FileFailed)
    (raise (SuppliedValueUnavailable (+ spelled " を書き出せない — 錠 " directory ".lock を取れない: " held.detail))))
  (<- failed (| FileFailed None) (written-entries directory entries))
  (<- (ReleaseLock held))
  (when (isinstance failed FileFailed)
    (raise (SuppliedValueUnavailable (+ spelled " を書き出せない — " failed.path ": " failed.detail))))
  directory)


;; --- 値の写しの持ち直し(worker の側 — 頭の註の値の写し・ADR-DOE-CLUSTER-004) ---------------------------------------------------

(defenum HoldDone HELD KEPT DROPPED)
(setv HoldDone.__doc__ "物 1 つの写しの持ち直しで何をしたか: HELD = 読んで書き直した・KEPT = 前の写しを残した・DROPPED = 403・404 で消した。")


(defrecord HeldWant
  "写しに持つ物 1 つ: object = 物の参照・keys = 宣言が名指したキー(None = 物 1 つ丸ごとの参照が在る — 全部のキー)。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ SuppliedObjectRef object)
  (#^ (| frozenset None) keys))


(defk worker-spelled? [worker]
  {:pre [(: worker str)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "protocol"}}
  "worker の名が写しの dir の名に成るかを判じるため(. と .. と / を含む名は dir の外を指す)。"
  (and (is-not (.match WORKER-SPELLING worker) None) (not-in worker #("." ".."))))


(defk held-wants [spellings]
  {:pre [(: spellings tuple)] :post [(: % (get tuple #(HeldWant ...)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "heartbeat の返事の参照の綴り spellings(値 1 つか物 1 つ丸ごと)を、物ごとに束ねた写しに持つ物の列にするため(物の綴りの順)。同じ物の
   値の参照はキーを足し合わせ、物 1 つ丸ごとの参照が 1 つでも在れば全部のキーを持つ。参照の綴りでない字は綴りを挙げて断る。"
  (val objects {})
  (val keys {})
  (for [spelled spellings]
    (if (is-not (.match OBJECT-SPELLING spelled) None)
        (do (<- whole SuppliedObjectRef (supplied-object-ref-of spelled))
            (<- whole-text str (object-text whole))
            (setv (get objects whole-text) whole (get keys whole-text) None))
        (do (<- ref SuppliedRef (supplied-ref-of spelled))
            (val object (SuppliedObjectRef :kind ref.kind :namespace ref.namespace :name ref.name))
            (<- text str (object-text object))
            (setv (get objects text) object)
            (val named (.get keys text (frozenset)))
            (when (or (not-in text keys) (is-not named None))
              (setv (get keys text) (| (or named (frozenset)) (frozenset #(ref.key))))))))
  (tuple (gfor text (sorted objects) (HeldWant :object (get objects text) :keys (get keys text)))))


(defk copy-text [read-at-ms entries]
  {:pre [(: read-at-ms int) (: entries (get dict #(str bytes)))] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "写しの file の中身を JSON の字へ綴るため(JSON の境界の 1 点 — 欄は HeldCopy と同じ名・中身は base64)。"
  (json.dumps {"readAtMs" read-at-ms
               "data" (dfor key (sorted entries) key (.decode (base64.b64encode (get entries key)) "ascii"))}
              :sort-keys True))


(defk private-dirs [root path]
  {:pre [(: root str) (: path str)] :post [(: % (| FileFailed None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "作業の根 root の下の dir path を、root から 1 段ずつ mode 0700 で作るため(MakeDirectory の mode は新しく作った最後の dir にだけ効く —
   途中の段を他人の読める mode で残さない)。作れなければ最初の失敗を値で返す。"
  (val parts (.split (posixpath.relpath path root) "/"))
  (for [n (range 1 (+ (len parts) 1))]
    (<- made (| FileFailed None) (MakeDirectory (posixpath.join root #* (cut parts 0 n)) :mode DIRECTORY-MODE))
    (when (isinstance made FileFailed)
      (return made)))
  None)


(defk hold-one [root worker want read-at-ms]
  {:pre [(: root str) (: worker str) (: want HeldWant) (: read-at-ms int)] :post [(: % HoldDone)] :tags {:context "doeff-cluster" :role "protocol"}}
  "物 1 つ want の写しを API server の今の答えに合わせ、何をしたか(HoldDone)を得るため(頭の註の値の写し)。届かない・token が
   読めない・物の答えでない status(401・5xx)・本文の形が違う時は前の写しを残す。403・404 は写しを消す。書けない・消せない時は物と理由を
   挙げて断る(値は載せない)。"
  (<- spelled str (object-text want.object))
  (<- path str (held-copy-path root worker want.object))
  (<- answer (| HttpResponse HttpFailed FileFailed) (object-answer want.object))
  (when (isinstance answer #(HttpFailed FileFailed))
    (return HoldDone.KEPT))
  (val status (! (status-field answer.status)))
  (when (in status REFUSED-STATUSES)
    (<- removed (| FileFailed None) (RemoveTree path))
    (when (isinstance removed FileFailed)
      (raise (SuppliedValueUnavailable (+ spelled " の写しを消せない — " removed.path ": " removed.detail))))
    (return HoldDone.DROPPED))
  (when (!= status 200)
    (return HoldDone.KEPT))
  (try
    (<- entries (get dict #(str bytes)) (entries-of-answer want.object spelled answer))
    (except [SuppliedValueUnavailable]
      (return HoldDone.KEPT)))
  (val kept-entries (dfor #(key content) (.items entries) :if (or (is want.keys None) (in key want.keys)) key content))
  (<- made (| FileFailed None) (private-dirs root (posixpath.dirname path)))
  (when (isinstance made FileFailed)
    (raise (SuppliedValueUnavailable (+ spelled " の写しを書けない — dir を作れない: " made.path ": " made.detail))))
  (<- text str (copy-text read-at-ms kept-entries))
  (<- written (| FileFailed None) (WriteBytes path (.encode text "utf-8") :mode FILE-MODE :replace True))
  (when (isinstance written FileFailed)
    (raise (SuppliedValueUnavailable (+ spelled " の写しを書けない — " written.path ": " written.detail))))
  HoldDone.HELD)


(defk listed-names [path kind]
  {:pre [(: path str) (: kind PathKind)] :post [(: % (get tuple #(str ...)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "dir path の直下の、種類 kind の物の名を並べるため(dir が無ければ空 — まだ何も持っていない worker)。読めない dir は理由を挙げて断る。"
  (<- seen (| PathStat FileFailed) (StatPath path))
  (when (and (isinstance seen PathStat) (= seen.kind PathKind.MISSING))
    (return #()))
  (<- listed (| (get tuple #(DirEntry ...)) FileFailed) (ListDirectory path))
  (when (isinstance listed FileFailed)
    (raise (SuppliedValueUnavailable (+ "写しの dir " path " を読めない: " listed.detail))))
  (tuple (sorted (gfor entry listed :if (= entry.kind kind) entry.name))))


(defk pruned-copies [base wanted]
  {:pre [(: base str) (: wanted frozenset)] :post [(: % int)] :tags {:context "doeff-cluster" :role "protocol"}}
  "worker の写しの dir base(<作業の根>/supplied-held/<worker の名>)の下の、持つ物の写しの path wanted に無い file を消し、消した数を得る
   ため(宣言から外れた参照の写しを持ち続けない)。消せなければ path と理由を挙げて断る。"
  (var count 0)
  (<- kinds tuple (listed-names base PathKind.DIRECTORY))
  (for [kind kinds]
    (<- spaces tuple (listed-names (posixpath.join base kind) PathKind.DIRECTORY))
    (for [space spaces]
      (val dir (posixpath.join base kind space))
      (<- names tuple (listed-names dir PathKind.FILE))
      (for [name names]
        (val path (posixpath.join dir name))
        (when (not-in path wanted)
          (<- removed (| FileFailed None) (RemoveTree path))
          (when (isinstance removed FileFailed)
            (raise (SuppliedValueUnavailable (+ "宣言から外れた写し " path " を消せない: " removed.detail))))
          (:= count (+ count 1))))))
  count)


(defk hold-supplied [worker spellings]
  {:pre [(: worker str) (: spellings tuple)] :post [(: % HoldTally)] :tags {:context "doeff-cluster" :role "protocol"}}
  "worker の名 worker の値の写しを、その worker に置かれうる job の宣言の参照 spellings(heartbeat の返事の supplied)に合わせて 1 回
   持ち直すため(頭の註の値の写し): 物ごとに API server へ 1 本問うて写しを書き直し(届かなければ前の写しを残す・403・404 なら消す)、
   宣言から外れた写しを消す。worker の名が dir の名に成らない・作業の根の事実が無い時は、何も書かずに断る。"
  (when (not (worker-spelled? worker))
    (raise (ValueError (.format "worker の名 {!r} が写しの dir の名に成らない" worker))))
  (<- root str (worker-fact WorkerFactName.WORK_ROOT))
  (<- wants tuple (held-wants spellings))
  (<- read-at-ms int (now-epoch-ms))
  (var dones #())
  (var paths (frozenset))
  (for [want wants]
    (<- path str (held-copy-path root worker want.object))
    (:= paths (| paths (frozenset #(path))))
    (<- done HoldDone (hold-one root worker want read-at-ms))
    (:= dones (+ dones #(done))))
  (<- pruned int (pruned-copies (posixpath.join root HELD-DIR-NAME worker) paths))
  (HoldTally :held (.count dones HoldDone.HELD) :kept (.count dones HoldDone.KEPT) :dropped (.count dones HoldDone.DROPPED) :pruned pruned))
