;;; worker の版ごとのコードの木の準備の判断 — 展開・bytecode の準備・完成の印の確かめ・rename を 1 本の sh の script に組む
;;; (handlers.hy の CodeStore.script から分けた・#2466)。I/O は呼び手(CodeStore — 後に worker/protocol の言い換え)が行う。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff_cluster.worker.intent.worker_model [CodeLayout])
(import doeff_cluster.worker.core.code_plan [MARKER MARKER-FORMAT])


(defk prepare-script [repo revision previous * hy-command tool layout]
  {:pre [(: repo str) (: revision str) (: previous (| str None)) (: hy-command (| str None)) (: tool str) (: layout CodeLayout)]
   :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "版 1 つの木を準備する sh の script を組むため: 展開 → bytecode の準備(木の中だけ・実行時に検める方式・前の版から引き継ぐ・検めて完成の
   印を置く)→ rename。previous = 引き継ぎ元の木の path(None = 引き継がない)・hy-command = 焼きの hy(None = bytecode を省き、印だけを置く)・
   tool = 焼く道具の file(worker 自身のコードの code_prepare.hy)。script は環境変数 T(作る木)・F(完成品の置き場)・B(脇へ退けた木 — 空なら無し)を読む。
   どの命令も単独の文にして set -e を効かせる(`a && b` の a の失敗は set -e が拾わない — 以前はそれで焼きの失敗が完成品になった)。
   git archive は pipe にせず file へ書く(pipe の失敗は最後の tar しか見えない)。rename が最後で、その前に印が在ることを確かめるので、
   final の在る dir は常に完成品。焼く道具そのもの(Hy)の import が timestamp 方式の .pyc を木へ書かないよう、PYTHONDONTWRITEBYTECODE を
   立てる。revision = worker_model.code-key = 1 つの commit。前の木から引き継ぐ時の「変わった file」は前の木の名(版)と revision の
   git diff。前の木の版をこの repo で解けない時は引き継がずに全部を焼く — 引き継ぎは速さのためだけで、引き継げないことを準備の失敗に
   しない(set -e は if の条件の失敗を拾わない)。"
  (val prepare-tool (+ f"PYTHONDONTWRITEBYTECODE=1 \"{hy-command}\" \"{tool}\" \"$T\" --revision \"{revision}\""
                       f" --import-roots \"{(.roots-arg layout)}\""))
  (val previous-name (if (is previous None) None (get (.split (.rstrip previous "/") "/") -1)))
  (val prepare (cond
                 (not hy-command)
                   (+ f"printf '{{\"format\": {MARKER-FORMAT}, \"revision\": \"%s\", \"bytecode\": false}}\\n' "
                      f"\"{revision}\" > \"$T/{MARKER}\"\n")
                 (is previous None) f"{prepare-tool}\n"
                 True
                   (+ f"if git -C \"{repo}\" diff --name-only \"{previous-name}\" \"{revision}\" > \"$T.changed\" 2>/dev/null; then\n"
                      f"  {prepare-tool} --from \"{previous}\" --changed \"$T.changed\"\n"
                      "else\n"
                      f"  echo \"前の木 {previous-name} の版をこの repo で解けない — 引き継がずに全部を焼く\" >&2\n"
                      f"  {prepare-tool}\n"
                      "fi\n")))
  (+ "set -eu\n"
     "if [ -n \"$B\" ]; then rm -rf \"$B\"; fi\n"
     "rm -rf \"$T\" \"$T.tar\" \"$T.changed\"\n"
     "mkdir -p \"$T\"\n"
     ;; 手元に無い版なら先に fetch する(Pod の mirror は起動時の版しか持たない)。
     f"if ! git -C \"{repo}\" cat-file -e \"{revision}^{{commit}}\" 2>/dev/null; then\n"
     f"  git -C \"{repo}\" fetch -q origin '+refs/heads/*:refs/heads/*'\n"
     "fi\n"
     f"git -C \"{repo}\" archive --format=tar -o \"$T.tar\" \"{revision}\"\n"
     "tar -x -C \"$T\" -f \"$T.tar\"\n"
     "rm -f \"$T.tar\"\n"
     "cd \"$T\"\n"
     prepare
     f"test -f \"$T/{MARKER}\" || {{ echo \"完成の印が置かれていない\" >&2; exit 1; }}\n"
     "rm -f \"$T.changed\"\n"
     "mv \"$T\" \"$F\"\n"))
