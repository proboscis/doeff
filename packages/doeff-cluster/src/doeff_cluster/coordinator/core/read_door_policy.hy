;;; 読みだけの口(coordinator の --read-port)で許す経路の表と、そこに届いた要求を断るかの判断(純粋・I/O はしない — #2742)。
;;;
;;; coordinator のどの経路にも認証は無く、GET でも状態を変える経路(GET /tasks/<id> は task の lease を延ばす)や、外へ出せない物を
;;; 返す経路(/programs/<sha> は詰めた Program の本体・/board は lease の token)が在る。だから「GET だけ通す」ではなく、許す経路を
;;; この表に 1 行ずつ載せ、表に無い要求は振り分け(api_policy.respond)の前に断る — 新しい経路が respond に足されても、この表に
;;; 足すまで読みの口には届かない(既定で断る)。
;;;
;;; 使い手: 各機体の land-arm(#2736)が、本番の記録の service の宣言の版(spec.revision)と動いている版
;;; (status.process.runningRevision)を tailnet 越しに読む(調べ = #2742 issuecomment-5945058649)。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff_cluster.shared.intent.protocol [Request RequestDoor])
(import doeff_cluster.coordinator.intent.cluster_model [ErrorReply])


;; 経路の区切りのうち、語でなく「空でない 1 区切りなら何でも」を表す印(資源の名など)。
(defenum RouteHole NAME)


(defrecord ReadRoute
  "読みだけの口で許す経路 1 つ: method と、path の区切りごとの語(RouteHole.NAME の区切りは空でない何でも)。"
  (#^ str method)
  (#^ tuple segments))


;; 読みだけの口で許す経路の表(ここに載せた物だけを通す)。
;; - GET /resources/Service/<名>: Service 1 つの宣言と状態(版の判定に要る spec.revision・status.process.runningRevision・
;;   status.version)。答えに token や鍵の値は無い(秘密は *_FILE / *_DIR の path で渡す決まり)。
(val READ-ROUTES
  #((ReadRoute :method "GET" :segments #("resources" "Service" RouteHole.NAME))))


(defk read-route-fits [route request]
  {:pre [(: route ReadRoute) (: request Request)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "許す経路の表の 1 行が、届いた要求に当たるかを判じるため(method が同じ・区切りの数が同じ・語の区切りは同じ語・名の区切りは空でない)。"
  (and (= request.method route.method)
       (= (len request.parts) (len route.segments))
       (all (gfor #(want got) (zip route.segments request.parts)
                  (if (is want RouteHole.NAME) (bool got) (= want got))))))


(defk read-door-refusal [request]
  {:pre [(: request Request)] :post [(: % (| ErrorReply None))] :tags {:context "coordinator" :role "judgment"}}
  "読みだけの口に届いた要求を、許す経路の表に当てて断るため: 表のどの行にも当たらなければ断りの本文、当たれば None。全部の経路の口
   (RequestDoor.MAIN)の要求は判じない(None)。"
  (when (!= request.door RequestDoor.READ)
    (return None))
  (for [route READ-ROUTES]
    (<- fits bool (read-route-fits route request))
    (when fits
      (return None)))
  (ErrorReply :message (.format "読みだけの口では {} {} を受けない(許す経路の表 = coordinator/core/read_door_policy.hy の READ-ROUTES)"
                                request.method request.path)))
