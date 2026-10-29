;;; job が自分へ届く網の宛先(k8s の Service)を、YAML でなく宣言(値と effect)で作る時の型と effect(関数は持たない — 本番の答え手は
;;; doeff_cluster.shared.protocol.kube_services の kube-services-api・模擬の答え手は doeff_cluster._sim.k8s_services の held-kube-services)。
;;; この package の service(名前付きで動き続ける Program)と区別するため、k8s の Service は名に Kube を付ける。
;;;
;;;   KubeServiceType      Service の種類: CLUSTER-IP = cluster の中の宛先・LOAD-BALANCER = load balancer の実装(class で名指す)が外へ出す宛先
;;;   KubeServicePort      口 1 つ: name・port(Service の口)・target-port(Pod の中で job が待ち受ける口 — worker の container は口に名を
;;;                        付けないので数で書く)
;;;   KubeService          Service 1 つの宣言: namespace・name・type・selector(Pod の label — 空にしない)・ports・labels・annotations・
;;;                        load-balancer-class(LOAD-BALANCER の時だけ書ける)
;;;   KubeServiceOutcome   当てた結末: CREATED = 無かったので作った・APPLIED = 在った物に宣言を当て直した(中身が同じで変わらない事も含む)
;;;   KubeServiceRefused   当てられなかった(namespace と名・理由を文に持つ — token は載せない・既定の値で埋めない)
;;;   EnsureKubeService    Service 1 つを宣言どおりに在らせる effect(答えは KubeServiceOutcome。当てられなければ KubeServiceRefused)
;;;
;;; 宣言が持つ欄だけを当てる(server-side apply): cluster が埋める欄(clusterIP・status・load balancer の実装が足す finalizer や annotation)と、
;;; 他の持ち主が足した欄には触らない。何度当てても同じ宣言なら同じ物になる(job が起動のたびに当て直してよい)。LOAD-BALANCER の Service に
;;; node の口(NodePort)は開けない(load balancer の実装が Pod へ直に繋ぐ — 宛先を node の外の口へ広げない)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import typing [ClassVar])
(import doeff [EffectBase])
(import doeff_hy._frozen [FrozenMap])
(import doeff_cluster.shared.intent.record_spec [RecordSpec RecordMode])

;; 値は k8s の spec.type の綴り。
(defenum KubeServiceType (CLUSTER-IP "ClusterIP") (LOAD-BALANCER "LoadBalancer"))

(defenum KubeServiceOutcome CREATED APPLIED)


(defrecord KubeServicePort
  "Service の口 1 つ: name = 口の名・port = Service の口・target-port = Pod の中で job が待ち受ける口(数で書く)。"
  {:check [(> (len name) 0) (<= 1 port 65535) (<= 1 target-port 65535)]}
  (#^ str name)
  (#^ int port)
  (#^ int target-port))


(defrecord KubeService
  "Service 1 つの宣言(頭の註): namespace・name・type・selector = 宛先の Pod の label(空にしない — 空の selector は namespace の全部の Pod を
   選ぶ)・ports = 口(1 つ以上・名は重ねない)・labels・annotations = Service 自身の metadata・load-balancer-class = LOAD-BALANCER の宛先を
   出す実装の名(None = cluster の既定の実装。CLUSTER-IP には書けない)。"
  {:check [(> (len namespace) 0) (> (len name) 0) (> (len selector) 0) (> (len ports) 0)
           (= (len (set (gfor port ports port.name))) (len ports))
           (or (is load-balancer-class None) (= type KubeServiceType.LOAD-BALANCER))]}
  (#^ str namespace)
  (#^ str name)
  (#^ KubeServiceType type)
  ;; selector・labels・annotations が写像である訳: k8s の label と annotation の鍵は書き手が決める字で、欄の名に決まらない(k8s の
  ;; metadata の写像そのもの)。
  (#^ (get FrozenMap str) selector)
  (#^ (get tuple #(KubeServicePort ...)) ports)
  (setv #^ (get FrozenMap str) labels (FrozenMap {}))
  (setv #^ (get FrozenMap str) annotations (FrozenMap {}))
  (setv #^ (| str None) load-balancer-class None))


(defclass KubeServiceRefused [RuntimeError]
  "Service を当てられなかった(namespace と名・理由を文に持つ — token は載せない・既定の値で埋めない)。")


(defclass [(dataclass :frozen True)] EnsureKubeService [(get EffectBase KubeServiceOutcome)]
  ;; 記録: 判断の外への報告(output — cluster の物を作る・当て直す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ KubeService service))
