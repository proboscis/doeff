;; 実行環境の準備の確かめ(処理ステージ probe — env_handlers.hy の PROBE-PROGRAM)が、子の入口の約束の版を root の新旧の両方の置き場から
;; 読むことの検(issue #2413)。
;;
;; 何のため: c56fa8634(2026-10-01)が runtime_env_model を doeff_cluster.shared.intent へ移した。probe が片方の置き場だけを読むと、
;; worker の版と root の版の組によって約束の版を 0 と読み、起こせる root を env-failed にする(2026-10-01 22:20 の service の宣言 1 つ —
;; 旧い置き場だけを読む worker が、新しい置き場の root を断った。逆に新しい置き場だけを読む worker は、旧い doeff で宣言した service を
;; 断る)。
;; 筋書き: 一時の dir に doeff_cluster の最小の root(約束の版を 1 つの置き場にだけ置く)を作り、本物の hy で PROBE-PROGRAM を撃って、
;; 出力の JSON の childProtocol を読む(本番と同じく `hy -c <PROBE-PROGRAM> <root>`・cwd = 空の作業 dir・root を PYTHONPATH の先頭に置く)。
;; 置き場ごとに違う版の値を置くので、環境の本物の doeff_cluster ではなく一時の root を読んだことが値で分かる。
;;   1. 新しい置き場だけの root → その値。
;;   2. 旧い置き場だけの root → その値。
;;   3. どちらにも無い root → 0(約束の版を名乗らない root — worker が env-failed にする形)。片方の置き場だけを読む probe では
;;      1 か 2 が 3 と同じ 0 になる(直す前の形の失敗)。
(require doeff-hy.macros [deftest defk val var])
(import json)
(import os)
(import shutil)
(import subprocess)
(import sys)
(import tempfile)
(import pathlib [Path])
(import doeff_cluster._worker.protocol.env_translation [PROBE-PROGRAM CHILD-PROTOCOL-PLACES])
(import doeff_cluster._shared.intent.job_model [CHILD-ENTRY-PLACES])

(setv HY (str (/ (. (Path sys.executable) parent) "hy"))
      ;; 置き場ごとの版の値(本物の CHILD-PROTOCOL の 1 と重ならない値)。
      NEW-PLACE-PROTOCOL 9
      OLD-PLACE-PROTOCOL 7)


(defk probed-protocol [place value]
  {:pre [(: place (| str None)) (: value int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "place(None = 置かない)の module に CHILD_PROTOCOL = value を置いた doeff_cluster の最小の root を作り、本物の hy で PROBE-PROGRAM を
   撃って、probe が読んだ約束の版を返すため(一時の dir は終わりに消す)。"
  (val root (Path (tempfile.mkdtemp :prefix "probe-root-")))
  (val work (Path (tempfile.mkdtemp :prefix "probe-cwd-")))
  (try
    (val package (/ root "doeff_cluster"))
    (.mkdir package)
    (.write-text (/ package "__init__.py") "")
    (when place
      (val parts (cut (.split place ".") 1 None))
      (var directory package)
      (for [part (cut parts 0 -1)]
        (:= directory (/ directory part))
        (.mkdir directory :exist-ok True)
        (.write-text (/ directory "__init__.py") ""))
      (.write-text (/ directory (+ (get parts -1) ".py")) (.format "CHILD_PROTOCOL = {}\n" value)))
    (val done (subprocess.run [HY "-c" PROBE-PROGRAM (str root)] :cwd (str work) :capture-output True :text True :timeout 120
                               :env (| (dict os.environ) {"PYTHONPATH" (str root)})))
    (assert (= done.returncode 0) done.stderr)
    (get (json.loads (get (.splitlines (.strip done.stdout)) -1)) "childProtocol")
    (finally
      (shutil.rmtree root :ignore-errors True)
      (shutil.rmtree work :ignore-errors True))))


(deftest test-the-probe-reads-the-hidden-place
  ;; ce175be53 以降の doeff の root(約束の版は _shared/intent の runtime_env_model)。移し替えの module shared.py を
  ;; 消した版の root では、ここしか読めない。
  (assert (= (get CHILD-PROTOCOL-PLACES 0) "doeff_cluster._shared.intent.runtime_env_model") CHILD-PROTOCOL-PLACES)
  (assert (= (! (probed-protocol (get CHILD-PROTOCOL-PLACES 0) NEW-PLACE-PROTOCOL)) NEW-PLACE-PROTOCOL)))


(deftest test-the-probe-reads-the-new-place
  ;; c56fa8634 から ce175be53 より前の doeff の root(約束の版は shared/intent の runtime_env_model)— 今動いている service の多くの root。
  (assert (= (get CHILD-PROTOCOL-PLACES 1) "doeff_cluster.shared.intent.runtime_env_model") CHILD-PROTOCOL-PLACES)
  (assert (= (! (probed-protocol (get CHILD-PROTOCOL-PLACES 1) NEW-PLACE-PROTOCOL)) NEW-PLACE-PROTOCOL)))


(deftest test-the-probe-reads-the-old-place
  ;; c56fa8634 より前の doeff で宣言した root(約束の版は doeff_cluster.runtime_env_model)。
  (assert (= (get CHILD-PROTOCOL-PLACES 2) "doeff_cluster.runtime_env_model") CHILD-PROTOCOL-PLACES)
  (assert (= (! (probed-protocol (get CHILD-PROTOCOL-PLACES 2) OLD-PLACE-PROTOCOL)) OLD-PLACE-PROTOCOL)))


;; --- 子の入口の置き場(entryPlace)-------------------------------------------------------------
;; worker は完成の印のこの欄から root へ送る子の入口の名を選ぶ。区画 worker を _worker へ隠す前の doeff の root(2026-10-31 の本番の 64 本の
;; うち 61 本)は区画を隠す前の置き場だけを持ち、移し替えの module worker.py を消した版の root は _worker だけを持つ。

(defk probed-entry-place [places]
  {:pre [(: places tuple)] :post [(: % (| str None))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "子の入口の置き場の列 places の下に job_entry を置いた doeff_cluster の最小の root を作り、本物の hy で PROBE-PROGRAM を撃って、probe が
   答えた子の入口の置き場(entryPlace)を返すため(一時の dir は終わりに消す)。"
  (val root (Path (tempfile.mkdtemp :prefix "probe-root-")))
  (val work (Path (tempfile.mkdtemp :prefix "probe-cwd-")))
  (try
    (val package (/ root "doeff_cluster"))
    (.mkdir package)
    (.write-text (/ package "__init__.py") "")
    (for [place places]
      (var directory package)
      (for [part (cut (.split place ".") 1 None)]
        (:= directory (/ directory part))
        (.mkdir directory :exist-ok True)
        (.write-text (/ directory "__init__.py") ""))
      (.write-text (/ directory "job_entry.py") "V = 1\n"))
    (val done (subprocess.run [HY "-c" PROBE-PROGRAM (str root)] :cwd (str work) :capture-output True :text True :timeout 120
                               :env (| (dict os.environ) {"PYTHONPATH" (str root)})))
    (assert (= done.returncode 0) done.stderr)
    (get (json.loads (get (.splitlines (.strip done.stdout)) -1)) "entryPlace")
    (finally
      (shutil.rmtree root :ignore-errors True)
      (shutil.rmtree work :ignore-errors True))))


(deftest test-the-probe-answers-the-entry-place-the-root-has
  (assert (= CHILD-ENTRY-PLACES #("doeff_cluster._worker.entry" "doeff_cluster.worker.entry")) CHILD-ENTRY-PLACES)
  ;; 区画を隠す前の doeff の root — 区画を隠す前の置き場(_worker の名を送ると子が起きない)。
  (assert (= (! (probed-entry-place #("doeff_cluster.worker.entry"))) "doeff_cluster.worker.entry"))
  ;; 両方を解く root(_worker と移し替えの module の在る版)— 新しい置き場を先に。
  (assert (= (! (probed-entry-place CHILD-ENTRY-PLACES)) "doeff_cluster._worker.entry"))
  ;; 失敗ケース: どの置き場にも入口の無い root は null(準備が env-failed にする — env_prepare の stage-probe)。
  (assert (is (! (probed-entry-place #())) None)))


(deftest test-a-root-without-the-protocol-reads-zero
  ;; 失敗ケース: どちらの置き場にも約束の版が無い root は 0(worker は env-failed にする)。片方の置き場だけを読む probe では、上の 2 本の
  ;; どちらかがこれと同じ 0 になる。
  (assert (= (! (probed-protocol None 0)) 0)))
