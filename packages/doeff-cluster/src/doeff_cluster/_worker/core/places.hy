;;; worker の置き場(AL-D2c — 同じ機体に同じ名の worker の 2 つの世代が並ぶ Deployment の RollingUpdate): worker の入口(worker/entry/main)が
;;; 状態の dir(--state-dir — 世代ごと)と分け合う dir(--shared-dir — 世代をまたぐ)とこの process の世代の名(boot)から作る置き場を、1 か所で決める。
;;;
;;; 世代ごと = 版の木(code)・job の cwd(jobs)・取り寄せた Program(programs)・task の結果(tasks)・温める子(warm)・入口の検め(probe)・
;;; 拍の状態(status.json)。分け合う = job の log(logs)。
;;; log の名は job の名と試みの番号だけでは世代を分けない — 試みの番号は worker の process の記憶(JobRecord)で数え、新しい世代は 1 から
;;; 数え直す(2026-10-11 の入れ替えで、2 つの世代が同じ <job の名>.1.log に書き足した・AL-D2j)。そこで分け合う dir の中の名に、
;;; 世代の印(generation — boot の頭)を入れる: job の log と待ちの子から分けた task の exit の file(綴りは process_host の job-log-stem)・
;;; 待ちの子の log(warm_host の warm-log-path)。
;;; 準備の要求は EnvSettings の state(世代ごと)、実行環境の root・固定の file・Python・wheel・mirror と錠は EnvSettings の shared
;;; (worker/protocol/env_store — root を世代ごとに準備し直さない・AL-D2g)。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_cluster._worker.core.warm_rules [warm-dir-of])

;; 世代の印の長さ: boot(16 byte の乱数の 16 進 32 字 — main の boot-name)の頭 12 字(48 bit)。同じ機体に積もる世代どうしが重ならず、
;; process の世代の名(process_host の instance)の 12 桁と同じ長さで読める。
(val GENERATION-MARK-LENGTH 12)
(val HEX-DIGITS "0123456789abcdef")


(defrecord WorkerPlaces
  "worker の入口が作る置き場の path: code = 版の木の cache・jobs = job の cwd の根・programs = 取り寄せた Program・tasks = task の結果・
   warm = 温める子の根・probe = 入口の検めの dir・status = 拍の状態の file・logs = job の log の dir(世代をまたいで分け合う)・
   generation = 分け合う log の dir の中の名に入れる世代の印(boot の頭 — 2 つの世代が同じ job を同じ試みの番号で動かしても、別の file に書く)。"
  {:tags {:context "worker" :role "type"}}
  (#^ str code)
  (#^ str jobs)
  (#^ str programs)
  (#^ str tasks)
  (#^ str warm)
  (#^ str probe)
  (#^ str status)
  (#^ str logs)
  (#^ str generation))


(defk worker-places [state shared boot]
  {:pre [(: state str) (: shared str) (: boot str) (>= (len boot) GENERATION-MARK-LENGTH) (all (gfor c boot (in c HEX-DIGITS)))]
   :post [(: % WorkerPlaces)] :tags {:context "worker" :role "judgment"}}
  "状態の dir(世代ごと)と分け合う dir と世代の名 boot から worker の置き場を作るため — 世代ごとの物は state の下、log だけを shared の
   下に置き、log の名に入れる世代の印を boot の頭から導く(boot は 16 進の字だけ — file の名にそのまま入る)。"
  ;; 温める子の根は、起こす宿・分ける宿・掃除の係と同じ綴り(warm-dir-of)。
  (<- warm str (warm-dir-of state))
  (WorkerPlaces :code (+ state "/code")
                :jobs (+ state "/jobs")
                :programs (+ state "/programs")
                :tasks (+ state "/tasks")
                :warm warm
                :probe (+ state "/probe")
                :status (+ state "/status.json")
                :logs (+ shared "/logs")
                :generation (cut boot 0 GENERATION-MARK-LENGTH)))
