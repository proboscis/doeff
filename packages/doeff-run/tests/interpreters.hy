;;; 検の解釈器(composition root)— 同じ筋書きの Program を、handler の組だけ替えて走らせる。
;;;
;;;   fake    fake の handler(FakeRunWorld)+ 仮想の時計(sim-time-handler)。負荷の振る舞いは workloads.fake-behavior-of。
;;;   docker  docker の handler(DockerRunHost)+ 壁の時計(sync-time-handler)+ 本物の子 process(offloaded-subprocess-handler)。
;;;           docker の在る機体(zeus)の検で、環境変数 DOEFF_RUN_DOCKER=1 の時だけ走る(無ければ skip — 在るのに docker が
;;;           答えなければ赤)。負荷の image は workload/ の Dockerfile をその場で build する(中身の hash の tag — 同じ中身は build
;;;           しない)。検ごとに新しい DockerRunHost を作り、検の終わりにその host の label の container を全部消す。
;;;
;;;   k3s     k3s の handler(K3sRunHost)+ 壁の時計 + 本物の子 process(kubectl を呼ぶ)。cluster の検で、環境変数
;;;           DOEFF_RUN_K3S=1 の時だけ走る(無ければ skip — 在るのに cluster が答えなければ赤)。次の環境変数を読む:
;;;             DOEFF_RUN_K3S_NAMESPACE  run を立てる namespace(必須 — 検専用に作った物。検の終わりに、その host の印の Pod を消す)
;;;             DOEFF_RUN_K3S_IMAGE      契約の負荷 contract-workload が入った image(必須 — node が取り寄せられる参照。tests/workload/ の
;;;                                      Dockerfile を image-build で registry へ置く。cluster の node は手元の docker を使えない)
;;;             DOEFF_RUN_K3S_AS         kubectl を替え身で呼ぶ身元(任意 — 例 system:serviceaccount:<ns>:<sa>。本番に渡す権限だけで
;;;                                      契約が通る事を、その身元の Role で確かめる)
;;;
;;; process の handler を足す card(Y-8)は、ここに解釈器の名と組(handlers-for・settings-for)を足し、
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
(import doeff_core_effects [scheduled offloaded-subprocess-handler])
(import doeff_time [SimClock sim-time-handler sync-time-handler])
(import doeff_run [FakeRunWorld fake-run-handler DockerRunHost docker-run-handler K3sRunHost k3s-run-handler])
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

(setv K3S "k3s")
;; k3s の解釈器を走らせる印の環境変数(値 1)と、読む環境変数(頭の註)。
(setv K3S-GATE "DOEFF_RUN_K3S")
(setv K3S-NAMESPACE-ENV "DOEFF_RUN_K3S_NAMESPACE")
(setv K3S-IMAGE-ENV "DOEFF_RUN_K3S_IMAGE")
(setv K3S-AS-ENV "DOEFF_RUN_K3S_AS")
;; 取り寄せられない image: 解決できない host の registry(node の取り寄せは直ちに断られ、backoff に入る)。
(setv K3S-MISSING-IMAGE "registry.invalid/doeff-run-contract-missing:absent")


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

(defn #^ None remove-docker-runs [#^ DockerRunHost host]
  "host が作った container(label の印)を全部消す(検の後始末)。"
  (setv listed (subprocess.run ["docker" "container" "ls" "--all" "--quiet" "--filter" (.format "label=doeff-run.host={}" host.host-id)]
                               :check True :capture-output True :text True))
  (setv ids (.split listed.stdout))
  (when ids
    (subprocess.run (+ ["docker" "container" "rm" "--force"] ids) :check True :capture-output True))
  None)

