;; git を起こさずに checkout の file から答える翻訳 checkout-memo と、その本物の答え手 checkout-files-reader の検。手元の bare repo と clone
;; (本物の git)を、本物の土台(subprocess-handler・os-file-handler)の上で読み、git の読み(checkout-reads だけの列)と照らす。
;;
;;   - 汚れていない・push 済みの checkout は、覚えた後の読みが git を 1 度も起こさず、git の読みと同じ答え(head・url・汚れ・remote に在るか)
;;   - 反例 3 つでは覚えた答えを使わず、git の読みと同じ答えになる: 作業木の変更・stage した変更・remote に無い commit
;;   - checkout の根(CheckoutRoot)と送り手の source の根(SenderSourceRoot)も git を起こさずに読み、git の答えと同じ
;;   - 模擬の答え手 scripted-checkout-files(git の台本と同じ世界)の上でも、覚えた後の読みは台本の git に問わない
(require doeff-hy.macros [deftest defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import os)
(import subprocess)
(import time)
(import pathlib [Path])
(import doeff [Program EffectBase with_handlers])
(import doeff_core_effects [state Get Put])
(import doeff_core_effects [MemoryFiles memory-file-handler])
(import doeff_core_effects [ProcessScript scripted-process-handler])
(import doeff_core_effects [RunProcess subprocess-handler os-file-handler])
(import doeff_cluster [CheckoutState ReadCheckout CheckoutRoot SenderSourceRoot SENDER-SOURCE-DIR])
(import doeff_cluster [checkout-reads checkout-memo checkout-files-reader scripted-checkout-files])
(import doeff_cluster [GitCheckout GitRemote git-command])

(val URL "https://example.invalid/app.git")
(val RUNS-KEY "git-runs")
;; 検の file の mtime を戻す秒(index を書く刻より確かに前にして、racy な項を作らない — 本物の checkout は書いてから時間が経っている)。
(val SETTLED-AGE 3600)


(defk git [cwd #* args]
  {:pre [(: cwd Path) (: args tuple)] :post [(: % str)]}
  "検の repo を作るために git を 1 回呼ぶため。"
  (val done (subprocess.run ["git" "-C" (str cwd) "-c" "user.name=t" "-c" "user.email=t@example.invalid" #* args]
                            :capture-output True :text True :check True))
  (.strip done.stdout))


(defk aged [work]
  {:pre [(: work Path)] :post [(: % int)]}
  "作業木の file の mtime を SETTLED-AGE 秒前へ戻すため(答え = 戻した file の数)。"
  (val then (- (time.time) SETTLED-AGE))
  (val files (tuple (gfor p (.rglob work "*") :if (and (.is-file p) (not-in ".git" p.parts)) p)))
  (for [p files]
    (os.utime p #(then then)))
  (len files))


(defk pushed-checkout [base]
  {:pre [(: base Path)] :post [(: % Path)]}
  "bare の remote と、2 file を push 済みの clone を作り、clone の path を返すため。push の後に origin の url を URL へ替え(追跡の ref は
   残る)、作業木の file を古くしてから index を読み直させる(git status が index を書き直す形を検の前に済ませる)。"
  (val remote (/ base "app.git"))
  (val work (/ base "app"))
  (<- (git base "init" "-q" "--bare" (str remote)))
  (<- (git base "clone" "-q" (str remote) (str work)))
  (.mkdir (/ work "pkg"))
  (.write-text (/ work "uv.lock") "httpx==0.28.1\n")
  (.write-text (/ work "pkg" "mod.py") "X = 1\n")
  (<- (aged work))
  (<- (git work "add" "-A"))
  (<- (git work "commit" "-q" "-m" "first"))
  (<- (git work "push" "-q" "origin" "HEAD:main"))
  (<- (git work "remote" "set-url" "origin" URL))
  (<- (git work "update-index" "--refresh"))
  work)


(defhandler counted-git
  ;; checkout の読みが起こした子 process の argv を外側の状態(RUNS-KEY の tuple)へ足し、そのまま外の答え手へ渡す。
  (RunProcess [argv]
    (<- runs tuple (Get RUNS-KEY))
    (<- (Put RUNS-KEY (+ runs #(argv))))
    (reperform effect)))


(defk read-counting [program remembered]
  {:pre [(: program (| Program EffectBase)) (: remembered bool)] :post [(: % tuple)]}
  "program を本物の土台の上で読み、#(答え・起こした git の argv の tuple)を返すため。remembered = 翻訳 checkout-memo を並べるか
   (False = git の読みだけ)。"
  (val translations (if remembered [checkout-reads checkout-memo] [checkout-reads]))
  (<- seen tuple ((state {RUNS-KEY #()})
                  (with_handlers (+ [subprocess-handler os-file-handler (checkout-files-reader) counted-git] translations)
                                 (answer-and-runs program))))
  seen)


(defk answer-and-runs [program]
  {:pre [(: program (| Program EffectBase))] :post [(: % tuple)]}
  "program の答えと、その間に起こした git の argv を並べるため。"
  (<- answer program)
  (<- runs tuple (Get RUNS-KEY))
  #(answer runs))


(defk agrees-with-git [work]
  {:pre [(: work Path)] :post [(: % tuple)]}
  "work の checkout を覚えた答えの列と git の読みだけの列で読み、#(覚えた列の答え・git の答え・覚えた列が起こした git の数)を返すため。"
  (<- remembered tuple (read-counting (ReadCheckout (str work) "origin") True))
  (<- plain tuple (read-counting (ReadCheckout (str work) "origin") False))
  #((get remembered 0) (get plain 0) (len (get remembered 1))))


(defk warmed [work]
  {:pre [(: work Path)] :post [(: % tuple)]}
  "work の checkout を 1 度読んで覚えさせた後の読みを、git の読みと照らすため(答えは agrees-with-git)。"
  (<- _ tuple (read-counting (ReadCheckout (str work) "origin") True))
  (<- seen tuple (agrees-with-git work))
  seen)


(deftest test-a-remembered-read-starts-no-git-and-agrees-with-git [tmp-path]
  (<- work Path (pushed-checkout tmp-path))
  (<- seen1 tuple (warmed work))
  (setv [remembered plain runs] seen1)
  (assert (= runs 0) runs)
  (assert (= remembered plain) #(remembered plain))
  (assert (= remembered (CheckoutState :head (! (git work "rev-parse" "HEAD")) :url URL :dirty False :on-remote True)) remembered))


(deftest test-a-changed-work-tree-is-read-by-git [tmp-path]
  (<- work Path (pushed-checkout tmp-path))
  (<- _ tuple (warmed work))
  (.write-text (/ work "pkg" "mod.py") "X = 2\n")
  (<- seen2 tuple (agrees-with-git work))
  (setv [remembered plain runs] seen2)
  (assert (> runs 0) runs)
  (assert (= remembered plain) #(remembered plain))
  (assert remembered.dirty remembered))


(deftest test-a-staged-change-is-read-by-git [tmp-path]
  (<- work Path (pushed-checkout tmp-path))
  (<- _ tuple (warmed work))
  ;; 作業木の file を index と同じ stat に戻しても、stage した変更は index の中身(checksum)を変えるので覚えた答えの鍵が合わない。
  (val path (/ work "pkg" "mod.py"))
  (.write-text path "X = 3\n")
  (<- (git work "add" "pkg/mod.py"))
  (<- (aged work))
  (<- (git work "update-index" "--refresh"))
  (<- seen3 tuple (agrees-with-git work))
  (setv [remembered plain runs] seen3)
  (assert (> runs 0) runs)
  (assert (= remembered plain) #(remembered plain))
  (assert remembered.dirty remembered))


(deftest test-a-commit-not-on-the-remote-is-read-by-git [tmp-path]
  (<- work Path (pushed-checkout tmp-path))
  (<- _ tuple (warmed work))
  (.write-text (/ work "extra.py") "Y = 1\n")
  (<- (aged work))
  (<- (git work "add" "-A"))
  (<- (git work "commit" "-q" "-m" "local only"))
  (<- (git work "update-index" "--refresh"))
  (<- seen4 tuple (agrees-with-git work))
  (setv [remembered plain runs] seen4)
  (assert (> runs 0) runs)
  (assert (= remembered plain) #(remembered plain))
  (assert (not remembered.on-remote) remembered)
  ;; 覚えた後も、remote に無いと読めた commit は覚えない(push すれば在ると読める)— 次の読みも git へ問う。
  (<- seen5 tuple (agrees-with-git work))
  (setv [again _ again-runs] seen5)
  (assert (> again-runs 0) again-runs)
  (assert (not again.on-remote) again))


(deftest test-checkout-roots-are-read-without-git [tmp-path]
  (<- work Path (pushed-checkout tmp-path))
  (<- seen6 tuple (read-counting (CheckoutRoot (str (/ work "pkg"))) True))
  (setv [root runs] seen6)
  (<- seen7 tuple (read-counting (CheckoutRoot (str (/ work "pkg"))) False))
  (setv [git-root _] seen7)
  (assert (= runs #()) runs)
  (assert (= root git-root (str (.resolve work))) #(root git-root))
  (<- seen8 tuple (read-counting (SenderSourceRoot) True))
  (setv [sender sender-runs] seen8)
  (<- seen9 tuple (read-counting (SenderSourceRoot) False))
  (setv [git-sender _] seen9)
  (assert (= sender-runs #()) sender-runs)
  (assert (= sender git-sender) #(sender git-sender SENDER-SOURCE-DIR)))


;; --- 模擬の答え手(git の台本と同じ世界)----------------------------------------------------------------------------

(defk scripted-runs [checkout]
  {:pre [(: checkout GitCheckout)] :post [(: % tuple)]}
  "台本の世界で checkout を 2 度読み、2 度目の #(答え・台本の git へ出した問い)を返すため(覚えた答えの置き場は memory の .git の dir)。"
  (val world #(checkout))
  (<- seen tuple ((state {RUNS-KEY #()})
                  (with_handlers [(memory-file-handler (MemoryFiles :dirs #("/src" checkout.path (+ checkout.path "/.git"))))
                                  (scripted-checkout-files world)
                                  (scripted-process-handler (ProcessScript :commands #((git-command world))))
                                  counted-git checkout-reads checkout-memo]
                                 (second-read checkout.path))))
  seen)


(defk second-read [path]
  {:pre [(: path str)] :post [(: % tuple)]}
  "ReadCheckout を 2 度出し、2 度目の答えと、2 度目に起こした git の問いを並べるため。"
  (<- _ CheckoutState (ReadCheckout path "origin"))
  (<- first-runs tuple (Get RUNS-KEY))
  (<- second CheckoutState (ReadCheckout path "origin"))
  (<- runs tuple (Get RUNS-KEY))
  #(second (cut runs (len first-runs) None)))


(deftest test-the-scripted-answerer-lets-a-second-read-skip-the-script []
  (val clean (GitCheckout :path "/src/app" :head (* "a" 40) :remotes #((GitRemote :name "origin" :url URL))
                          :pushed #("origin/main") :remote-head "origin/main"))
  (<- seen10 tuple (scripted-runs clean))
  (setv [seen runs] seen10)
  (assert (= runs #()) runs)
  (assert (= seen (CheckoutState :head (* "a" 40) :url URL :dirty False :on-remote True)) seen)
  ;; 汚れた checkout は覚えず、2 度目も台本の git へ問う。
  (<- seen11 tuple (scripted-runs (GitCheckout :path "/src/app" :head (* "a" 40) :remotes #((GitRemote :name "origin" :url URL))
                                               :pushed #("origin/main") :remote-head "origin/main" :dirty True)))
  (setv [dirty dirty-runs] seen11)
  (assert dirty-runs dirty-runs)
  (assert dirty.dirty dirty))
