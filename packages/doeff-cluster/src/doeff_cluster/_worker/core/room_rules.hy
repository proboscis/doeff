;;; worker の枠(常駐の job の memory の予算・置ける組の印)を枠の file から読む判断— I/O は呼び手
;;; (worker/protocol/coordinator_link と入口 worker/entry/main)が行う。
;;;
;;; 枠を起動の引数だけで受けると、値を変えるたびに worker の Pod の template が変わって作り直され、走っている job と task が切れる。
;;; 枠の file は名の固定した ConfigMap を volume で置いた file(subPath でない — 中身を変えると kubelet が Pod を作り直さずに入れ替える)で、
;;; worker は heartbeat の前に読み直して名乗る。coordinator は heartbeat ごとに Worker の行の memoryMiB を書き直す。
;;;
;;; file の形(JSON): worker の名 → {"memoryMiB": 整数}。名の無い行は読まない(他の worker の行を置いてよい)。必須の欄は 1 つ:
;;;   memoryMiB       その worker で常駐の job に使ってよい memory の予算(0 以上の整数の MiB)。coordinator は置いた job の settle の和 +
;;;                   新しい job の settle がこの値以下の時だけ job を置く(job は数でなく memory で縛る)
;;;   {"node-h": {"memoryMiB": 63488}, "node-a": {"memoryMiB": 16384}}
;;; task の置き先は手で書いた数でなく、worker が heartbeat で名乗る機体の測った空き(measured — worker/core/load_rules)から coordinator が
;;; 数える。行に手書きの task の枠(capacity・taskReserve・backgroundRoom — 消した欄)が在っても読まずに通す(旧い版の file を読む間)。
;;; 行は省ける欄 warmServices(真偽)も持てる: 実行環境の service も待ちの子から分けて起こすか(WorkerPolicy.warm-services)。
;;; 起動の時に 1 度だけ読む(枠と違って読み直さない)— 版の file の入れ替えで新しい世代が起きた時に効き、Pod の template を変えずに機体ごとに入れる。
;;; 行は省ける欄 pools(枠と同じく heartbeat の前に読み直す)も持てる: 置ける組の印の列(pool-… —
;;; shared/core/capabilities の pool-refusal)。worker は起動の引数の能力(--provides)との和を heartbeat の provides で名乗る — 常駐の job を
;;; 2 台以上の機体の組の印で宣言でき、組を替えるのに Pod を作り直さない。
;;;   {"node-a": {"memoryMiB": 16384, "pools": ["pool-a", "pool-b"]}}
;;; 行は省ける欄 keepLineFile(枠と同じく heartbeat の前に読み直す)も持てる: 会話のターンに残す MemAvailable の線の file の絶対 path
;;; (worker/core/load_rules の頭の註の「会話のターンに残す線」— 上に載る系のターンを受ける係が書く)。worker は測りのたびにその file を
;;; 読み、名乗る memory の空きを線の上に絞る。欄が無い行は線を読まない。
;;;   {"node-h": {"memoryMiB": 63488, "keepLineFile": "/cargo-build/.keep-available-mib"}}
;;; 行は省ける欄の組 commandLineFile と commandFloorMiB(枠と同じく heartbeat の前に読み直す)も持てる: 命令の層(commands)の memory.max を
;;; 掛け直す線の file の絶対 path と短い命令の余地(MiB — worker/core/eviction_rules の頭の註の命令の層)。線の file は上に載る系のターンを受ける
;;; 係が書く。2 つは組で書く(片方だけは malformed)。欄が無い行は命令の層を掛け直さない。
;;;   {"node-h": {"memoryMiB": 63488, "commandLineFile": "/cargo-build/.command-keep-available-mib", "commandFloorMiB": 8192}}
;;; 行は省ける欄 pinnedToNode(真偽 — 枠と同じく heartbeat の前に読み直す)も持てる: この worker の Pod は自分の node の外で動けない
;;; (起動の引数 --pinned-to-node と同じ意味 — どちらかが真なら縛られている。heartbeat_rules.held-keep-fence-ms)。Pod の env で渡すと値を
;;; 変えるたびに Pod が作り直されるので、枠の file でも受ける(2026-11-10)。欄が無い行は偽。
;;;   {"node-h": {"memoryMiB": 63488, "pinnedToNode": true}}
;;;
;;; 読めた状態(heartbeat の roomFile で名乗る):
;;;   ok          自分の行を読めた
;;;   unreadable  file を読めない(無い・権限)
;;;   malformed   file は読めたが、JSON でない・自分の行が無い・memoryMiB が無いか 0 以上の整数でない・
;;;               pools が列でないか組の印でない名を持つ
;;; ok でない時は前に名乗った値(初回は起動の引数 — 組の印は無し・memory の予算は --memory-mib の値)のままにする — 既定の値で埋めない。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import json)
(import doeff_cluster._shared.core.capabilities [HOST-PREFIX capabilities-of pool-refusal])

