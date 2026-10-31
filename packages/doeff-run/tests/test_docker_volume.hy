;;; docker の handler の volume の効果の検 — docker の CLI を台本(RunProcess の答え手)で模して、docker の無い機体でも handler の訳
;;; (作る前の断り・命令・「作った」と「在った」の見分け・作ったつもりで無い事の防ぎ)を確かめる。本物の docker を撃つ筋書きは
;;; 契約の検(test_volume_contract.hy の解釈器 docker — DOEFF_RUN_DOCKER=1)。失敗の筋書きを先に置く。
(require doeff-hy.macros [deftest defhandler defk <- val var])
(import json)
(import doeff [with_handlers])
(import doeff_core_effects [RunProcess ProcessOutcome])
(import doeff_run [DockerRunHost docker-run-handler RefusalWhy VolumeEnsure VolumeStatus
                   VolumeCreated VolumeExisted VolumePresent VolumeAbsent VolumeRefused])

(setv RIVAL-LABELS {"doeff-run.host" "another-host" "doeff-run.volume-token" "rivals-token"})


(defclass ScriptedDocker []
  "docker の CLI の台本: volumes = 名 → label の写像(docker の daemon が持つ volume)・calls = 受けた命令(docker の後ろの引数)の記録。
   rival = create の直前に、別の作り手が同じ名をこの label で作る(作成が重なった時)・create-error = create を断る文・
   forgets = create は成功と答えるが volume を残さない(作ったつもりで無い)。"
  (defn __init__ [self * [volumes None] [rival None] [create-error None] [forgets False]]
    (setv self.volumes (dict (if (is volumes None) {} volumes)))
    (setv self.calls #())
    (setv self.rival rival self.create-error create-error self.forgets forgets))
  (defn answer [self argv]
    (assert (= (get argv 0) "docker") argv)
    (setv args (tuple (cut argv 1 None)))
    (setv self.calls (+ self.calls #(args)))
    (cond
      (= (cut args 0 2) #("volume" "inspect")) (self.inspect (get args -1))
      (= (cut args 0 2) #("volume" "create")) (self.create args)
      True (raise (AssertionError (.format "台本に無い docker の命令: {!r}" args)))))
  (defn inspect [self name]
    (if (in name self.volumes)
        (ProcessOutcome :exit-code 0 :stdout (json.dumps [{"Name" name "Labels" (or (get self.volumes name) None)}]) :stderr "")
        (ProcessOutcome :exit-code 1 :stdout "[]" :stderr (.format "Error response from daemon: get {}: no such volume" name))))
  (defn labels-of [self args]
    (setv labels {})
    (for [#(flag value) (zip args (cut args 1 None))]
      (when (= flag "--label")
        (setv #(key text) (.split value "=" 1))
        (setv (get labels key) text)))
    labels)
  (defn create [self args]
    (setv name (get args -1))
    (when (is-not self.rival None)
      (setv (get self.volumes name) self.rival)
      (setv self.rival None))
    (when (is-not self.create-error None)
      (return (ProcessOutcome :exit-code 1 :stdout "" :stderr self.create-error)))
    ;; docker は在る名への create を黙って受け、在る volume(と label)をそのままにする。
    (when (and (not self.forgets) (not-in name self.volumes))
      (setv (get self.volumes name) (self.labels-of args)))
    (ProcessOutcome :exit-code 0 :stdout (+ name "\n") :stderr "")))

;; 引数に残す理由: 台本の daemon は検ごとに違う世界(volume の表と受けた命令の記録)を持ち、検がその記録を読む。
(defhandler scripted-docker [daemon]
  (RunProcess [argv]
    (resume (daemon.answer argv))))


(defk ensure-with [daemon host name]
  {:pre [(: daemon ScriptedDocker) (: host DockerRunHost) (: name str)] :post [(: % (| VolumeCreated VolumeExisted VolumeRefused))]}
  "台本の docker と docker の handler の下で VolumeEnsure を撃つ。"
  (<- outcome (with_handlers [(scripted-docker daemon) (docker-run-handler host)] (VolumeEnsure name)))
  outcome)

(defk status-with [daemon host name]
  {:pre [(: daemon ScriptedDocker) (: host DockerRunHost) (: name str)] :post [(: % (| VolumePresent VolumeAbsent VolumeRefused))]}
  "台本の docker と docker の handler の下で VolumeStatus を撃つ。"
  (<- outcome (with_handlers [(scripted-docker daemon) (docker-run-handler host)] (VolumeStatus name)))
  outcome)


;; --- 失敗の筋書き -------------------------------------------------------------------------------------------

(deftest test-a-name-docker-would-read-as-a-path-is-refused-without-calling-docker
  ;; 作る前に断る: docker の named volume の名でない名は、docker を 1 度も呼ばずに UNSUPPORTED で断る(問いも同じ)。
  {:interpreters ["plain"]}
  (val daemon (ScriptedDocker))
  (val host (DockerRunHost :host-id "h1"))
  (for [bad ["/srv/data" "x" "has space" "a,b" ".hidden" "-dash"]]
    (<- made (ensure-with daemon host bad))
    (assert (and (isinstance made VolumeRefused) (= made.why RefusalWhy.UNSUPPORTED) (= made.name bad) made.detail) (repr made))
    (<- asked (status-with daemon host bad))
    (assert (and (isinstance asked VolumeRefused) (= asked.why RefusalWhy.UNSUPPORTED) (= asked.name bad) asked.detail) (repr asked)))
  (assert (= daemon.calls #()) (repr daemon.calls))
  (assert (= daemon.volumes {}) (repr daemon.volumes)))

(deftest test-a-create-that-docker-reports-but-did-not-keep-is-not-created
  ;; 作ったつもりで無い: docker が成功と答えても、問い直して volume が見えなければ「作った」と答えない(作れなかった・訳つき)。
  {:interpreters ["plain"]}
  (val daemon (ScriptedDocker :forgets True))
  (<- made (ensure-with daemon (DockerRunHost :host-id "h1") "vol-a"))
  (assert (and (isinstance made VolumeRefused) (= made.why RefusalWhy.NO-CAPACITY) (= made.name "vol-a") (in "見えない" made.detail)) (repr made))
  (assert (= daemon.volumes {}) (repr daemon.volumes)))

(deftest test-a-create-docker-refuses-is-not-created-and-carries-dockers-words
  ;; docker が create を断った(容量など): 「作った」と答えず、訳に docker の最後の行を写す(作れなかった・何も残らない)。
  {:interpreters ["plain"]}
  (val daemon (ScriptedDocker :create-error "a warning line\nError response from daemon: disk is full"))
  (<- made (ensure-with daemon (DockerRunHost :host-id "h1") "vol-a"))
  (assert (and (isinstance made VolumeRefused) (= made.why RefusalWhy.NO-CAPACITY) (in "disk is full" made.detail)) (repr made))
  (assert (not-in "a warning line" made.detail) (repr made))
  (<- asked (status-with daemon (DockerRunHost :host-id "h1") "vol-a"))
  (assert (= asked (VolumeAbsent :name "vol-a")) (repr asked)))

(deftest test-a-volume-another-creator-made-first-is-existed-not-created
  ;; 作成が重なった時: create の直前に別の作り手が同じ名を作っていたら、docker は在る volume をそのままにする — その volume の印(token)は
  ;; こちらの物でないので「在った」と答える(「作った」と答えるのは作った 1 つだけ)。
  {:interpreters ["plain"]}
  (val daemon (ScriptedDocker :rival RIVAL-LABELS))
  (<- made (ensure-with daemon (DockerRunHost :host-id "h1") "vol-a"))
  (assert (= made (VolumeExisted :name "vol-a")) (repr made))
  (assert (= (get daemon.volumes "vol-a") RIVAL-LABELS) (repr daemon.volumes)))

(deftest test-a-create-docker-refuses-because-the-name-is-taken-is-existed
  ;; docker が重なった作成を誤りで断る版でも、問い直して在れば「在った」(volume は 1 つ・作ったと偽らない)。
  {:interpreters ["plain"]}
  (val daemon (ScriptedDocker :rival RIVAL-LABELS :create-error "Error response from daemon: a volume named vol-a already exists"))
  (<- made (ensure-with daemon (DockerRunHost :host-id "h1") "vol-a"))
  (assert (= made (VolumeExisted :name "vol-a")) (repr made)))

(deftest test-a-volume-that-is-already-there-is-not-created-again
  ;; 在る volume には create を撃たない(命令は問いだけ)。「在った」と答え、label も中身もそのまま。
  {:interpreters ["plain"]}
  (val daemon (ScriptedDocker :volumes {"vol-a" {}}))
  (<- made (ensure-with daemon (DockerRunHost :host-id "h1") "vol-a"))
  (assert (= made (VolumeExisted :name "vol-a")) (repr made))
  (assert (= daemon.calls #(#("volume" "inspect" "--" "vol-a"))) (repr daemon.calls)))

(deftest test-asking-never-creates
  ;; 問いは作らない: 無い volume を問うても create を撃たず、無いままと答える。在れば在ると答える。
  {:interpreters ["plain"]}
  (val daemon (ScriptedDocker :volumes {"vol-b" {}}))
  (val host (DockerRunHost :host-id "h1"))
  (<- absent (status-with daemon host "vol-a"))
  (<- present (status-with daemon host "vol-b"))
  (assert (= absent (VolumeAbsent :name "vol-a")) (repr absent))
  (assert (= present (VolumePresent :name "vol-b")) (repr present))
  (assert (all (gfor call daemon.calls (= (cut call 0 2) #("volume" "inspect")))) (repr daemon.calls))
  (assert (not-in "vol-a" daemon.volumes) (repr daemon.volumes)))

(deftest test-created-volume-carries-the-host-label-and-a-fresh-token
  ;; 作った volume は handler の host の印(片付けの的)と、この作成だけの token を label に持つ。「作った」の答えは token が残った時だけ。
  {:interpreters ["plain"]}
  (val daemon (ScriptedDocker))
  (<- first (ensure-with daemon (DockerRunHost :host-id "h1") "vol-a"))
  (<- second (ensure-with daemon (DockerRunHost :host-id "h1") "vol-b"))
  (assert (and (= first (VolumeCreated :name "vol-a")) (= second (VolumeCreated :name "vol-b"))) (repr #(first second)))
  (val labels-a (get daemon.volumes "vol-a"))
  (val labels-b (get daemon.volumes "vol-b"))
  (assert (= (get labels-a "doeff-run.host") "h1") (repr labels-a))
  (assert (and (get labels-a "doeff-run.volume-token") (!= (get labels-a "doeff-run.volume-token") (get labels-b "doeff-run.volume-token")))
          (repr #(labels-a labels-b)))
  (assert (= (get (get daemon.calls 1) -1) "vol-a") (repr daemon.calls)))
