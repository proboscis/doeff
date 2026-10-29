;;; k8s の API server の、名の決まった ConfigMap・Secret の読み取りの相手役(模擬の環境)。job が要る値を参照で受ける部品
;;; (doeff_cluster.shared.protocol.supplied_values の supplied-value・supplied-directory)が出す
;;; `GET /api/v1/namespaces/<namespace>/configmaps/<名>` と `GET /api/v1/namespaces/<namespace>/secrets/<名>` に、API server と同じ意味で答える。
;;;
;;; 契約の出どころ(k8s の API の決まり):
;;;   * 身元 = Authorization の Bearer の token。API server が認めない token は 401(kind Status)。
;;;   * 権限 = get(Role の resources・verbs [get]。resourceNames で名を指す事も、書かずに namespace の全部に効かせる事もできる)。
;;;     権限の判定は物が在るかの判定より先 — 権限の無い名は、在っても無くても 403(kind Status・message に資源の種類と名)。
;;;   * 権限は在るが物が無い名は 404(kind Status・reason NotFound)。
;;;   * 在る物は 200。本文は kind・metadata(name・namespace)・data。ConfigMap の data は字のまま、Secret の data は base64。空の data は欄ごと省く。
;;;   * 届かない API server は、HTTP の答えでなく接続の失敗(失敗を値で受ける要求には HttpFailed・そうでなければ接続の例外)。
;;; 答えるのは上の 2 種類の path への GET だけで、ほかの要求は外側へ通す。
;;;
;;; 世界は引数で受ける(筋書きごとに違う外の世界そのものの形 — 何が在り、worker の身元に何が読めるか)。値を持つだけで、要求では変わらない。
;;;
;;; 世界を job の宣言から組む部品:
;;;   held-value-of・held-whole-of  宣言の :environ の参照の綴り(値 1 つ・物 1 つ丸ごと)が指す物を、与えた中身で持つ物にする(namespace と名を
;;;                                 宣言と 2 か所に書かない)
;;;   read-grant-of                 物 1 つに名を指した get の権限
;;;   service-account-token-file    worker の ServiceAccount の token の file(k8s が Pod に置く path)の読みにだけ token で答える(手元の機体の本物の
;;;                                 file の答え手の内側に置く — 手元の機体には ServiceAccount の dir が無い)
(require doeff-hy.macros [defhandler defk <- val])
(require doeff-hy.record [defrecord])
(import base64)
(import json)
(import re)
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import httpx [ConnectError])
(import doeff_core_effects.file_effects [ReadText])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_cluster.shared.protocol.supplied_values [KUBE-API OBJECT-SPELLING REF-SPELLING SERVICE-ACCOUNT-TOKEN-FILE text-field])

(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})

;; API server に届かない時の断りの字(本番の接続の断りと同じ綴り)。
(val UNREACHABLE "[Errno 111] Connection refused")
;; 答える path の形(資源の種類は configmaps か secrets)。
(val OBJECT-PATH (re.compile r"^/api/v1/namespaces/([^/]+)/(configmaps|secrets)/([^/]+)$"))
(val KIND-OF-RESOURCE {"configmaps" "ConfigMap" "secrets" "Secret"})
;; 参照の綴りの種類 → 資源の種類。
(val RESOURCE-OF-KIND {"configmap" "configmaps" "secret" "secrets"})


