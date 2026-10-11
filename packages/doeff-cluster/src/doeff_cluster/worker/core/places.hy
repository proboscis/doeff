;;; worker の置き場(AL-D2c — 同じ機体に同じ名の worker の 2 つの世代が並ぶ Deployment の RollingUpdate): worker の入口(worker/entry/main)が
;;; 状態の dir(--state-dir — 世代ごと)と分け合う dir(--shared-dir — 世代をまたぐ)から作る置き場を、1 か所で決める。
;;;
;;; 世代ごと = 版の木(code)・job の cwd(jobs)・取り寄せた Program(programs)・task の結果(tasks)・温める子(warm)・入口の検め(probe)・
;;; 拍の状態(status.json)。分け合う = job の log(logs — 名は job の名と試みの番号なので、世代が違えば試みが違い、同じ file を書かない)。
;;; 準備の要求は EnvSettings の state(世代ごと)、実行環境の root・固定の file・Python・wheel・mirror と錠は EnvSettings の shared
;;; (worker/protocol/env_store — root を世代ごとに準備し直さない・AL-D2g)。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_cluster.worker.core.warm_rules [warm-dir-of])


(defrecord WorkerPlaces
  "worker の入口が作る置き場の path: code = 版の木の cache・jobs = job の cwd の根・programs = 取り寄せた Program・tasks = task の結果・
   warm = 温める子の根・probe = 入口の検めの dir・status = 拍の状態の file・logs = job の log の dir。"
  {:tags {:context "worker" :role "type"}}
  (#^ str code)
  (#^ str jobs)
  (#^ str programs)
  (#^ str tasks)
  (#^ str warm)
  (#^ str probe)
  (#^ str status)
  (#^ str logs))


(defk worker-places [state shared]
  {:pre [(: state str) (: shared str)] :post [(: % WorkerPlaces)] :tags {:context "worker" :role "judgment"}}
  "状態の dir(世代ごと)と分け合う dir から worker の置き場を作るため — 世代ごとの物は state の下、log だけを shared の下に置く。"
  ;; 温める子の根は、起こす宿・分ける宿・掃除の係と同じ綴り(warm-dir-of)。
  (<- warm str (warm-dir-of state))
  (WorkerPlaces :code (+ state "/code")
                :jobs (+ state "/jobs")
                :programs (+ state "/programs")
                :tasks (+ state "/tasks")
                :warm warm
                :probe (+ state "/probe")
                :status (+ state "/status.json")
                :logs (+ shared "/logs")))
