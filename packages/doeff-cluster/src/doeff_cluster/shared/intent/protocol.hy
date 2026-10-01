;;; coordinator・worker・記録の置き場が取り交わす形 — 本文の版・拍の時間・HTTP の要求と返事の effect・送り手の誤り(cluster_model から移した・#2023)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] ClusterTiming []
  (setv #^ int lease-ms 10000)          ; これより新しい heartbeat の worker にだけ新しく割り当てる
  ;; fence は tailnet の実測の途絶(最長 約 13 秒・2026-09-23 newmac)より長く、移し替えは fence より十分長く取る
  ;; (止めた worker と新しい担い手が同時に動かない)。代償は障害時の移し替えが 45 秒になること。
  ;; worker は heartbeat の返事の timing から fence を受け取る(この値が唯一の定義点)。
  ;; worker が連絡の途絶から lease を持たない job と task を止めるまで。書き手(入れ替えを宣言した job)は止めない — 書きは lease の
  ;; 柵だけが守る(worker_policy.kept-when-cut-off・2026-09-25)。
  (setv #^ int fence-ms 20000)
  (setv #^ int reassign-after-ms 45000) ; 連絡の途絶えた worker の job を他へ移すまで

  (defn #^ None __post-init__ [self]
    (when (<= self.reassign-after-ms self.fence-ms)
      (raise (ValueError "移し替えは worker の自己停止より後でなければならない")))))


;; HTTP の本文(/tasks・/detached・/heartbeat)の形の版(2026-09-26)。送り手・coordinator・worker は別々の版になり得るので、本文に
;; format を置き、coordinator は受け入れる範囲を heartbeat の返事と /livez で名乗り、範囲の外の送り手を 400 で断る。format の無い
;; 本文(この版より前の送り手)は 1 として受ける。
(setv PROTOCOL-FORMAT 1)


;; --- HTTP の要求と返事 ----------------------------------------------------------

(defclass [(dataclass :frozen True :eq False)] Request []
  "受けた HTTP 要求 1 件。slot は返事を待つ handler の側の物(判断は見ない)。
   actor = 送り手(header X-Actor)。無ければ None(資源の書きは断る・盤と task は送り元の番地で記録する)。
   path = 受けたままの path(log と返事の文に使う)・parts = path を / で割り、区切りごとに percent の符号を戻した物。
   符号を戻すのは HTTP の境(coordinator_inbox.http-request)の仕事で、判断(api_policy.respond)は parts だけを読む(#1636)。"
  (#^ str method)
  (#^ str path)
  (#^ dict query)
  (#^ object body)
  (#^ tuple parts)
  (setv #^ object slot None)
  (setv #^ (| str None) actor None)
  (setv #^ str peer ""))


(defclass BodyInvalid [ValueError]
  "送り手の要求の本文の誤り(欠けた欄・受けられない値・旧い形)。受け口(api_policy.respond)はこれと resource_policy.Refused だけを
   400 にし、それ以外の例外は coordinator の中の欠陥(Fault・500 と log の 1 行)にする(#1024 — #1005 では中の
   TypeError が 400 に畳まれ、log にも出ずに原因の特定が遅れた)。ValueError の子なので、同じ検めを保存の行や起動の引数で呼ぶ所の
   except ValueError はそのまま受ける。")


(defclass [(dataclass :frozen True)] PlainText []
  "JSON でない返事の本文(GET /metrics の Prometheus の text)。HTTP の handler は content-type をそのまま付けて text を返す。"
  (#^ str text)
  (setv #^ str content-type "text/plain; version=0.0.4; charset=utf-8"))


(defclass [(dataclass :frozen True)] NextRequests [EffectBase]
  "受付に並んだ要求をまとめて取る(coordinator と record-store の受け口の effect)。結果は Request の list。最初の 1 件を
   timeout-seconds まで待ち(来なければ空 = 期限の経過で次の拍へ進む)、その時点で並んでいる要求を limit 件まで一緒に取る
   (group commit の 1 まとまり)。coordinator の調停ループは、模擬の時計の下の受け口だけが読む材料を足した子 class
   doeff_cluster.coordinator.intent.cluster_model.IdleNextRequests を出す(本番の受け口はこの class として受ける・agora-redesign #2180)。"
  (#^ float timeout-seconds)
  (setv #^ int limit 256))


(defclass [(dataclass :frozen True)] Reply [EffectBase]
  (#^ Request request)
  (#^ int status)
  (#^ object body))


(defclass [(dataclass :frozen True)] CoordinatorStopRequested [EffectBase]
  "結果は bool。")