(defn #^ K3sRunHost k3s-host []
  "k3s の解釈器の host(検ごとに新しい印)。namespace は環境変数で受け、無ければ赤(走らせると決めたのに足りない)。DOEFF_RUN_K3S_AS が
   あれば、kubectl をその身元の替え身で呼ぶ(本番に渡す権限だけで通る事の確かめ)。"
  (setv namespace (os.environ.get K3S-NAMESPACE-ENV))
  (when (not namespace)
    (raise (RuntimeError (.format "k3s の解釈器には環境変数 {} が要る(検専用の namespace)" K3S-NAMESPACE-ENV))))
  (setv identity (os.environ.get K3S-AS-ENV))
  (K3sRunHost :namespace namespace :kubectl (if identity #("kubectl" "--as" identity) #("kubectl"))))

(defn #^ None remove-k3s-runs [#^ K3sRunHost host]
  "host が作った Pod(label の印)を全部消す(検の後始末 — 検を走らせる管理者の身元で消す。止めた Pod は finalizer で残るので、
   finalizer を外してから強制で消す)。Secret は Pod の持ち物なので Pod と一緒に消える。"
  (setv listed (subprocess.run ["kubectl" "--namespace" host.namespace "get" "pods" "--selector" (.format "doeff-run.host={}" host.host-id) "--output" "name"]
                               :check True :capture-output True :text True))
  (for [pod (.split listed.stdout)]
    (subprocess.run ["kubectl" "--namespace" host.namespace "patch" pod "--type" "merge" "--patch" "{\"metadata\":{\"finalizers\":null}}"]
                    :capture-output True)
    (subprocess.run ["kubectl" "--namespace" host.namespace "delete" pod "--grace-period" "0" "--force" "--wait=false"]
                    :check True :capture-output True))
  None)


(defn settings-for [#^ str name #^ Path tmp-path]
  (cond
    (= name FAKE) (Settings :image FAKE-IMAGE :missing-image FAKE-MISSING-IMAGE :slack 0.01 :interpreter name)
    (= name DOCKER)
      (do
        (when (!= (os.environ.get DOCKER-GATE) "1")
          (pytest.skip (.format "docker の解釈器は {}=1 の時だけ走る(docker の在る機体 — zeus の検)" DOCKER-GATE)))
        (Settings :image (docker-workload-image) :missing-image DOCKER-MISSING-IMAGE :slack 1.0 :interpreter name))
    (= name K3S)
      (do
        (when (!= (os.environ.get K3S-GATE) "1")
          (pytest.skip (.format "k3s の解釈器は {}=1 の時だけ走る(cluster の検 — namespace と負荷の image を環境変数で渡す)" K3S-GATE)))
        (setv image (os.environ.get K3S-IMAGE-ENV))
        (when (not image)
          (raise (RuntimeError (.format "k3s の解釈器には環境変数 {} が要る(contract-workload の入った image)" K3S-IMAGE-ENV))))
        (Settings :image image :missing-image K3S-MISSING-IMAGE :slack 1.0 :interpreter name))
    True (raise (ValueError (.format "知らない解釈器: {!r}" name)))))

(defn handlers-for [#^ str name #^ Settings settings #^ (| DockerRunHost K3sRunHost None) host]
  (cond
    (= name FAKE)
      [(sim-time-handler :clock (SimClock))
       (fake-run-handler (FakeRunWorld :behavior-of (fn [spec] (fake-behavior-of spec settings.missing-image))))]
    (= name DOCKER)
      [(sync-time-handler) offloaded-subprocess-handler (docker-run-handler host)]
    (= name K3S)
      [(sync-time-handler) offloaded-subprocess-handler (k3s-run-handler host)]
    True []))

(defn build-interpreter [#^ str name #^ Path tmp-path]
  "名 → Program を走らせる関数。名が plain の時は scheduler だけ(handler を要しない純関数の検)。docker・k3s は走らせた後に、その検の
   container・Pod を全部消す。"
  (when (= name "plain")
    (return (fn [program] (run (scheduled program)))))
  (setv settings (settings-for name tmp-path))
  (setv host (cond (= name DOCKER) (DockerRunHost) (= name K3S) (k3s-host) True None))
  (setv stack (+ (handlers-for name settings host) [(scenario-settings settings)]))
  (fn [program]
    (try
      (run (scheduled (with_handlers stack program)))
      (finally
        (cond
          (isinstance host DockerRunHost) (remove-docker-runs host)
          (isinstance host K3sRunHost) (remove-k3s-runs host))))))
