;;; job が自分へ届く網の宛先(k8s の Service と Ingress)を宣言どおりに在らせる effect EnsureKubeService・EnsureKubeIngress の模擬の答え手
;;; (模擬の環境)。本番の答え手(doeff_cluster.shared.protocol.kube_services の kube-services-api — API server への server-side apply)と同じ
;;; 結末で答え、作った Service と Ingress の一覧を入れ物に残す(検と模擬の世界が、走らせた後に読む)。
;;;
;;; 契約(本番の server-side apply と同じ — tests/test_kube_services.hy が両方の答え手で同じ筋書きを回す):
;;;   * 同じ種類で同じ namespace と名の物が無ければ作り、CREATED で答える。在れば宣言で置き換え、APPLIED で答える(同じ宣言でも APPLIED)。
;;;   * 一覧は種類ごとに、namespace と名ごとに 1 つ(最後に当てた宣言)を、初めて作った順に持つ。
;;;   * 届かない API server は、種類と namespace と名・理由を挙げた KubeServiceRefused。
(require doeff-hy.macros [defhandler defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import doeff_cluster.shared.intent.kube_service_model [EnsureKubeIngress EnsureKubeService KubeIngress KubeService KubeServiceOutcome
                                                        KubeServiceRefused])


(defclass KubeServiceLog []
  "模擬の答え手 held-kube-services が作った物を残す入れ物 — 検と模擬の世界が、走らせた後に読むため。services・ingresses = namespace と名ごとに
   最後に当てた宣言(KubeService・KubeIngress)の組(初めて作った順)・down = API server に届かない世界か。当てるたびに組を作り直す(書き換える
   のは欄 services・ingresses の値だけ)。"
  (defn #^ None __init__ [self #^ bool [down False]]  ; defk にできない: class の作り手(Python が呼ぶ口)
    "空の入れ物を作るため(down = API server に届かない世界)。"
    (setv #^ (get tuple #(KubeService ...)) self.services #()
          #^ (get tuple #(KubeIngress ...)) self.ingresses #()
          #^ bool self.down down)))


(defk _held-after [held declaration]
  {:pre [(: held tuple) (: declaration (| KubeService KubeIngress))] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "foundation"}}
  "同じ種類の一覧 held に宣言 declaration を当てた後の一覧を得るため(頭の註の契約 — 同じ物 = namespace と名が同じ・在れば置き換え、無ければ
   末に足す)。"
  (val place #(declaration.namespace declaration.name))
  (if (any (gfor one held (= #(one.namespace one.name) place)))
      (tuple (gfor one held (if (= #(one.namespace one.name) place) declaration one)))
      (+ held #(declaration))))


(defk held-outcome [log service]
  {:pre [(: log KubeServiceLog) (: service KubeService)] :post [(: % KubeServiceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "Service の宣言 service を入れ物 log に当て、結末を得るため(頭の註の契約)。"
  (when log.down
    (raise (KubeServiceRefused (+ "Service " service.namespace "/" service.name " を当てられない — API server に届かない: 模擬の世界が down"))))
  (val before log.services)
  (setv log.services (! (_held-after before service)))
  (if (= (len log.services) (len before)) KubeServiceOutcome.APPLIED KubeServiceOutcome.CREATED))


(defk _held-ingress-outcome [log ingress]
  {:pre [(: log KubeServiceLog) (: ingress KubeIngress)] :post [(: % KubeServiceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "Ingress の宣言 ingress を入れ物 log に当て、結末を得るため(頭の註の契約)。"
  (when log.down
    (raise (KubeServiceRefused (+ "Ingress " ingress.namespace "/" ingress.name " を当てられない — API server に届かない: 模擬の世界が down"))))
  (val before log.ingresses)
  (setv log.ingresses (! (_held-after before ingress)))
  (if (= (len log.ingresses) (len before)) KubeServiceOutcome.APPLIED KubeServiceOutcome.CREATED))


;; 引数に残す理由: 入れ物は呼び手が作って走らせた後に読む記録の置き場(検と模擬の世界ごとに別の入れ物)。
(defhandler held-kube-services [#^ KubeServiceLog log]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 模擬: 宣言を入れ物に当てる(頭の註の契約)。
  (EnsureKubeService [service]
    (val outcome (! (held-outcome log service)))
    (resume outcome))
  (EnsureKubeIngress [ingress]
    (val outcome (! (_held-ingress-outcome log ingress)))
    (resume outcome)))
