;;; 検の解釈器(composition root)— 同じ筋書きの Program を、handler の組だけ替えて走らせる。
;;;
;;;   fake    fake の handler(FakeRunWorld)+ 仮想の時計(sim-time-handler)。負荷の振る舞いは workloads.fake-behavior-of。
;;;   docker  docker の handler(DockerRunHost)+ 壁の時計(sync-time-handler)+ 本物の子 process(offloaded-subprocess-handler)。
;;;           docker の在る機体(zeus)の検で、環境変数 DOEFF_RUN_DOCKER=1 の時だけ走る(無ければ skip — 在るのに docker が
;;;           答えなければ赤)。負荷の image は workload/ の Dockerfile をその場で build する(中身の hash の tag — 同じ中身は build
;;;           しない)。検ごとに新しい DockerRunHost を作り、検の終わりにその host の label の container を全部消す。
;;;   process process の handler(ProcessRunHost)+ 壁の時計 + 子 process の答え手と終わりの待ち(await-handler・process-exit-handler)。
;;;           docker の無い手元の開発の handler なので、いつでも走る(sh の在る機体)。image の名 → 負荷の dir の表を host に渡し、
;;;           負荷の実行 file は dir ごと PATH の先頭に載る。資源の上限と volume は守れない設定として断る handler なので、メモリの上限の
;;;           筋書きは OOM の筋書きでなく「断る」筋書きを通す(test_contract.hy)。検の終わりに host の走っている子を全部止める。
;;;
;;; k3s の handler を足す card(Y-9)は、ここに解釈器の名と組(handlers-for・settings-for)を足し、
;;; test_contract.hy の各 deftest の :interpreters に名を足す。筋書きと workloads.hy の約束は変えない — 同じ契約を通す事が目的。
;;;
;;; 筋書きは ScenarioSettings の effect で自分の宣言(負荷の image・時間の許容)を読む。
(require doeff-hy.macros [defhandler])
(import dataclasses [dataclass])
(import hashlib)
(import os)
(import pathlib [Path])
(import subprocess)
(import pytest)
(import doeff [EffectBase run with_handlers])
(import doeff_core_effects [scheduled offloaded-subprocess-handler await-handler process-exit-handler StopProcess])
(import doeff_time [SimClock sim-time-handler sync-time-handler])
(import doeff_run [FakeRunWorld fake-run-handler DockerRunHost docker-run-handler ProcessRunHost process-run-handler])
(import tests.workloads [fake-behavior-of])

(setv FAKE "fake")
(setv FAKE-IMAGE "registry.invalid/contract-workload@sha256:0000000000000000000000000000000000000000000000000000000000000000")
(setv FAKE-MISSING-IMAGE "registry.invalid/missing@sha256:1111111111111111111111111111111111111111111111111111111111111111")

(setv DOCKER "docker")
;; docker の解釈器を走らせる印の環境変数(値 1)。
(setv DOCKER-GATE "DOEFF_RUN_DOCKER")
(setv WORKLOAD-DIR (/ (. (Path __file__) parent) "workload"))
;; 取り寄せられない image: 誰も listen しない手元の port の registry(取り寄せは直ちに断られる)。
(setv DOCKER-MISSING-IMAGE "127.0.0.1:9/doeff-run-contract-missing:absent")

(setv PROCESS "process")
;; process の解釈器の image の名: 負荷の dir(workload/)に引ける名と、host の表に無い名(起動できない image)。
(setv PROCESS-IMAGE "local/contract-workload")
(setv PROCESS-MISSING-IMAGE "local/missing")


