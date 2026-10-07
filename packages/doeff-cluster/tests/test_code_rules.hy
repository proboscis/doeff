;; 版ごとのコードの木の準備の script を組む判断 prepare-script(worker/core/code_rules — #2466)を、sh を走らせずに確かめる。
;;   * どの命令も set -eu の下の単独の文で、完成の印を確かめてから最後に rename する(final の在る dir は常に完成品)。
;;   * 焼きの hy が無ければ bytecode を省いて印だけを置く・前の版の木から引き継がない(.pyc は source の中身で引く保存先から書く — #3858)。
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster.worker.intent.worker_model [CodeLayout])
(import doeff_cluster.worker.core.code_plan [MARKER])
(import doeff_cluster.worker.core.code_rules [prepare-script])


(deftest test-the-script-checks-the-marker-before-the-rename-that-comes-last
  (<- script str (prepare-script "/repo" "rev9" :hy-command "/bin/hy" :tool "/w/code_prepare.hy" :layout (CodeLayout)))
  (val lines (.splitlines script))
  (assert (= (get lines 0) "set -eu") lines)
  (assert (= (get lines -1) "mv \"$T\" \"$F\"") lines)
  (assert (in (.format "test -f \"$T/{}\"" MARKER) script) script)
  (assert (in "git -C \"/repo\" archive --format=tar -o \"$T.tar\" \"rev9\"" script) script)
  (assert (in "PYTHONDONTWRITEBYTECODE=1 \"/bin/hy\" \"/w/code_prepare.hy\" --revision \"rev9\" --tree \"$T\" --roots \".\"" script) script))


(deftest test-the-script-does-not-carry-from-a-previous-tree
  ;; 失敗ケース(#3858): 版の木の準備は前の版の木から .pyc を引き継がない(引き継ぎ元の差の一覧も渡さない)— 引き継ぐ形に戻すと、引き継ぎ元の
  ;; 無い worker で全部を焼き直す道が残る。
  (<- script str (prepare-script "/repo" "rev9" :hy-command "/bin/hy" :tool "/w/code_prepare.hy" :layout (CodeLayout)))
  (assert (not-in "--from" script) script)
  (assert (not-in "--changed" script) script)
  (assert (not-in "diff --name-only" script) script)
  ;; 焼きの hy が無ければ bytecode を省き、印だけを置く(道具を起こさない)。
  (<- plain str (prepare-script "/repo" "rev9" :hy-command None :tool "/w/code_prepare.hy" :layout (CodeLayout)))
  (assert (not-in "code_prepare.hy" plain) plain)
  (assert (in "\"bytecode\": false" plain) plain))


(deftest test-the-script-passes-the-compile-jobs-to-the-tool-only-when-given
  ;; 失敗ケース(2026-10-08): 版の木の準備の焼く道具は、準備の action が並べる数を持つ時だけ `--jobs N` を受ける(recreate の job の旧い
  ;; process が動いている間の新しい版の準備 — 旧と同じ memory の上限を分け合う)。持たない時は道具の既定(cgroup の CPU の上限)のまま。
  ;; 直す前は並べる数を受けなかった。
  (<- given str (prepare-script "/repo" "rev9" :hy-command "/bin/hy" :tool "/w/code_prepare.hy" :layout (CodeLayout) :compile-jobs 2))
  (assert (in "--roots \".\" --jobs 2\n" given) given)
  (<- plain str (prepare-script "/repo" "rev9" :hy-command "/bin/hy" :tool "/w/code_prepare.hy" :layout (CodeLayout) :compile-jobs None))
  (assert (not-in "--jobs" plain) plain))
