;;; worker の枠(capacity・task-reserve)を枠の file から読む判断(card ki-6828eda880ce)— I/O は呼び手(worker/protocol/coordinator_link と
;;; 入口 worker/entry/main)が行う。
;;;
;;; 枠を起動の引数だけで受けると、値を変えるたびに worker の Pod の template が変わって作り直され、走っている job と task が切れる。
;;; 枠の file は名の固定した ConfigMap を volume で置いた file(subPath でない — 中身を変えると kubelet が Pod を作り直さずに入れ替える)で、
;;; worker は heartbeat の前に読み直して名乗る。coordinator は heartbeat ごとに Worker の行の capacity・taskReserve を書き直す。
;;;
;;; file の形(JSON): worker の名 → {"capacity": 整数, "taskReserve": 整数}。名の無い行は読まない(他の worker の行を置いてよい)。
;;;   {"zeus": {"capacity": 39, "taskReserve": 6}, "atlas": {"capacity": 12, "taskReserve": 3}}
;;; 範囲は coordinator の heartbeat の本文の型と同じ(0 ≤ taskReserve ≤ capacity)— 外れた値は名乗らない(断られて名乗り続けない)。
;;; 行は省ける欄 warmServices(真偽 — card ki-67abc0cf71e9)も持てる: 実行環境の service も待ちの子から分けて起こすか(WorkerPolicy.warm-services)。
;;; 起動の時に 1 度だけ読む(枠と違って読み直さない)— 版の file の入れ替えで新しい世代が起きた時に効き、Pod の template を変えずに機体ごとに入れる。
;;; 行は省ける欄を 2 つ持てる(card ki-ced3c45980a5 — 枠と同じく heartbeat の前に読み直す):
;;;   pools      置ける組の印の列(pool-… — shared/core/capabilities の pool-refusal)。worker は起動の引数の能力(--provides)との和を heartbeat の
;;;              provides で名乗る — 常駐の job を 2 台以上の機体の組の印で宣言でき、組を替えるのに Pod を作り直さない。
;;;   memoryMiB  その worker で常駐の job に使ってよい memory の予算(0 以上の整数・MiB)。heartbeat の memoryMiB で名乗るだけで、置き方の判断には
;;;              使わない(照らすのは宣言の側)。
;;;   {"atlas": {"capacity": 13, "taskReserve": 3, "pools": ["pool-a", "pool-b"], "memoryMiB": 16384}}
;;;
;;; 読めた状態(heartbeat の roomFile で名乗る):
;;;   ok          自分の行を読めた
;;;   unreadable  file を読めない(無い・権限)
;;;   malformed   file は読めたが、JSON でない・自分の行が無い・欄が整数でない・範囲の外・pools が列でないか組の印でない名を持つ・
;;;               memoryMiB が 0 以上の整数でない
;;; ok でない時は前に名乗った値(初回は起動の引数 — 組の印は無し・memory の予算は名乗らない)のままにする — 既定の値で埋めない。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import json)
(import doeff_cluster.shared.core.capabilities [pool-refusal])

;; 枠の file を読み直す間。kubelet が ConfigMap の変更を Pod の file へ写すのに 1 分前後かかるので、拍ごとに読まなくても遅れは増えない。
(val READ-EVERY-MS (* 10 1000))

(defenum RoomState OK UNREADABLE MALFORMED)


(defrecord WorkerRoom
  "worker の枠: capacity = 同時の job と task の上限・task-reserve = そのうち task のために空けておく数・pools = 置ける組の印(名の順 —
   heartbeat の provides に足す・card ki-ced3c45980a5)・memory-mib = 常駐の job に使ってよい memory の予算(MiB — None = 枠の file に
   書いていない。heartbeat の memoryMiB で名乗るだけ)。"
  (#^ int capacity)
  (#^ int task-reserve)
  (setv #^ (get tuple #(str ...)) pools #())
  (setv #^ (| int None) memory-mib None))


(defrecord RoomReading
  "枠の file を 1 度読んだ答え: state = 読めた状態・room = 読めた枠(ok でなければ None)・reason = ok でない訳(ok なら空)。"
  (#^ RoomState state)
  (#^ (| WorkerRoom None) room)
  (#^ str reason))


(defk room-of-text [text name]
  {:pre [(: text str) (: name str)] :post [(: % RoomReading)] :tags {:context "worker" :role "judgment" :reads "json"}}
  "枠の file の中身から worker name の枠を読むため。JSON でない・name の行が無い・欄が整数でない(真偽は整数と読まない)・範囲
   (0 ≤ taskReserve ≤ capacity)の外・pools と memoryMiB の形の誤り(row-pools・row-memory)は malformed とその訳。"
  ;; 解けた値を 1 つの組に包む(空の組 = JSON でない — 中身の null と分ける)。
  (val decoded (try #((json.loads text)) (except [json.JSONDecodeError] #())))
  (val parsed (if decoded (get decoded 0) None))
  (val row (if (isinstance parsed dict) (.get parsed name) None))
  (val fields (if (isinstance row dict) #((.get row "capacity") (.get row "taskReserve")) #(None None)))
  (val whole (all (gfor v fields (and (isinstance v int) (not (isinstance v bool))))))
  (<- pools (| tuple str) (row-pools (if (isinstance row dict) row {}) name))
  (<- memory (| int str None) (row-memory (if (isinstance row dict) row {}) name))
  (val malformed (fn [reason] (RoomReading :state RoomState.MALFORMED :room None :reason reason)))
  (cond
    (not decoded) (malformed "JSON でない")
    (not (isinstance parsed dict)) (malformed "JSON の object でない")
    (is row None) (malformed (.format "worker {} の行が無い" name))
    (not whole) (malformed (.format "worker {} の行の capacity・taskReserve が整数でない" name))
    (not (<= 0 (get fields 1) (get fields 0)))
      (malformed (.format "worker {} の taskReserve {} が 0 以上 capacity {} 以下でない" name (get fields 1) (get fields 0)))
    (isinstance pools str) (malformed pools)
    (isinstance memory str) (malformed memory)
    True (RoomReading :state RoomState.OK
                      :room (WorkerRoom :capacity (get fields 0) :task-reserve (get fields 1) :pools pools :memory-mib memory)
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


(defk row-memory [row name]
  {:pre [(: row dict) (: name str)] :post [(: % (| int str None))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "枠の file の worker name の行から常駐の job の memory の予算 memoryMiB を読むため(card ki-ced3c45980a5)。欄が無ければ None・0 以上の
   整数ならその値・それ以外(真偽・null・文字・負の数)は malformed の訳の文。"
  (val value (.get row "memoryMiB"))
  (cond
    (not-in "memoryMiB" row) None
    (and (isinstance value int) (not (isinstance value bool)) (>= value 0)) value
    True (.format "worker {} の memoryMiB {!r} が 0 以上の整数でない" name value)))


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
  (val said (fn [r] (+ (.format "capacity {}・task-reserve {}" r.capacity r.task-reserve)
                       (if r.pools (.format "・pools {}" (.join "," r.pools)) "")
                       (if (is r.memory-mib None) "" (.format "・memory {} MiB" r.memory-mib)))))
  (if (= reading.state RoomState.OK)
      (.format "worker: 枠の file から {} → {} を名乗る" (said before) (said after))
      (.format "worker: 枠の file を読めない({}: {})— 前の値 {} のまま名乗る" reading.state.value reading.reason (said before))))
