;;; worker の枠(task-reserve・background-room・memory の予算)を枠の file から読む判断(card ki-6828eda880ce)— I/O は呼び手
;;; (worker/protocol/coordinator_link と入口 worker/entry/main)が行う。
;;;
;;; 枠を起動の引数だけで受けると、値を変えるたびに worker の Pod の template が変わって作り直され、走っている job と task が切れる。
;;; 枠の file は名の固定した ConfigMap を volume で置いた file(subPath でない — 中身を変えると kubelet が Pod を作り直さずに入れ替える)で、
;;; worker は heartbeat の前に読み直して名乗る。coordinator は heartbeat ごとに Worker の行の taskReserve・backgroundRoom・memoryMiB を書き直す。
;;;
;;; file の形(JSON): worker の名 → {"taskReserve": 整数, "backgroundRoom": 整数, "memoryMiB": 整数}。名の無い行は読まない(他の worker の
;;; 行を置いてよい)。3 つの欄はどれも必須の 0 以上の整数(card ki-2acb63aec0fd):
;;;   taskReserve     task のために空けておく数
;;;   backgroundRoom  背景の仕事を同時に置ける数(task の空き = taskReserve + backgroundRoom − 置いた task — 常駐の job の数に依らない)
;;;   memoryMiB       その worker で常駐の job に使ってよい memory の予算(MiB)。coordinator は置いた job の settle の和 + 新しい job の settle が
;;;                   この値以下の時だけ job を置く(job は数でなく memory で縛る)
;;;   {"zeus": {"taskReserve": 6, "backgroundRoom": 4, "memoryMiB": 63488}, "atlas": {"taskReserve": 3, "backgroundRoom": 0, "memoryMiB": 16384}}
;;; 行に capacity(手書きの job と task の上限 — 消した欄)が在っても読まずに通す。上に載る系の pin がまだ capacity を読むので、file から
;;; capacity を消すのは使い手を入れ替えた後(card ki-2acb63aec0fd の step 4)。
;;; 行は省ける欄 warmServices(真偽 — card ki-67abc0cf71e9)も持てる: 実行環境の service も待ちの子から分けて起こすか(WorkerPolicy.warm-services)。
;;; 起動の時に 1 度だけ読む(枠と違って読み直さない)— 版の file の入れ替えで新しい世代が起きた時に効き、Pod の template を変えずに機体ごとに入れる。
;;; 行は省ける欄 pools(card ki-ced3c45980a5 — 枠と同じく heartbeat の前に読み直す)も持てる: 置ける組の印の列(pool-… —
;;; shared/core/capabilities の pool-refusal)。worker は起動の引数の能力(--provides)との和を heartbeat の provides で名乗る — 常駐の job を
;;; 2 台以上の機体の組の印で宣言でき、組を替えるのに Pod を作り直さない。
;;;   {"atlas": {"taskReserve": 3, "backgroundRoom": 0, "memoryMiB": 16384, "pools": ["pool-a", "pool-b"]}}
;;;
;;; 読めた状態(heartbeat の roomFile で名乗る):
;;;   ok          自分の行を読めた
;;;   unreadable  file を読めない(無い・権限)
;;;   malformed   file は読めたが、JSON でない・自分の行が無い・taskReserve・backgroundRoom・memoryMiB のどれかが無いか 0 以上の整数でない・
;;;               pools が列でないか組の印でない名を持つ
;;; ok でない時は前に名乗った値(初回は起動の引数 — 組の印は無し・memory の予算は --memory-mib の値)のままにする — 既定の値で埋めない。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import json)
(import doeff_cluster.shared.core.capabilities [HOST-PREFIX capabilities-of pool-refusal])

;; 枠の file を読み直す間。kubelet が ConfigMap の変更を Pod の file へ写すのに 1 分前後かかるので、拍ごとに読まなくても遅れは増えない。
(val READ-EVERY-MS (* 10 1000))

(defenum RoomState OK UNREADABLE MALFORMED)