(defclass [(dataclass :frozen True)] Settings []
  "筋書きが読む宣言: image = 契約の負荷 contract-workload が入った image / missing-image = 起動できない image の名 /
   slack = 時間の比べの許容(秒 — 仮想の時計は刻み 1 マイクロ秒、壁の時計の handler はもっと広く)/ interpreter = 名。"
  (#^ str image)
  (#^ str missing-image)
  (#^ float slack)
  (#^ str interpreter))

(defclass [(dataclass :frozen True)] ScenarioSettings [EffectBase])

(defhandler scenario-settings [settings]
  (ScenarioSettings []
    (resume settings)))


(defn #^ str docker-workload-image []
  "負荷の image を build して tag を返す(tag は workload/ の中身の hash — 在れば build しない)。docker が答えなければ赤。"
  (setv digest (hashlib.sha256))
  (for [path (sorted (.iterdir WORKLOAD-DIR))]
    (.update digest (.encode path.name))
    (.update digest (.read-bytes path)))
  (setv tag (+ "doeff-run-contract-workload:" (cut (.hexdigest digest) 0 12)))
  (when (!= (. (subprocess.run ["docker" "image" "inspect" tag] :capture-output True) returncode) 0)
    (subprocess.run ["docker" "build" "--tag" tag (str WORKLOAD-DIR)] :check True :capture-output True))
  tag)

(defn process-host []
  "process の解釈器の host: 負荷の image の名 → workload/ の dir の表を持つ。"
  (ProcessRunHost :images {PROCESS-IMAGE (str WORKLOAD-DIR)}))

(defn #^ None stop-process-runs [#^ ProcessRunHost host]
  "host の走っている子を全部、猶予なしで止める(検の後始末 — 検の run はもう終わっているので、後始末のためだけの小さい run で止める)。"
  (for [pid host.live-pids]
    (run (scheduled (with_handlers [offloaded-subprocess-handler] (StopProcess :pid pid :stop-grace 0.0)))))
  None)

(defn #^ None remove-docker-runs [#^ DockerRunHost host]
  "host が作った container(label の印)を全部消す(検の後始末)。"
  (setv listed (subprocess.run ["docker" "container" "ls" "--all" "--quiet" "--filter" (.format "label=doeff-run.host={}" host.host-id)]
                               :check True :capture-output True :text True))
  (setv ids (.split listed.stdout))
  (when ids
    (subprocess.run (+ ["docker" "container" "rm" "--force"] ids) :check True :capture-output True))
  None)


(defn settings-for [#^ str name #^ Path tmp-path]
  (cond
    (= name FAKE) (Settings :image FAKE-IMAGE :missing-image FAKE-MISSING-IMAGE :slack 0.01 :interpreter name)
    (= name DOCKER)
      (do
        (when (!= (os.environ.get DOCKER-GATE) "1")
          (pytest.skip (.format "docker の解釈器は {}=1 の時だけ走る(docker の在る機体 — zeus の検)" DOCKER-GATE)))
        (Settings :image (docker-workload-image) :missing-image DOCKER-MISSING-IMAGE :slack 1.0 :interpreter name))
    (= name PROCESS) (Settings :image PROCESS-IMAGE :missing-image PROCESS-MISSING-IMAGE :slack 1.0 :interpreter name)
    True (raise (ValueError (.format "知らない解釈器: {!r}" name)))))

(defn handlers-for [#^ str name #^ Settings settings host]
  (cond
    (= name FAKE)
      [(sim-time-handler :clock (SimClock))
       (fake-run-handler (FakeRunWorld :behavior-of (fn [spec] (fake-behavior-of spec settings.missing-image))))]
    (= name DOCKER)
      [(sync-time-handler) offloaded-subprocess-handler (docker-run-handler host)]
    (= name PROCESS)
      [(sync-time-handler) await-handler offloaded-subprocess-handler process-exit-handler (process-run-handler host)]
    True []))

(defn build-interpreter [#^ str name #^ Path tmp-path]
  "名 → Program を走らせる関数。名が plain の時は scheduler だけ(handler を要しない純関数の検)。docker は走らせた後に、その検の
   container を全部消し、process は走っている子を全部止める。"
  (when (= name "plain")
    (return (fn [program] (run (scheduled program)))))
  (setv settings (settings-for name tmp-path))
  (setv host (cond (= name DOCKER) (DockerRunHost) (= name PROCESS) (process-host) True None))
  (setv stack (+ (handlers-for name settings host) [(scenario-settings settings)]))
  (fn [program]
    (try
      (run (scheduled (with_handlers stack program)))
      (finally
        (cond
          (= name DOCKER) (remove-docker-runs host)
          (= name PROCESS) (stop-process-runs host))))))