(defrecord HeldEntry
  "cluster が持つ物の data の 1 項目: key = キーの名・value = 平文の値(Secret も平文で持ち、答える時に base64 に綴る)。"
  (#^ str key)
  (#^ str value))


(defrecord HeldObject
  "cluster が持つ ConfigMap か Secret 1 つ: resource = 資源の種類(configmaps・secrets)・namespace・name・entries = data の項目。"
  {:check [(in resource #("configmaps" "secrets"))]}
  (#^ str resource)
  (#^ str namespace)
  (#^ str name)
  (#^ (get tuple #(HeldEntry ...)) entries))


(defrecord ReadGrant
  "worker の身元に与えた get の権限 1 つ: resource = 資源の種類・namespace・name = 指した名(None = その namespace の全部 — Role に
   resourceNames を書かない形)。物が在るかとは別 — 権限だけ先に在る事もある。"
  {:check [(in resource #("configmaps" "secrets"))]}
  (#^ str resource)
  (#^ str namespace)
  (#^ (| str None) name))


(defrecord SuppliedCluster
  "API server の相手役の世界: token = API server が認める worker の身元の token・objects = cluster が持つ物・grants = その身元の get の権限・
   down = API server に届かない世界か。"
  (#^ str token)
  (#^ (get tuple #(HeldObject ...)) objects)
  (#^ (get tuple #(ReadGrant ...)) grants)
  (setv #^ bool down False))


(defk held-value-of [spelled value]
  {:pre [(: spelled str) (: value str)] :post [(: % HeldObject)] :tags {:context "doeff-cluster" :role "foundation"}}
  "宣言の :environ の値 1 つの参照の綴り spelled(「<種類>:<namespace>/<名>/<キー>」)が指す物を、そのキーに value を持つ物にするため
   (綴りでない字は綴りを挙げて断る)。"
  (val found (.match REF-SPELLING spelled))
  (when (is found None)
    (raise (ValueError (.format "値の参照の綴りでない: {!r}" spelled))))
  (HeldObject :resource (get RESOURCE-OF-KIND (.group found 1)) :namespace (.group found 2) :name (.group found 3)
              :entries #((HeldEntry :key (.group found 4) :value value))))


(defk held-whole-of [spelled entries]
  {:pre [(: spelled str) (: entries (get tuple #(HeldEntry ...)))] :post [(: % HeldObject)] :tags {:context "doeff-cluster" :role "foundation"}}
  "宣言の :environ の物 1 つ丸ごとの参照の綴り spelled(「<種類>:<namespace>/<名>」)が指す物を、data の項目 entries で持つ物にするため
   (綴りでない字は綴りを挙げて断る)。"
  (val found (.match OBJECT-SPELLING spelled))
  (when (is found None)
    (raise (ValueError (.format "物の参照の綴りでない: {!r}" spelled))))
  (HeldObject :resource (get RESOURCE-OF-KIND (.group found 1)) :namespace (.group found 2) :name (.group found 3) :entries entries))


(defk read-grant-of [held]
  {:pre [(: held HeldObject)] :post [(: % ReadGrant)] :tags {:context "doeff-cluster" :role "foundation"}}
  "物 held 1 つに名を指した get の権限を作るため。"
  (ReadGrant :resource held.resource :namespace held.namespace :name held.name))


(defhandler service-account-token-file [#^ str token]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 申告する token は世界ごとの値(API server の相手役の世界の token と同じ字)で、Ask の設定ではない。
  ;; worker の ServiceAccount の token の file の読みにだけ答え、ほかの path の読みは外側の file の答え手へ通す(頭の註)。
  (ReadText [path]
    :when (= path SERVICE-ACCOUNT-TOKEN-FILE)
    (resume token)))


(defk api-answer [status text url]
  {:pre [(: status int) (: text str) (: url str)] :post [(: % HttpResponse)] :tags {:context "doeff-cluster" :role "foundation"}}
  "API server の相手役の答え(status と本文の字)を HTTP の応答にするため。"
  (HttpResponse status {} (.encode text "utf-8") text url 0.0))


(defk status-text [code reason message]
  {:pre [(: code int) (: reason str) (: message str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "API server の断りの本文(kind Status)を JSON の字へ綴るため(JSON の境界の 1 点 — 欄は API server の Status と同じ名)。"
  (json.dumps {"kind" "Status" "apiVersion" "v1" "status" "Failure" "reason" reason "message" message "code" code} :ensure-ascii False))


(defk object-text [held]
  {:pre [(: held HeldObject)] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "cluster が持つ物を API server の GET の答えの本文の JSON の字へ綴るため(JSON の境界の 1 点 — Secret の data は base64・空の data は欄ごと省く)。"
  (val data (dfor entry held.entries
                  entry.key (if (= held.resource "secrets")
                                (.decode (base64.b64encode (.encode entry.value "utf-8")) "ascii")
                                entry.value)))
  (json.dumps (| {"kind" (get KIND-OF-RESOURCE held.resource) "apiVersion" "v1" "metadata" {"name" held.name "namespace" held.namespace}}
                 (if data {"data" data} {}))
              :ensure-ascii False))


(defk answer-of [cluster presented namespace resource name url]
  {:pre [(: cluster SuppliedCluster) (: presented (| str None)) (: namespace str) (: resource str) (: name str) (: url str)]
   :post [(: % HttpResponse)] :tags {:context "doeff-cluster" :role "foundation"}}
  "名の決まった物 1 つの GET に、API server と同じ順(身元 → 権限 → 物が在るか)で答えるため(頭の註)。"
  (val held (lfor o cluster.objects :if (and (= o.resource resource) (= o.namespace namespace) (= o.name name)) o))
  (val granted (any (gfor g cluster.grants (and (= g.resource resource) (= g.namespace namespace) (or (is g.name None) (= g.name name))))))
  (cond
    (!= presented (+ "Bearer " cluster.token))
    (! (api-answer 401 (! (status-text 401 "Unauthorized" "Unauthorized")) url))
    (not granted)
    (! (api-answer 403 (! (status-text 403 "Forbidden"
                                           (.format "{} \"{}\" is forbidden: cannot get resource \"{}\" in the namespace \"{}\"" resource name resource namespace)))
                   url))
    (not held)
    (! (api-answer 404 (! (status-text 404 "NotFound" (.format "{} \"{}\" not found" resource name))) url))
    True
    (! (api-answer 200 (! (object-text (get held 0))) url))))


(defk headers-field [value]
  {:pre [(: value dict)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation"}}
  "HTTP の要求の見出しの欄(doeff の http_effects は Hy の module で型検査から見えない)を表として受けるため — 表でなければ :pre が落ちる。"
  value)


(defhandler k8s-supplied-peer [#^ SuppliedCluster cluster]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 外の世界(何が在り、worker の身元に何が読めるか・届くか)は筋書きごとに違う値で、Ask の設定ではなく外の世界そのものの形。
  ;; 名の決まった ConfigMap・Secret の GET だけに答え、ほかの HTTP の要求は外側へ通す(頭の註)。
  (HttpRequest [method url headers failures-as-values]
    :when (and (= method "GET")
               (isinstance url str) (.startswith url KUBE-API)
               (is-not (.match OBJECT-PATH (cut url (len KUBE-API) None)) None))
    (val target (! (text-field url)))
    (when cluster.down
      (if failures-as-values
          (resume (HttpFailed :url target :detail (+ "ConnectError: " UNREACHABLE) :kind HttpFailureKind.CONNECT-FAILED))
          (raise (ConnectError UNREACHABLE))))
    (val found (.match OBJECT-PATH (cut target (len KUBE-API) None)))
    ;; :when が path の形を確かめた後なので無い事は無いが、型の上で None を外す(形の違う path に黙って答えない)。
    (when (is found None)
      (raise (ValueError (+ "API server の相手役が答える形でない path: " target))))
    (val presented (.get (! (headers-field (or headers {}))) "authorization"))
    (resume (! (answer-of cluster presented (.group found 1) (.group found 2) (.group found 3) target)))))
