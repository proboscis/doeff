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
(require doeff-hy.macros [deftest])
(import json)
(import os)
(import shutil)
(import subprocess)
(import sys)
(import tempfile)
(import pathlib [Path])
(import doeff_cluster.worker.protocol.env_translation [PROBE-PROGRAM CHILD-PROTOCOL-PLACES])

(setv HY (str (/ (. (Path sys.executable) parent) "hy"))
      ;; 置き場ごとの版の値(本物の CHILD-PROTOCOL の 1 と重ならない値)。
      NEW-PLACE-PROTOCOL 9
      OLD-PLACE-PROTOCOL 7)


(defn #^ int probed-protocol [#^ (| str None) place #^ int value]  ; defk にできない: 本物の子 process と一時の dir の寿命を持つ検の筋書き(Program の外)
  "place(None = 置かない)の module に CHILD_PROTOCOL = value を置いた doeff_cluster の最小の root を作り、本物の hy で PROBE-PROGRAM を
   撃って、probe が読んだ約束の版を返す。"
  (setv root (Path (tempfile.mkdtemp :prefix "probe-root-"))
        work (Path (tempfile.mkdtemp :prefix "probe-cwd-")))
  (try
    (setv package (/ root "doeff_cluster"))
    (.mkdir package)
    (.write-text (/ package "__init__.py") "")
    (when place
      (setv parts (cut (.split place ".") 1 None)
            directory package)
      (for [part (cut parts 0 -1)]
        (setv directory (/ directory part))
        (.mkdir directory :exist-ok True)
        (.write-text (/ directory "__init__.py") ""))
      (.write-text (/ directory (+ (get parts -1) ".py")) (.format "CHILD_PROTOCOL = {}\n" value)))
    (setv done (subprocess.run [HY "-c" PROBE-PROGRAM (str root)] :cwd (str work) :capture-output True :text True :timeout 120
                               :env (| (dict os.environ) {"PYTHONPATH" (str root)})))
    (assert (= done.returncode 0) done.stderr)
    (get (json.loads (get (.splitlines (.strip done.stdout)) -1)) "childProtocol")
    (finally
      (shutil.rmtree root :ignore-errors True)
      (shutil.rmtree work :ignore-errors True))))


(deftest test-the-probe-reads-the-new-place
  ;; c56fa8634 以降の doeff の root(約束の版は shared/intent の runtime_env_model)。
  (assert (= (get CHILD-PROTOCOL-PLACES 0) "doeff_cluster.shared.intent.runtime_env_model") CHILD-PROTOCOL-PLACES)
  (assert (= (probed-protocol (get CHILD-PROTOCOL-PLACES 0) NEW-PLACE-PROTOCOL) NEW-PLACE-PROTOCOL)))


(deftest test-the-probe-reads-the-old-place
  ;; c56fa8634 より前の doeff で宣言した root(約束の版は doeff_cluster.runtime_env_model)— 今動いている service の root。
  (assert (= (get CHILD-PROTOCOL-PLACES 1) "doeff_cluster.runtime_env_model") CHILD-PROTOCOL-PLACES)
  (assert (= (probed-protocol (get CHILD-PROTOCOL-PLACES 1) OLD-PLACE-PROTOCOL) OLD-PLACE-PROTOCOL)))


(deftest test-a-root-without-the-protocol-reads-zero
  ;; 失敗ケース: どちらの置き場にも約束の版が無い root は 0(worker は env-failed にする)。片方の置き場だけを読む probe では、上の 2 本の
  ;; どちらかがこれと同じ 0 になる。
  (assert (= (probed-protocol None 0) 0)))
