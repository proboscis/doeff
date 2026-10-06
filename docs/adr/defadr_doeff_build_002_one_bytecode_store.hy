;;; Executable ADR: bytecode(.pyc の中身の code と、閉包の歩みが読む import の名)を引く・足す保存先は 1 つ — doeff-hy の
;;; doeff_hy_bytecode_guard/code_store.py。鍵は source の中身と、中身の読みを変える物(Hy の source は module 名と Hy の版・Python の版の印・
;;; 最適化の段)だけで、版(commit)と木の path に依らない。macro の出所の中身は鍵に入れず、当たった code の記録を今の環境の macro の file と
;;; 照らす(合わなければ焼き直して同じ鍵の entry を書き直す)。
;;;
;;; 出自 = 利用者の 2026-10-07 の原文 2 通(下の fact)と、#3858 の決定(cisco-c8・2026-10-07 01:5x — 実行環境の用意を速くする側で直す・
;;; worker の bytecode の保存を、版をまたいで中身で引ける形にする)。
;;;
;;; 戻し方: この ADR を足した commit を revert する(ADR の file 1 つと、worker の bytecode の準備が前の root からの引き継ぎに戻る)。保存先の
;;; 中身は消してよい物(無ければ作り直すだけ)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk val var <-])
(import doeff [run])
(import re)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; 台帳

;; 保存先の入口(並び・鍵・使った印・壊れた entry の扱いの定義点)。
(val ENTRY "packages/doeff-hy/src/doeff_hy_bytecode_guard/code_store.py")

;; worker の bytecode の準備の道具(入口を worker の版の file として path で読む所)と、起動の script(保存先の既定を置く所)。
(val PREPARE-TOOL "packages/doeff-cluster/src/doeff_cluster/worker/entry/code_prepare.hy")
(val POOL-TOOL "packages/doeff-cluster/src/doeff_cluster/foundation/bytecode_pool.hy")
(val BOOT-SCRIPT "packages/doeff-cluster/deploy/boot.sh")
(val UPKEEP "packages/doeff-cluster/src/doeff_cluster/worker/core/env_upkeep.hy")

;; 保存先の entry を綴る形の目印(種類の末尾 .code・.imports の文字列 — 入口の外にこの綴りが在れば 2 つ目の保存先)。doeff-hy-check の
;; 展開の cache(doeff_hy/static_cache — 型検査の展開・.json と .txt)は bytecode の保存先ではないので当たらない。
(val ENTRY-SUFFIX (re.compile r"[\"']\.(?:code|imports)[\"']"))
;; 前の版の木から .pyc を引き継ぐ形(古い保存の形 — 道具の引数 --from / --changed)の目印。
(val CARRY-ARGUMENT (re.compile r"\"--(?:from|changed)\""))