;; 枠の file を読み直す間。kubelet が ConfigMap の変更を Pod の file へ写すのに 1 分前後かかるので、拍ごとに読まなくても遅れは増えない。
(val READ-EVERY-MS (* 10 1000))

(defenum RoomState OK UNREADABLE MALFORMED)

;; 雛形の能力: worker の雛形(上に載る系の宣言の worker/worker.yaml の WORKER_PROVIDES — host-only の差も同じ)が、機体の印 host-<機体> の後ろに
;; どの機体の worker にも渡す能力。上に載る系の照らし(常駐の job が枠に入るか)は写さずにここから読む。
(val TEMPLATE-PROVIDES #("host-systemd-readable"))


;; 枠の file の行の必須の欄(どれも 0 以上の整数 — room-of-text が読む順)。
(val ROOM-FIELDS #("memoryMiB"))


(defrecord WorkerRoom
  "worker の枠: memory-mib = 常駐の job に使ってよい memory の予算(MiB — None = 名乗らない。枠の file を読まない機体の worker
   (Mac・node-h-native)が --memory-mib を渡さない時だけ。coordinator はその worker を memory で縛らない)・pools = 置ける組の印(名の順 —
   heartbeat の provides に足す)・keep-line-file = 会話のターンに残す線の file(頭の註の keepLineFile — 空 = 読まない)・command-line-file =
   命令の層を掛け直す線の file・command-floor-mib = 短い命令の余地(頭の註の commandLineFile と commandFloorMiB — 空と 0 = 掛け直さない)・
   pinned-to-node = この worker の Pod は自分の node の外で動けない(頭の註の pinnedToNode — 偽 = 起動の引数の値のまま)。"
  {:check [(or (is memory-mib None) (>= memory-mib 0)) (or (= keep-line-file "") (.startswith keep-line-file "/"))
           (or (= command-line-file "") (.startswith command-line-file "/")) (>= command-floor-mib 0)]}
  (#^ (| int None) memory-mib)
  (setv #^ (get tuple #(str ...)) pools #())
  (setv #^ str keep-line-file "")
  (setv #^ str command-line-file "")
  (setv #^ int command-floor-mib 0)
  (setv #^ bool pinned-to-node False))


(defrecord RoomReading
  "枠の file を 1 度読んだ答え: state = 読めた状態・room = 読めた枠(ok でなければ None)・reason = ok でない訳(ok なら空)。"
  (#^ RoomState state)
  (#^ (| WorkerRoom None) room)
  (#^ str reason))


(defk room-of-text [text name]
  {:pre [(: text str) (: name str)] :post [(: % RoomReading)] :tags {:context "worker" :role "judgment" :reads "json"}}
  "枠の file の中身から worker name の枠を読むため。JSON でない・name の行が無い・memoryMiB が無いか 0 以上の整数でない(真偽は整数と
   読まない — ROOM-FIELDS)・pools の形の誤り(row-pools)・keepLineFile の形の誤り(row-keep-line-file)は malformed とその訳。行の
   手書きの task の枠(capacity・taskReserve・backgroundRoom)は読まない。"
  ;; 解けた値を 1 つの組に包む(空の組 = JSON でない — 中身の null と分ける)。
  (val decoded (try #((json.loads text)) (except [json.JSONDecodeError] #())))
  (val parsed (if decoded (get decoded 0) None))
  (val row (if (isinstance parsed dict) (.get parsed name) None))
  (val fields (if (isinstance row dict) (tuple (gfor k ROOM-FIELDS (.get row k))) (tuple (gfor k ROOM-FIELDS None))))
  ;; 0 以上の整数でない最初の欄の名(None = 全部受けられる)。
  (val wrong (next (gfor #(k v) (zip ROOM-FIELDS fields) :if (not (and (isinstance v int) (not (isinstance v bool)) (>= v 0))) k) None))
  (<- pools (| tuple str) (row-pools (if (isinstance row dict) row {}) name))
  (<- keep-line (| tuple str) (row-keep-line-file (if (isinstance row dict) row {}) name))
  (<- command-line (| tuple str) (row-command-line (if (isinstance row dict) row {}) name))
  (val pinned (if (isinstance row dict) (.get row "pinnedToNode" False) False))
  (val malformed (fn [reason] (RoomReading :state RoomState.MALFORMED :room None :reason reason)))
  (cond
    (not decoded) (malformed "JSON でない")
    (not (isinstance parsed dict)) (malformed "JSON の object でない")
    (is row None) (malformed (.format "worker {} の行が無い" name))
    (is-not wrong None)
      (malformed (.format "worker {} の行の {} が 0 以上の整数でない: {!r}" name wrong (.get row wrong)))
    (isinstance pools str) (malformed pools)
    (isinstance keep-line str) (malformed keep-line)
    (isinstance command-line str) (malformed command-line)
    (not (isinstance pinned bool)) (malformed (.format "worker {} の行の pinnedToNode は真偽で書く: {!r}" name pinned))
    True (RoomReading :state RoomState.OK
                      :room (WorkerRoom :memory-mib (get fields 0) :pools pools :keep-line-file (get keep-line 0)
                                        :command-line-file (get command-line 0) :command-floor-mib (get command-line 1)
                                        :pinned-to-node pinned)
                      :reason "")))


(defk row-command-line [row name]
  {:pre [(: row dict) (: name str)] :post [(: % (| (get tuple #(str int)) str))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "枠の file の worker name の行から命令の層を掛け直す線の file と床(頭の註の commandLineFile と commandFloorMiB)を読むため。答え = 2 つ組
   (欄が無ければ空の path と 0)か malformed の訳の文(片方だけ・/ で始まらない path・0 以上の整数でない床 — 黙って掛け直さない側へ倒れない)。"
  (val path (.get row "commandLineFile" ""))
  (val floor (.get row "commandFloorMiB" None))
  (cond
    (and (= path "") (is floor None)) #("" 0)
    (or (= path "") (is floor None))
      (.format "worker {} の行の commandLineFile と commandFloorMiB は組で書く" name)
    (not (and (isinstance path str) (.startswith path "/")))
      (.format "worker {} の行の commandLineFile は / で始まる file の path で書く: {!r}" name path)
    (not (and (isinstance floor int) (not (isinstance floor bool)) (>= floor 0)))
      (.format "worker {} の行の commandFloorMiB が 0 以上の整数でない: {!r}" name floor)
    True #(path floor)))


(defk row-keep-line-file [row name]
  {:pre [(: row dict) (: name str)] :post [(: % (| (get tuple #(str)) str))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "枠の file の worker name の行から会話のターンに残す線の file(頭の註の keepLineFile)を読むため。答え = 1 つ組(欄が無ければ空の
   path)か malformed の訳の文(文字列でない・/ で始まらない値 — 黙って線を読まない側へ倒れない)。"
  (val value (.get row "keepLineFile" ""))
  (if (and (isinstance value str) (or (= value "") (.startswith value "/")))
      #(value)
      (.format "worker {} の行の keepLineFile は / で始まる file の path で書く: {!r}" name value)))


(defk row-pools [row name]
  {:pre [(: row dict) (: name str)] :post [(: % (| (get tuple #(str ...)) str))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "枠の file の worker name の行から置ける組の印 pools を読むため。欄が無ければ空・在れば名の順の重なりの無い
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
   足した列を知るため — heartbeat の本文と上に載る系の照らしが同じ和を使う。"
  (+ provides (tuple (gfor p pools :if (not-in p provides) p))))


(defk room-warm-services-of [text name]
  {:pre [(: text str) (: name str)] :post [(: % (| bool None str))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "枠の file の中身から worker name の行の warmServices を読むため。真偽ならその値・JSON でない・行が無い・欄が
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
   after = 次に名乗る枠(読めなければ before と同じ)。組の印は名乗る時だけ添える。"
  (val said (fn [r] (+ (if (is r.memory-mib None) "memory の予算なし" (.format "memory {} MiB" r.memory-mib))
                       (if r.pools (.format "・pools {}" (.join "," r.pools)) "")
                       (if r.keep-line-file (.format "・ターンに残す線 {}" r.keep-line-file) "")
                       (if r.command-line-file (.format "・命令の層の線 {}・床 {} MiB" r.command-line-file r.command-floor-mib) ""))))
  (if (= reading.state RoomState.OK)
      (.format "worker: 枠の file から {} → {} を名乗る" (said before) (said after))
      (.format "worker: 枠の file を読めない({}: {})— 前の値 {} のまま名乗る" reading.state.value reading.reason (said before))))