;; 雛形の能力: worker の雛形(deploy/k8s/worker/worker.yaml の WORKER_PROVIDES — host-only の差も同じ)が、機体の印 host-<機体> の後ろに
;; どの機体の worker にも渡す能力。上に載る系の照らし(常駐の job が枠に入るか)は写さずにここから読む(card ki-5996b0cce581)。
(val TEMPLATE-PROVIDES #("host-systemd-readable"))


;; 枠の file の行の必須の欄(どれも 0 以上の整数 — room-of-text が読む順)。
(val ROOM-FIELDS #("taskReserve" "backgroundRoom" "memoryMiB"))


(defrecord WorkerRoom
  "worker の枠(card ki-2acb63aec0fd): task-reserve = task のために空けておく数・background-room = 背景の仕事を同時に置ける数・memory-mib =
   常駐の job に使ってよい memory の予算(MiB — None = 名乗らない。枠の file を読まない機体の worker(Mac・zeus-native)が --memory-mib を
   渡さない時だけ。coordinator はその worker を memory で縛らない)・pools = 置ける組の印(名の順 — heartbeat の provides に足す・
   card ki-ced3c45980a5)。"
  {:check [(>= task-reserve 0) (>= background-room 0) (or (is memory-mib None) (>= memory-mib 0))]}
  (#^ int task-reserve)
  (#^ int background-room)
  (#^ (| int None) memory-mib)
  (setv #^ (get tuple #(str ...)) pools #()))


(defrecord RoomReading
  "枠の file を 1 度読んだ答え: state = 読めた状態・room = 読めた枠(ok でなければ None)・reason = ok でない訳(ok なら空)。"
  (#^ RoomState state)
  (#^ (| WorkerRoom None) room)
  (#^ str reason))


(defk room-of-text [text name]
  {:pre [(: text str) (: name str)] :post [(: % RoomReading)] :tags {:context "worker" :role "judgment" :reads "json"}}
  "枠の file の中身から worker name の枠を読むため。JSON でない・name の行が無い・taskReserve・backgroundRoom・memoryMiB のどれかが無いか
   0 以上の整数でない(真偽は整数と読まない — ROOM-FIELDS)・pools の形の誤り(row-pools)は malformed とその訳。行の capacity は読まない。"
  ;; 解けた値を 1 つの組に包む(空の組 = JSON でない — 中身の null と分ける)。
  (val decoded (try #((json.loads text)) (except [json.JSONDecodeError] #())))
  (val parsed (if decoded (get decoded 0) None))
  (val row (if (isinstance parsed dict) (.get parsed name) None))
  (val fields (if (isinstance row dict) (tuple (gfor k ROOM-FIELDS (.get row k))) (tuple (gfor k ROOM-FIELDS None))))
  ;; 0 以上の整数でない最初の欄の名(None = 全部受けられる)。
  (val wrong (next (gfor #(k v) (zip ROOM-FIELDS fields) :if (not (and (isinstance v int) (not (isinstance v bool)) (>= v 0))) k) None))
  (<- pools (| tuple str) (row-pools (if (isinstance row dict) row {}) name))
  (val malformed (fn [reason] (RoomReading :state RoomState.MALFORMED :room None :reason reason)))
  (cond
    (not decoded) (malformed "JSON でない")
    (not (isinstance parsed dict)) (malformed "JSON の object でない")
    (is row None) (malformed (.format "worker {} の行が無い" name))
    (is-not wrong None)
      (malformed (.format "worker {} の行の {} が 0 以上の整数でない: {!r}" name wrong (.get row wrong)))
    (isinstance pools str) (malformed pools)
    True (RoomReading :state RoomState.OK
                      :room (WorkerRoom :task-reserve (get fields 0) :background-room (get fields 1) :memory-mib (get fields 2) :pools pools)
                      :reason "")))


(defk row-pools [row name]
  {:pre [(: row dict) (: name str)] :post [(: % (| (get tuple #(str ...)) str))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "枠の file の worker name の行から置ける組の印 pools を読むため(card ki-ced3c45980a5)。欄が無ければ空・在れば名の順の重なりの無い
   tuple。列でない値・文字列でない名・組の印として受けられない名(機体の印 host-・pool- で始まらない名 — 導く能力の名もここで落ちる)は
   malformed の訳の文(黙って外して残りを名乗らない — 1 台の縛りを枠の file から入れない)。"
  (val value (.get row "pools" []))
  (val names (if (isinstance value list) value []))
  ;; 最初の受けられない名の訳(None = 全部受けられる)。
  (var problem None)
  (for [n names]
    (when (is problem None)
      (if (isinstance n str)
          (do (<- refused (| str None) (pool-refusal n))
              (:= problem refused))
          (:= problem (.format "{!r} が文字列でない" n)))))
  (cond
    (not (isinstance value list)) (.format "worker {} の行の pools が列でない: {!r}" name value)
    (is-not problem None) (.format "worker {} の pools の {}" name problem)
    True (tuple (sorted (set names)))))


(defk launch-provides [node extra]
  {:pre [(: node str) (: extra (get tuple #(str ...)))] :post [(: % (get tuple #(str ...)))] :tags {:context "worker" :role "judgment"}}
  "雛形が機体 node の worker に起動の引数 --provides で渡す能力(host-<node>・雛形の能力 TEMPLATE-PROVIDES・env の WORKER_PROVIDES_EXTRA の
   名 extra — 空の名は飛ばす)を、起動の入口と同じ読み(capabilities-of — 名の順の重なりの無い tuple)で知るため。受けられない名は
   BodyInvalid(ValueError の子)。"
  (capabilities-of (lfor n (+ #((+ HOST-PREFIX node)) TEMPLATE-PROVIDES extra) :if n n) "--provides"))


(defk announced-provides [provides pools]
  {:pre [(: provides (get tuple #(str ...))) (: pools (get tuple #(str ...)))] :post [(: % (get tuple #(str ...)))]
   :tags {:context "worker" :role "judgment"}}
  "worker が heartbeat の provides で名乗る能力 = 起動の引数の能力 provides の後ろに、枠の file の行の組の印 pools のうちまだ無い物を
   足した列(card ki-ced3c45980a5)を知るため — heartbeat の本文と上に載る系の照らしが同じ和を使う(card ki-5996b0cce581)。"
  (+ provides (tuple (gfor p pools :if (not-in p provides) p))))


(defk room-warm-services-of [text name]
  {:pre [(: text str) (: name str)] :post [(: % (| bool None str))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "枠の file の中身から worker name の行の warmServices を読むため(card ki-67abc0cf71e9)。真偽ならその値・JSON でない・行が無い・欄が
   無いなら None(機体の配備が入れていない)・真偽でない値は起動を断る訳の文(黙って偽に倒れない)。"
  (val decoded (try #((json.loads text)) (except [json.JSONDecodeError] #())))
  (val parsed (if decoded (get decoded 0) None))
  (val row (if (isinstance parsed dict) (.get parsed name) None))
  (val value (if (isinstance row dict) (.get row "warmServices") None))
  (cond
    (is value None) None
    (isinstance value bool) value
    True (.format "枠の file の worker {} の warmServices は true か false で書く: {!r}" name value)))


(defk room-read-due [read-ms now-ms]
  {:pre [(: read-ms (| int None)) (: now-ms int)] :post [(: % bool)]}
  "この拍で枠の file を読み直すかを決めるため(read-ms = 前に読んだ刻・None = まだ読んでいない)。壁の時計が戻った時も読み直す。"
  (or (is read-ms None) (< now-ms read-ms) (>= (- now-ms read-ms) READ-EVERY-MS)))


(defk room-line [before after reading]
  {:pre [(: before WorkerRoom) (: after WorkerRoom) (: reading RoomReading)] :post [(: % str)]}
  "枠の file を読んだ結果の移り(値が変わった・読めなくなった・戻った)を、worker の log の 1 行にするため。before = 前に名乗った枠・
   after = 次に名乗る枠(読めなければ before と同じ)。組の印と memory の予算は名乗る時だけ添える。"
  (val said (fn [r] (+ (.format "task-reserve {}・background-room {}" r.task-reserve r.background-room)
                       (if r.pools (.format "・pools {}" (.join "," r.pools)) "")
                       (if (is r.memory-mib None) "" (.format "・memory {} MiB" r.memory-mib)))))
  (if (= reading.state RoomState.OK)
      (.format "worker: 枠の file から {} → {} を名乗る" (said before) (said after))
      (.format "worker: 枠の file を読めない({}: {})— 前の値 {} のまま名乗る" reading.state.value reading.reason (said before))))
