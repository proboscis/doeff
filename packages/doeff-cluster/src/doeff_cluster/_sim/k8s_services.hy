;;; job が自分へ届く網の宛先(k8s の Service)を宣言どおりに在らせる effect EnsureKubeService の模擬の答え手(模擬の環境)。本番の答え手
;;; (doeff_cluster.shared.protocol.kube_services の kube-services-api — API server への server-side apply)と同じ結末で答え、作った Service の
;;; 一覧を入れ物に残す(検と模擬の世界が、走らせた後に読む)。
;;;
;;; 契約(本番の server-side apply と同じ — tests/test_kube_services.hy が両方の答え手で同じ筋書きを回す):
;;;   * 同じ namespace と名の Service が無ければ作り、CREATED で答える。在れば宣言で置き換え、APPLIED で答える(同じ宣言でも APPLIED)。
;;;   * 一覧は namespace と名ごとに 1 つ(最後に当てた宣言)を、初めて作った順に持つ。
;;;   * 届かない API server は、namespace と名・理由を挙げた KubeServiceRefused。
(require doeff-hy.macros [defhandler defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import doeff_cluster.shared.intent.kube_service_model [EnsureKubeService KubeService KubeServiceOutcome KubeServiceRefused])


(defclass KubeServiceLog []
  "模擬の答え手 held-kube-services が作った Service を残す入れ物 — 検と模擬の世界が、走らせた後に読むため。services = namespace と名ごとに
   最後に当てた宣言(KubeService)の組(初めて作った順)・down = API server に届かない世界か。当てるたびに組を作り直す(書き換えるのは欄
   services の値だけ)。"
  (defn #^ None __init__ [self #^ bool [down False]]  ; defk にできない: class の作り手(Python が呼ぶ口)
    "空の入れ物を作るため(down = API server に届かない世界)。"
    (setv #^ (get tuple #(KubeService ...)) self.services #()
          #^ bool self.down down)))


(defk held-outcome [log service]
  {:pre [(: log KubeServiceLog) (: service KubeService)] :post [(: % KubeServiceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "宣言 service を入れ物 log に当て、結末を得るため(頭の註の契約 — 同じ Service = namespace と名が同じ)。"
  (when log.down
    (raise (KubeServiceRefused (+ "Service " service.namespace "/" service.name " を当てられない — API server に届かない: 模擬の世界が down"))))
  (val place #(service.namespace service.name))
  (val existed (any (gfor held log.services (= #(held.namespace held.name) place))))
  (setv log.services (if existed
                         (tuple (gfor held log.services (if (= #(held.namespace held.name) place) service held)))
                         (+ log.services #(service))))
  (if existed KubeServiceOutcome.APPLIED KubeServiceOutcome.CREATED))


;; 引数に残す理由: 入れ物は呼び手が作って走らせた後に読む記録の置き場(検と模擬の世界ごとに別の入れ物)。
(defhandler held-kube-services [#^ KubeServiceLog log]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 模擬: 宣言を入れ物に当てる(頭の註の契約)。
  (EnsureKubeService [service]
    (val outcome (! (held-outcome log service)))
    (resume outcome)))