;; 走査で降りない dir(作業木・venv・生成物)と、検と文書の置き場。
(val SCAN-SKIP-PARTS #{".git" ".venv" ".worktrees" "node_modules" "target" "__pycache__" "tests" "conformance"})
(val SCAN-SKIP-PREFIXES #("docs/" "specs/" "notes/" ".github/"))


;; ---------------------------------------------------------------------------
;; 判断(純関数)

(defk spells-store-layout [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "source が保存先の entry(種類の末尾 .code・.imports)を自分で綴るか(; と # で始まる註の行は数えない)。"
  (val code (.join "\n" (gfor line (.splitlines text) :if (not (.startswith (.lstrip line) #(";" "#"))) line)))
  (bool (.search ENTRY-SUFFIX code)))


(defk layout-violations [found]
  {:pre [(: found frozenset)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "保存先の entry を綴る file の集合 found のうち、入口でない物(2 つ目の保存先)と、入口が綴りを持たない事を挙げる。"
  (+ (lfor rel (sorted found) :if (!= rel ENTRY)
           f"{rel}: bytecode の保存先の entry を自分で綴る 2 つ目の保存先 — {ENTRY} の entry-path・CODE-SUFFIX・IMPORTS-SUFFIX を使う")
     (if (in ENTRY found) [] [f"{ENTRY}: 入口が保存先の並びを持たない(入口が消えた)"])))


(defk carries-from-a-previous-tree [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "bytecode の準備の道具の source が、前の版の木から引き継ぐ引数(--from・--changed)を持つか(; で始まる註の行は数えない)。"
  (val code (.join "\n" (gfor line (.splitlines text) :if (not (.startswith (.lstrip line) ";")) line)))
  (bool (.search CARRY-ARGUMENT code)))


;; ---------------------------------------------------------------------------
;; 土台(file を読む)

(defk store-layout-files [repo-root]
  {:pre [(: repo-root Path)] :post [(: % frozenset)] :tags {:context "doeff-build-adr" :role "foundation"}}
  "repo の中で保存先の entry を綴る Python と Hy の file の相対 path を集める(検・文書・作業木・venv を除く)。"
  (var found #{})
  (for [p (sorted (.rglob repo-root "*"))
        :setv rel (.relative-to p repo-root)
        :if (and (in p.suffix #(".py" ".hy"))
                 (not (& (set rel.parts) SCAN-SKIP-PARTS))
                 (not (.startswith (.as-posix rel) SCAN-SKIP-PREFIXES))
                 (not (.is-symlink p))
                 (.is-file p))]
    (<- hit bool (spells-store-layout (.read-text p :encoding "utf-8" :errors "replace")))
    (when hit
      (:= found (| found #{(.as-posix rel)}))))
  (frozenset found))


(defadr ADR-DOE-BUILD-002
  :title "bytecode を引く・足す保存先は doeff-hy の code_store 1 つ。鍵は source の中身と中身の読みを変える物だけで版に依らず、macro の出所は当たった code の記録で照らす。worker の bytecode の準備は前の版の木から引き継がず、保存先から書いて中身の変わった物だけを焼く"
  :status "accepted"
  :scope ["packages/doeff-hy/src/doeff_hy_bytecode_guard/code_store.py"
          "packages/doeff-hy/src/doeff_hy_bytecode_guard/loader_hooks.py"
          "packages/doeff-cluster/src/doeff_cluster/worker/entry/code_prepare.hy"
          "packages/doeff-cluster/src/doeff_cluster/foundation/bytecode_pool.hy"
          "packages/doeff-cluster/src/doeff_cluster/worker/core/bake_plan.hy"
          "packages/doeff-cluster/deploy/boot.sh"
          "docs/adr/defadr_doeff_build_002_one_bytecode_store.hy"]
  :problem
    [(fact
       "利用者の原文 2026-10-07: 「つまり結局のところ、full testは4時間に1回までとし、さらに１テストは30秒を超えてはならないってことを確実にしなければならないね」"
       :evidence "#3858 の本文")
     (fact
       "利用者の原文 2026-10-07 02:1x: 「imageを作るにしてもちゃんとMakefileやキャッシュレイヤーをつかって無駄にしないようにして」"
       :evidence "#3860 の本文")
     (fact
       "#3858 の決定(2026-10-07 01:5x・戻せる): test_same_program_daily.hy は実行環境の用意を速くする側で直す — worker の bytecode の保存を、版をまたいで中身で引ける形にする(版が替わっても、変わった file だけを焼き直す)。依存と写しの保存も日次の間で残す。失敗ケースの件は、テストの中の起こし直しの上限を短くする。"
       :evidence "#3858 の comment(決定)")
     (fact
       "測り(日次の Pod・#3858): test_same_program_daily.hy の正常の件 162 秒のうち 148.6 秒が実行環境の用意で、bytecode の準備 1,130 file が 94.7 秒。zeus の手元(nice 10)でも正常の件 153.8 秒のうち bytecode の処理ステージ 123.9 秒(閉包の歩み 61.3 秒・焼き 59.0 秒)。テストの worker は空の /work で起き、引き継ぎ元の root が無いので全部を焼いた。"
       :evidence "#3858 の調べ・手元の測りの完成マーカーの bytecode の欄")
     (fact
       "直す前の形: worker の準備は .pyc を root の木の中にだけ置き、次の版は完成済みの root の木から変わっていない file(git diff の外)の .pyc を hardlink で引き継いだ。閉包の歩みの import の名も、木の根に残す表を引き継ぎ元の木から使い回した。引き継ぎ元の選びは Python と Hy の版・同じ repo の完成済みの root に限るので、新しい worker・空の /work・root を消した後・テストの 1 台の cluster では全部を焼き直した。同じ中身の code の保存先は doeff-hy の import の口にだけ在り(DOEFF_HY_CODE_STORE)、準備の道具は使わなかった。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/worker/core/env_prepare.hy の carry-source(この変更の前の版)・worker/entry/code_prepare.hy の --from と --changed")]
  :context
    [(interpretation
       "版が替わっても中身の同じ source を焼き直さない事は、鍵が中身だけで決まる保存先を、準備の道具も import の口も同じ 1 つの入口で引く時だけ保証できる。前の木からの引き継ぎは、引き継ぎ元の root が在る時にだけ効き、在るかは worker の state と掃除に依る — 鍵を版や木に結ぶと、効かない場合(新しい worker・テスト)が必ず残る。")
     (interpretation
       "Hy の source の展開は macro の出所の中身に依るが、出所は compile するまで分からない。だから出所の中身は鍵に入れず、code の定数の末尾の記録(doeff-hy の records — 出所の module 名と中身の sha256)を、使う時に今の環境の import の路で引き直した file と照らす。合わなければ焼き直し、同じ鍵の entry を書き直す。Python の source の compile は module 名と Hy の版に依らないので、鍵は中身・Python の版の印・最適化の段だけ。")
     (interpretation
       "準備の道具は準備する root の venv の python で走り、root の版の doeff を import する。root の版の doeff-hy は保存先の入口を持たない版でありうるので、道具は worker の版の doeff-hy の code_store.py を path で読む(bake_plan と同じ置き方)。入口は標準 library だけを import する。")
     (interpretation
       "保存先の並びと掃除は native の wheel の保存先(ADR-DOE-BUILD-001)と同じ作法: <dir>/<鍵の頭>/<鍵の残り><種類>・使うたびに時刻を進める・7 日使われない物を worker の掃除が消す・書きは一時の file からの置き換え・壊れた entry は名指して除き作り直す。保存先の dir は env DOEFF_HY_CODE_STORE で、worker の起動の script が $WORK_DIR/state/doeff-hy-code-store を既定に置く(日次の全体検証の task と同じ dir)。")]
  :decision
    [(rule R1 "bytecode を引く・足す保存先は doeff-hy の code_store.py 1 つ。並び・鍵・使った印・壊れた entry の扱いはここだけが綴り、import の口(loader_hooks)と worker の準備の道具(code_prepare・bytecode_pool)はこの関数を呼ぶ。")
     (rule R2 "code の鍵 = source の中身・Python の版の印・最適化の段、Hy の source はさらに module 名と Hy の版。macro の出所の中身は、当たった code の記録を今の環境の file と照らして確かめ、合わなければ焼き直して同じ鍵の entry を書き直す。")
     (rule R3 "worker の bytecode の準備は前の版の木から .pyc も import の名も引き継がない(--from・--changed・引き継ぎ元の選びを持たない)。.pyc は保存先の code から書き、無い物と今の macro に合わない物だけを焼いて足す。閉包の import の名も source の中身の鍵で保存先から引く(種類 .imports)。")
     (rule R4 "保存先の dir は env DOEFF_HY_CODE_STORE。worker の起動の script は既定を $WORK_DIR/state/doeff-hy-code-store に置き、呼び手の値を優先する(手元の 1 台の cluster のテストは件をまたいで同じ dir を渡す)。")
     (rule R5 "7 日使われない entry は worker の掃除が消す(native の wheel と同じ 7 日 — CODE-STORE-UNUSED-SECONDS = WHEEL-UNUSED-SECONDS)。")]
  :laws
    [(law one-bytecode-store-entry
       :statement "for_all file f in doeff (excluding tests and docs): spells_store_layout(f) => f == ENTRY; and spells_store_layout(ENTRY)"
       :counterexamples
         [(counterexample "import の口(loader_hooks)が自分で os.path.join(store, key[:2], key[2:] + \".code\") を綴る(この変更の前の形 — 準備の道具が同じ保存先を引けなかった)")
          (counterexample "worker の道具が自前の dir に .pyc の cache を置く(3 つ目の保存先)")])
     (law the-prepare-tool-does-not-carry-from-a-previous-tree
       :statement "not carries_from_a_previous_tree(PREPARE-TOOL)"
       :counterexamples
         [(counterexample "道具の引数に --from <前の木> と --changed <差の一覧> が残る(引き継ぎ元の無い準備で全部を焼き直す道が残る)")])
     (law the-worker-defaults-the-store-under-its-state
       :statement "boot.sh exports DOEFF_HY_CODE_STORE defaulting to $WORK_DIR/state/doeff-hy-code-store, and the prepare tool reads the store entry from the worker's own doeff-hy"
       :counterexamples
         [(counterexample "起動の script が既定を置かず、worker の道具が利用者の HOME の cache(Pod では消える dir)へ書く")])]
  :enforcement
    [(deftest test-adr-doe-build-002-one-bytecode-store-entry
       (val repo-root (. (Path __file__) parent parent parent))
       (val found (run (store-layout-files repo-root)))
       (val violations (run (layout-violations found)))
       (assert (= violations []) (+ "bytecode の 2 つ目の保存先(ADR-DOE-BUILD-002 R1): " (str violations))))
     (deftest test-adr-doe-build-002-the-prepare-tool-does-not-carry-from-a-previous-tree
       (val repo-root (. (Path __file__) parent parent parent))
       (for [rel #(PREPARE-TOOL POOL-TOOL)]
         (assert (not (run (carries-from-a-previous-tree (.read-text (/ repo-root rel) :encoding "utf-8"))))
                 (+ rel ": 前の版の木から引き継ぐ引数が残る(ADR-DOE-BUILD-002 R3)"))))
     (deftest test-adr-doe-build-002-the-worker-defaults-the-store-under-its-state
       (val repo-root (. (Path __file__) parent parent parent))
       (val boot (.read-text (/ repo-root BOOT-SCRIPT) :encoding "utf-8"))
       (assert (in "export DOEFF_HY_CODE_STORE=\"${DOEFF_HY_CODE_STORE:-$WORK_DIR/state/doeff-hy-code-store}\"" boot)
               "起動の script が保存先の既定を worker の state の下に置かない(ADR-DOE-BUILD-002 R4)")
       (val tool (.read-text (/ repo-root PREPARE-TOOL) :encoding "utf-8"))
       (assert (in "\"doeff-hy\" \"src\" \"doeff_hy_bytecode_guard\" \"code_store.py\"" tool)
               "準備の道具が worker の版の doeff-hy の保存先の入口を読まない(ADR-DOE-BUILD-002 R1)")
       (val upkeep (.read-text (/ repo-root UPKEEP) :encoding "utf-8"))
       (assert (in "(val CODE-STORE-UNUSED-SECONDS WHEEL-UNUSED-SECONDS)" upkeep)
               "保存先の掃除の日数が native の wheel と同じでない(ADR-DOE-BUILD-002 R5)"))
     (deftest test-adr-doe-build-002-judgments-reject-the-counterexamples
       ;; 反例: 入口の外で entry を綴る形(Python・Hy の .code と .imports の末尾)は 2 つ目の保存先として赤。註の行の綴りは数えない。
       ;; 前の版の木から引き継ぐ引数は赤。
       (assert (run (spells-store-layout "entry = os.path.join(store, key[:2], key[2:] + '.code')\n")))
       (assert (run (spells-store-layout "(val path (posixpath.join store (cut key 0 2) (+ (cut key 2 None) \".imports\")))\n")))
       (assert (not (run (spells-store-layout "# \".code\" の entry は code_store が綴る\n"))))
       (assert (not (run (spells-store-layout "return cache_dir / key[:2] / f\"{key}.json\"\n"))))
       (val second "packages/doeff-cluster/src/doeff_cluster/worker/core/pyc_cache.hy")
       (assert (= (len (run (layout-violations (frozenset #{ENTRY second})))) 1))
       (assert (= (run (layout-violations (frozenset #{ENTRY}))) []))
       (assert (= (len (run (layout-violations (frozenset #{second})))) 2))
       (assert (run (carries-from-a-previous-tree "(.add-argument parser \"--from\" :action \"append\")\n")))
       (assert (not (run (carries-from-a-previous-tree ";; 前は \"--from\" で引き継いだ\n")))))]
  :plans [])
