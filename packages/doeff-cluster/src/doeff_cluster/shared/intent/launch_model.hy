;;; worker と coordinator を起こす時の値 — boot.sh が読む環境変数のうち、名乗り(名・能力・枠・取っておく数)と自己起動の版
;;; (WORKER_DOEFF_COMMIT)を型にした物と、配備する側の repo がその望む状態を言う effect(#3366 の単位 1)。
;;;
;;;   (<- changes (DesireWorker (WorkerLaunch :name "w1" :provides #("verify" "host-w1") :exclusive #() :capacity 1
;;;                                           :task-reserve 0 :doeff-commit <40 字の sha>)))
;;;   (<- changes (DesireCoordinator (CoordinatorLaunch :doeff-commit <40 字の sha>)))
;;;
;;; 答え = 変えた行(LaunchLineChange)の tuple — 空なら宣言は既に望む状態。manifest(Deployment の YAML)そのものは配備する側の
;;; repo が持つ(README の「manifest は配備する側の repo が持つ」)ので、答え手(宣言の file の行を書く handler)もその repo に置く。
;;; ここは boot.sh の契約の持ち主として、値の型・値の検め・値から環境変数の行への写し(shared/core/launch_rules.hy)だけを持つ。
;;; 版上げの手順(worker を全部先・coordinator を最後・1 台ずつ)は 2026-10-05 の版上げ 12 回(#3156)で通った形。
;;; 宣言の行の値を変える手順だけを型にし、volume・資格の mount・資源など Deployment の他の欄はここに持たない(配備する側の手書きのまま)。
(require doeff-hy.macros [val defeffect])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])


(defclass [(dataclass :frozen True)] WorkerLaunch []
  "worker 1 台の名乗りと自己起動の版。name = WORKER_NAME・provides = WORKER_PROVIDES(書いた順のまま — 宣言の行は順を保つ)・
   exclusive = WORKER_EXCLUSIVE(空なら行を持たない)・capacity = WORKER_CAPACITY・task-reserve = WORKER_TASK_RESERVE(0 以上
   capacity 以下 — worker の入口 main.hy が起動の時に断るのと同じ範囲)・doeff-commit = WORKER_DOEFF_COMMIT(40 字の sha)。"
  (#^ str name)
  (#^ (get tuple #(str ...)) provides)
  (#^ (get tuple #(str ...)) exclusive)
  (#^ int capacity)
  (#^ int task-reserve)
  (#^ str doeff-commit)
  (defn #^ None __post_init__ [self]  ; defk にできない: dataclass の __post_init__ — 起動の時に断られる値を宣言の時に断る
    (when (not self.name)
      (raise (ValueError "WorkerLaunch.name は空でない worker の名")))
    (when (not self.provides)
      (raise (ValueError (.format "WorkerLaunch {} の provides が空" self.name))))
    (when (or (< self.capacity 1) (< self.task-reserve 0) (> self.task-reserve self.capacity))
      (raise (ValueError (.format "WorkerLaunch {} の task-reserve {} は 0 以上 capacity {} 以下" self.name self.task-reserve
                                  self.capacity))))
    (when (not (and (= (len self.doeff-commit) 40) (all (gfor ch self.doeff-commit (in ch "0123456789abcdef")))))
      (raise (ValueError (.format "WorkerLaunch {} の doeff-commit は 40 字の sha: {!r}" self.name self.doeff-commit))))))


(defclass [(dataclass :frozen True)] StaticWorkerLaunch []
  "機体ごとの静的な worker 1 台の自己起動の版(deploy/k8s/nodes/<Node の名> — 雛形 deploy/k8s/worker の機体ごとの差)。name = worker の名
   (= Node の名 — 宣言は fieldRef で受け、行に書かない)・doeff-commit = WORKER_DOEFF_COMMIT(40 字の sha — 機体の dir の version.yaml の
   1 行)。能力・枠・task に空けておく数は、上に載る系の ConfigMap と機体の dir が渡すので、この値に持たない(宣言の版の行が持つのは
   版だけ — CoordinatorLaunch と同じ形)。"
  (#^ str name)
  (#^ str doeff-commit)
  (defn #^ None __post_init__ [self]  ; defk にできない: dataclass の __post_init__ — 起動の時に断られる値を宣言の時に断る
    (when (not self.name)
      (raise (ValueError "StaticWorkerLaunch.name は空でない worker の名")))
    (when (not (and (= (len self.doeff-commit) 40) (all (gfor ch self.doeff-commit (in ch "0123456789abcdef")))))
      (raise (ValueError (.format "StaticWorkerLaunch {} の doeff-commit は 40 字の sha: {!r}" self.name self.doeff-commit))))))


(defclass [(dataclass :frozen True)] CoordinatorLaunch []
  "coordinator の自己起動の版(WORKER_DOEFF_COMMIT — boot.sh は ROLE=coordinator でも同じ名で読む)。"
  (#^ str doeff-commit)
  (defn #^ None __post_init__ [self]  ; defk にできない: dataclass の __post_init__
    (when (not (and (= (len self.doeff-commit) 40) (all (gfor ch self.doeff-commit (in ch "0123456789abcdef")))))
      (raise (ValueError (.format "CoordinatorLaunch の doeff-commit は 40 字の sha: {!r}" self.doeff-commit))))))


(defrecord LaunchLineChange
  "宣言の行を 1 つ書き換えた事実。unit = 書き換えた宣言の単位(答え手が名指す — 例: Deployment の名)・name = 環境変数の名・
   before / after = 書き換える前と後の値。"
  (#^ str unit)
  (#^ str name)
  (#^ str before)
  (#^ str after))


(defeffect DesireWorker
  "worker 1 台の名乗りと版を launch にする(望む状態を言う — 機体ごとの静的な worker は版だけ)。答え手は配備する側の repo の宣言の行を
   書く handler。答え = 変えた行の tuple(空 = 既に望む状態)。"
  {:fields [(: launch (| WorkerLaunch StaticWorkerLaunch))]
   :answer (get tuple #(LaunchLineChange ...))
   :tags {:context "doeff-cluster" :role "intent"}})

(defeffect DesireCoordinator
  "coordinator の版を launch にする(望む状態を言う)。答え = 変えた行の tuple(空 = 既に望む状態)。"
  {:fields [(: launch CoordinatorLaunch)]
   :answer (get tuple #(LaunchLineChange ...))
   :tags {:context "doeff-cluster" :role "intent"}})
