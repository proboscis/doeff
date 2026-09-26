;; 自己起動の後、起動の script を宣言した doeff の commit の物へ引き継ぐ(E13)ことの検。
;;
;; image に焼いた起動の script は最初の自己起動(root を用意するまで)だけを受け持ち、その後は root の中の
;; packages/doeff-cluster/deploy/boot.sh へ exec する。だから起動の script を直しても、WORKER_DOEFF_COMMIT を変えて入れ替えれば
;; 新しい script で起き、image を作り直さない。引き継いだ先では 2 度目の引き継ぎをしない(同じ script を回り続けない)。
;; 検は準備済みの root(完成の印を持つ)を tmp に作り、root の script が自分の名乗りを出して終わる形で確かめる。
(require doeff-hy.macros [deftest defk <- val])
(import os)
(import subprocess)
(import pathlib [Path])

(val BOOT-SH (str (/ (. (Path __file__) (resolve) parent parent) "deploy" "boot.sh")))


(defk git [cwd #* args]
  {:pre [(: cwd Path)] :post [(: % str)]}
  "tmp の repo で git を 1 回撃つ(検の root の材料を作るため)。"
  (. (subprocess.run ["git" "-C" (str cwd) #* args] :capture-output True :text True :check True) stdout))


(defk prepared-root [tmp script]
  {:pre [(: tmp Path) (: script str)] :post [(: % tuple)]}
  "commit 1 つの doeff の bare mirror と、その commit の準備済みの root(root の起動の script = script)を作る。答え = #(mirror sha)。"
  (val src (/ tmp "src"))
  (.mkdir src)
  (<- (git src "init" "-q"))
  (.write-text (/ src "README") "x\n")
  (<- (git src "add" "README"))
  (<- (git src "-c" "user.email=t@t" "-c" "user.name=t" "commit" "-q" "-m" "x"))
  (<- head str (git src "rev-parse" "HEAD"))
  (val sha (.strip head))
  (val boot (/ tmp "work" "boot"))
  (.mkdir boot :parents True)
  (<- (git tmp "clone" "-q" "--bare" (str src) (str (/ boot "doeff.git"))))
  (val deploy (/ boot "roots" sha "packages" "doeff-cluster" "deploy"))
  (.mkdir deploy :parents True)
  (.write-text (/ deploy "boot.sh") script)
  (.touch (/ boot "roots" sha ".doeff-boot-ready"))
  #((str src) sha))


(defk boot [tmp url sha role [extra None]]
  {:pre [(: tmp Path) (: url str) (: sha str) (: role str) (: extra (| dict None))]
   :post [(: % subprocess.CompletedProcess)]}
  "image の起動の script を、準備済みの root を指して起こす。"
  (subprocess.run ["sh" BOOT-SH]
                  ;; PATH は OS の道具だけ(検の venv の hy を見せない — 役の起動へ進めば hy が無く落ちる)。
                  :env {"PATH" "/usr/bin:/bin" "HOME" (str tmp) "ROLE" role "WORK_DIR" (str (/ tmp "work"))
                        "WORKER_DOEFF_COMMIT" sha "WORKER_DOEFF_URL" url #** (or extra {})}
                  :capture-output True :text True :timeout 60))


(deftest test-the-image-script-hands-over-to-the-root-script [tmp-path]
  (<- made tuple (prepared-root tmp-path "echo \"root の script: $ROLE $DOEFF_BOOT_FROM_ROOT\"\n"))
  (<- done subprocess.CompletedProcess (boot tmp-path (get made 0) (get made 1) "records"))
  (assert (= done.returncode 0) done.stderr)
  (assert (in "root の script: records 1" done.stdout) (+ done.stdout done.stderr)))


(deftest test-the-root-script-is-not-handed-over-again [tmp-path]
  ;; 引き継いだ先(DOEFF_BOOT_FROM_ROOT が立っている)ではもう 1 度引き継がず、そのまま役の起動へ進む。
  (<- made tuple (prepared-root tmp-path "echo \"root の script\"\n"))
  (<- done subprocess.CompletedProcess (boot tmp-path (get made 0) (get made 1) "records" {"DOEFF_BOOT_FROM_ROOT" "1"}))
  (assert (not-in "root の script" done.stdout) done.stdout)
  (assert (!= done.returncode 0) "検の root には hy が無いので、役の起動へ進めば落ちる"))
