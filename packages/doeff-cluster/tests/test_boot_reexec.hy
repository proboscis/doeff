;; 自己起動の後、起動の script を宣言した doeff の commit の物へ引き継ぐ(E13)ことの検。
;;
;; image に焼いた起動の script は最初の自己起動(root を用意するまで)だけを受け持ち、その後は root の中の
;; packages/doeff-cluster/deploy/boot.sh へ exec する。だから起動の script を直しても、WORKER_DOEFF_COMMIT を変えて入れ替えれば
;; 新しい script で起き、image を作り直さない。引き継いだ先では 2 度目の引き継ぎをしない(同じ script を回り続けない)。
;; 検は準備済みの root(完成の印を持つ)を tmp に作り、root の script が自分の名乗りを出して終わる形で確かめる。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import os)
(import subprocess)
(import pathlib [Path])

(val BOOT-SH (str (/ (. (Path __file__) (resolve) parent parent) "deploy" "boot.sh")))


(defk git [cwd #* args]
  {:pre [(: cwd Path) (: args tuple)] :post [(: % str)]}
  "tmp の repo で git を 1 回撃つ(検の root の材料を作るため)。"
  (val words (lfor a args :if (isinstance a str) a))
  (assert (= (len words) (len args)) #("子 process の引数は文字列だけ" args))
  (. (subprocess.run ["git" "-C" (str cwd) #* words] :capture-output True :text True :check True) stdout))


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


(deftest test-the-prepare-role-prepares-the-root-and-starts-nothing [tmp-path]
  ;; 役 prepare は展開と準備だけで終わる(#3725)— 版上げの前に、今の worker の Pod の中で上げ先の版の root を
  ;; 先に組むため。root の script は本物(引き継いだ先がこの役を受け持つ)。検の PATH に hy は無いので、役の起動へ落ちれば非 0 になる。
  (<- made tuple (prepared-root tmp-path (.read-text (Path BOOT-SH))))
  (<- done subprocess.CompletedProcess (boot tmp-path (get made 0) (get made 1) "prepare"))
  (assert (= done.returncode 0) done.stderr)
  (assert (= (.strip done.stdout) (str (/ tmp-path "work" "boot" "roots" (get made 1)))) done.stdout)
  (assert (in "準備済み" done.stderr) done.stderr))


(deftest test-an-unknown-role-is-refused-before-anything-is-extracted [tmp-path]
  ;; 失敗ケース: 知らない役を worker の起動へ落とすと、走っている worker の Pod の中で 2 つ目の worker が起きる。名指しで断り、
  ;; 展開もしない(mirror の dir を作らない)。
  (<- done subprocess.CompletedProcess (boot tmp-path (str (/ tmp-path "no-such-repo")) (* "0" 40) "prepair"))
  (assert (= done.returncode 2) (+ done.stdout done.stderr))
  (assert (in "知らない役 ROLE=prepair" done.stderr) done.stderr)
  (assert (not (.exists (/ tmp-path "work" "boot"))) "断る前に展開の dir を作った"))


(deftest test-the-prepare-role-needs-the-commit-to-prepare [tmp-path]
  ;; 失敗ケース: 準備する commit を渡さない prepare は、何もせずに成功したように終わらない。
  (val done (subprocess.run ["sh" BOOT-SH]
                            :env {"PATH" "/usr/bin:/bin" "HOME" (str tmp-path) "ROLE" "prepare" "WORK_DIR" (str (/ tmp-path "work"))}
                            :capture-output True :text True :timeout 60))
  (assert (= done.returncode 2) (+ done.stdout done.stderr))
  (assert (in "WORKER_DOEFF_COMMIT" done.stderr) done.stderr))
