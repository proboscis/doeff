;; 版ごとのコードの木の準備の script を組む判断 prepare-script(worker/core/code_rules — #2466)を、sh を走らせずに確かめる。
;;   * どの命令も set -eu の下の単独の文で、完成の印を確かめてから最後に rename する(final の在る dir は常に完成品)。
;;   * 焼きの hy が無ければ bytecode を省いて印だけを置く・前の木が在れば git diff の変わった file で引き継ぎ、解けなければ全部を焼く。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster.worker.intent.worker_model [CodeLayout])
(import doeff_cluster.worker.core.code_plan [MARKER])
(import doeff_cluster.worker.core.code_rules [prepare-script])


(deftest test-the-script-checks-the-marker-before-the-rename-that-comes-last
  (<- script str (prepare-script "/repo" "rev9" None :hy-command "/bin/hy" :tool "/w/code_prepare.hy" :layout (CodeLayout)))
  (val lines (.splitlines script))
  (assert (= (get lines 0) "set -eu") lines)
  (assert (= (get lines -1) "mv \"$T\" \"$F\"") lines)
  (assert (in (.format "test -f \"$T/{}\"" MARKER) script) script)
  (assert (in "git -C \"/repo\" archive --format=tar -o \"$T.tar\" \"rev9\"" script) script)
  (assert (in "PYTHONDONTWRITEBYTECODE=1 \"/bin/hy\" \"/w/code_prepare.hy\" \"$T\" --revision \"rev9\" --import-roots \".\"" script) script))


(deftest test-the-script-carries-the-previous-tree-and-falls-back-to-a-full-bake
  (<- carried str (prepare-script "/repo" "rev9" "/cache/rev8" :hy-command "/bin/hy" :tool "/w/code_prepare.hy" :layout (CodeLayout)))
  (assert (in "diff --name-only \"rev8\" \"rev9\"" carried) carried)
  (assert (in "--from \"/cache/rev8\" --changed \"$T.changed\"" carried) carried)
  (assert (in "引き継がずに全部を焼く" carried) carried)
  ;; 焼きの hy が無ければ bytecode を省き、印だけを置く(道具を起こさない)。
  (<- plain str (prepare-script "/repo" "rev9" "/cache/rev8" :hy-command None :tool "/w/code_prepare.hy" :layout (CodeLayout)))
  (assert (not-in "code_prepare.hy" plain) plain)
  (assert (in "\"bytecode\": false" plain) plain))
