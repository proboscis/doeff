;;; coordinator・worker・記録の置き場が取り交わす形 — 本文の版・拍の時間・HTTP の要求と返事の effect・送り手の誤り(cluster_model から移した・#2023)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] ClusterTiming []
  (setv #^ int lease-ms 10000)          ; これより新しい heartbeat の worker にだけ新しく割り当てる
  ;; fence は tailnet の実測の途絶(最長 約 13 秒・2026-09-23 newmac)より長く、移し替えは fence より十分長く取る
  ;; (止めた worker と新しい担い手が同時に動かない)。代償は障害時の移し替えが 60 秒になること。
  ;; worker は heartbeat の返事の timing から fence を受け取る(この値が唯一の定義点)。
  ;; worker が連絡の途絶から lease を持たない job と task を止めるまで。書き手(入れ替えを宣言した job)は止めない — 書きは lease の
  ;; 柵だけが守る(worker_policy.kept-when-cut-off・2026-09-25)。
  (setv #^ int fence-ms 20000)
  ;; 連絡の途絶えた worker の job を他へ移すまで。条 C4 timing-outlasts-the-self-stop(architecture.hy・#2806): 移し替えは、worker が
  ;; 自分で止まり切るまで(fence + heartbeat の返事の上限 + 接続の上限 + 子の停止の猶予 = 20 + 15 + 2 + 15 = 52 秒)より後。45 秒では
  ;; 足りず(戻った worker が返事を待つ間に移し替えが来る)、60 秒にした(余白 8 秒 — 返事の上限を詰める案は /watch の上限を詰める版を
  ;; またぐ変更と、coordinator の fsync の詰まりの間の heartbeat の落ちを招くので採らない・cisco-c8 の決め 2026-10-02)。
  (setv #^ int reassign-after-ms 60000)
  ;; 能力と版の合う worker が登録されているが live でない間、待っている task を失敗にせず待つ上限(その worker の最後の連絡から数える —
  ;; task の lease とは別)。worker の Recreate の入れ替え(古い Pod の drain の最長 約 4 時間 + 新しい Pod の名乗り)を覆う 5 時間
  ;; (#2753 — 切り離した task の lease は積んだ時の 60 秒で、入れ替えの間に過ぎて「合う worker が無い」で落ちた)。
  (setv #^ int silent-worker-wait-ms (* 5 3600 1000))
  ;; 途絶しても動かし続けてよい印(#2804)の在る job を、worker が途絶の後も動かし続ける上限(長い方の柵)。印の在る job は coordinator が
  ;; 他の worker へ移さないので fence では止めないが、同じ名の worker の新しい世代(k8s が届かない node の Pod を追い出して作り直した物)
  ;; とは重なりうるので、それより先に止める。前提の数: 本番の worker の Deployment は strategy = Recreate で not-ready / unreachable の
  ;; tolerations を manifest に足していない → admission の既定 tolerationSeconds 300 秒 + k8s v1.32 の node-monitor-grace-period の既定
  ;; 50 秒 → 届かない node の Pod の新しい世代が来るのは早くても約 350 秒後(2026-10-02 14:24 の実測 約 6.6 分)。keep-fence-ms 240 秒 +
  ;; 停止の猶予 15 秒 = 255 秒 < 350 秒なので、分断の最中に新しい世代が来ても古い process とは重ならない。tolerations を短くする manifest の
  ;; 変更はこの前提を崩す。worker は heartbeat の返事の timing から受け取る(欄の無い返事 = 古い coordinator は印も付けない)。
  (setv #^ int keep-fence-ms 240000)

  (defn #^ None __post-init__ [self]
    (when (<= self.keep-fence-ms self.fence-ms)
      (raise (ValueError "印の在る job の長い方の柵(keep-fence-ms)は fence より長くなければならない")))
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


;; 版の変化を待つ読み(GET /watch)の待ちの上限(秒)— worker が問いの timeoutSeconds に載せ、coordinator が頭打ちにする取り交わしの値。
;; 本番の受付の thread は返事を 30 秒まで待ち、worker の HTTP の client は 15 秒で打ち切る(coordinator_http の REPLY-SECONDS)ので、
;; その両方より拍 1 つ分以上短くする(coordinator/intent/cluster_model から移した・#2025)。
(val WATCH-MAX-SECONDS 10.0)


(defclass [(dataclass :frozen True)] NextRequests [EffectBase]
  "受付に並んだ要求をまとめて取る(coordinator と record-store の受け口の effect)。結果は Request の list。最初の 1 件を
   timeout-seconds まで待ち(来なければ空 = 期限の経過で次の歩へ進む・None = 期限なし — 要求か停止の合図か外の出来事でだけ起きる)、
   その時点で並んでいる要求を limit 件まで一緒に取る(group commit の 1 まとまり)。coordinator の調停ループは、次の期限までの秒を
   渡す(coordinator/core/wake_policy.wait-seconds・#3865)。"
  (#^ (| float None) timeout-seconds)
  (setv #^ int limit 256))


(defclass [(dataclass :frozen True)] Reply [EffectBase]
  (#^ Request request)
  (#^ int status)
  (#^ object body))


(defclass [(dataclass :frozen True)] CoordinatorStopRequested [EffectBase]
  "結果は bool。")
