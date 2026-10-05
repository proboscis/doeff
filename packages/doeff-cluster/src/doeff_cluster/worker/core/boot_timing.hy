;;; worker の起動の内訳の計時(#3676)— Pod の起動 → boot.sh の始まり → worker の exec → import の終わり → 最初の heartbeat の答え の
;;; 5 つの刻を、最初の heartbeat の答えの後に 1 行で出すための判断と読み。
;;;
;;; 刻の出どころ:
;;;   Pod の起動         PID 1 の process の始まり(ProcessStartedMs "1" — 答え手 worker/protocol/process_clock が /proc/1/stat を読む)
;;;   boot.sh の始まり   boot.sh が最初に置く環境変数 BOOT-STARTED-VAR(起動の script の引き継ぎの exec をまたいで同じ値)
;;;   exec              boot.sh が worker を exec する直前に置く環境変数 BOOT-EXEC-VAR
;;;   import の終わり    worker の入口 main の頭の刻(呼び手が渡す)
;;;   最初の答え          coordinator への口が最初の heartbeat の返事を読み終えた刻(呼び手が渡す)
;;; OS の process の始まり(ProcessStartedMs "self")は exec で変わらない(boot.sh から exec した worker では boot.sh の process の始まり)
;;; ので、行には添えるが順の断言には入れない。取れない刻は None で、順の断言から外す。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_cluster.worker.intent.worker_model [BootMarks ProcessStartedMs])

(val MODULE-TAGS {:context "worker" :role "judgment"})

;; boot.sh が worker へ渡す刻の環境変数の名(epoch ミリ秒 — deploy/boot.sh と同じ名)。
(val BOOT-STARTED-VAR "DOEFF_BOOT_STARTED_MS")
(val BOOT-EXEC-VAR "DOEFF_BOOT_EXEC_MS")


(defrecord BootStamp
  "起動の刻 1 つ: name = 刻の名(pod・script・exec・imported・answered)・ms = epoch ミリ秒か None(取れない)。"
  {:tags {:context "worker" :role "type"}}
  (#^ str name)
  (#^ (| int None) ms))


(defk process-start-ms [stat-text uptime-text now-ms ticks]
  {:pre [(: stat-text str) (: uptime-text str) (: now-ms int) (: ticks int)] :post [(: % (| int None))]
   :tags {:context "worker" :role "judgment"}}
  "/proc/<pid>/stat と /proc/uptime の中身から process の始まりの刻(epoch ミリ秒)を返すため。始まり = 機体の起動の刻(今 − uptime)+
   starttime(stat の 22 番目の欄・clock tick の数)÷ ticks(1 秒の tick の数)。/proc/stat の btime(秒の単位)を使わず uptime から引くのは
   ミリ秒の桁を残すため。形の崩れた中身は None。"
  ;; process の名(2 番目の欄)は空白と括弧を含み得るので、最後の `)` の後ろだけを欄に割る(その先頭が 3 番目の欄)。
  (val tail (get (.rpartition stat-text ")") 2))
  (val fields (.split tail))
  (val uptime (.split uptime-text))
  (if (or (< (len fields) 20) (not uptime) (<= ticks 0) (not (.isdigit (get fields 19))))
      None
      (do (val booted-ms (- now-ms (* 1000 (float (get uptime 0)))))
          (int (round (+ booted-ms (/ (* 1000 (int (get fields 19))) ticks)))))))


(defk env-ms [environ name]
  {:pre [(: environ dict) (: name str)] :post [(: % (| int None))] :tags {:context "worker" :role "judgment"}}
  "環境変数 name の epoch ミリ秒(無い・数でなければ None)を返すため。"
  (val text (.strip (.get environ name "")))
  (if (.isdigit text) (int text) None))


(defk read-boot-marks [environ imported-ms]
  {:pre [(: environ dict) (: imported-ms int)] :post [(: % BootMarks)] :tags {:context "worker" :role "judgment"}}
  "worker の起動の刻を集めるため(environ = 入口が読んだ機体の環境変数・imported-ms = 入口 main の頭の刻)。process の始まりは
   ProcessStartedMs で問う(答え手は入口が並べる worker/protocol/process_clock)。"
  (<- pod (| int None) (ProcessStartedMs "1"))
  (<- process (| int None) (ProcessStartedMs "self"))
  (<- script (| int None) (env-ms environ BOOT-STARTED-VAR))
  (<- exec-at (| int None) (env-ms environ BOOT-EXEC-VAR))
  (BootMarks :pod-ms pod :script-ms script :exec-ms exec-at :process-ms process :imported-ms imported-ms))


(defk boot-stamps [marks answered-ms]
  {:pre [(: marks BootMarks) (: answered-ms int)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "起動の 5 つの刻(BootStamp の列・起きる順)を返すため: Pod の起動 → boot.sh の始まり → exec → import の終わり → 最初の heartbeat の答え。"
  #((BootStamp :name "pod" :ms marks.pod-ms) (BootStamp :name "script" :ms marks.script-ms)
    (BootStamp :name "exec" :ms marks.exec-ms) (BootStamp :name "imported" :ms marks.imported-ms)
    (BootStamp :name "answered" :ms answered-ms)))


(defk stamps-out-of-order [stamps]
  {:pre [(: stamps tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "取れた刻(None を外す)を起きる順に並べた時、前の刻より早い刻を名指すため(#(前の名 後の名) の列 — 空 = 順のとおり)。"
  (val known (tuple (gfor s stamps :if (is-not s.ms None) s)))
  (tuple (gfor #(a b) (zip known (cut known 1 None)) :if (< b.ms a.ms) #(a.name b.name))))


(defk boot-line [marks answered-ms]
  {:pre [(: marks BootMarks) (: answered-ms int)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "起動の内訳の 1 行を作るため(最初の heartbeat の答えの後に 1 度だけ出す): 5 つの刻(epoch ミリ秒・取れない刻は `-`)と、取れた刻の
   隣どうしの間の秒、OS の process の始まり。"
  (<- stamps tuple (boot-stamps marks answered-ms))
  (val known (tuple (gfor s stamps :if (is-not s.ms None) s)))
  (val spans (lfor #(a b) (zip known (cut known 1 None)) (.format "{}→{}={:.3f}s" a.name b.name (/ (- b.ms a.ms) 1000.0))))
  (.format "worker: 起動の内訳 {} 間 {} (OS の process の始まり process={})"
           (.join " " (gfor s stamps (.format "{}={}" s.name (if (is s.ms None) "-" s.ms))))
           (.join " " spans)
           (if (is marks.process-ms None) "-" marks.process-ms)))
