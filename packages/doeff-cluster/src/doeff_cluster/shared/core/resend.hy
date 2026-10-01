;;; 何度送っても同じ意味の要求を送り直す期限 — worker の自己停止(ClusterTiming の fence)から導く値。前は foundation/coordinator_http に
;;; 在ったが、層 foundation は intent(ClusterTiming)を読めないので、intent を読んでよい core に置いた(#2566)。送り直しの間
;;; RESEND-PAUSE-SECONDS は intent に依らないので foundation/coordinator_http のまま。値は組み立てる側が RouteOptions の
;;; resend-deadline-seconds に渡す(#2565)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff_cluster.shared.intent.protocol [ClusterTiming])

;; 何度でも送ってよい要求(読み)を、途中で切れても送り直す時間の上限(秒)。worker の自己停止(fence — ClusterTiming・20 秒)より
;; 5 秒長くする: fence より短い途絶は service も worker も越え、それより長い途絶では worker の方が job を止める(読みを先に諦めて
;; service が自分で落ちることはない)。
(val IDEMPOTENT-DEADLINE-SECONDS (+ (/ (. (ClusterTiming) fence-ms) 1000) 5.0))
