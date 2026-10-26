;;; Executable ADR: bytecode(.pyc の中身の code と、閉包の歩みが読む import の名)を引く・足す保存先は 1 つ — doeff-hy の
;;; doeff_hy_bytecode_guard/code_store.py。鍵は source の中身と、中身の読みを変える物(Hy の source は module 名と Hy の版・Python の版の印・
;;; 最適化の段)だけで、版(commit)と木の path に依らない。macro の出所の中身は鍵に入れず、当たった code の記録を今の環境の macro の file と
;;; 照らす(合わなければ焼き直す)。鍵 1 つには macro の環境ごとの entry が並び、焼き直した code は今の環境の entry として足す(R2b — 他の
;;; 環境の entry を上書きしない)。
;;;
;;; 出自 = 利用者の 2026-10-07 の指示 2 つ(下の fact)と、#3858 の決定(cisco-c8・2026-10-07 01:5x — 実行環境の用意を速くする側で直す・
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

;; .pyc の置き場の知識(__pycache__ の dir の名・.pyc の末尾・source から .pyc の path を求める関数)の目印。doeff-cluster の中でこれを綴って
;; よいのは焼く道具の module と模擬の世界だけ(R3b — 木を置く翻訳や準備の program が .pyc を置くと、前の root からの 2 本目の引き継ぎになる)。
(val PLACEMENT-TOKEN (re.compile r"[\"']__pycache__[\"']|[\"']\.pyc[\"']|\bpyc[-_]path\b|\bcache[-_]from[-_]source\b"))
(val CLUSTER-SRC "packages/doeff-cluster/src/doeff_cluster")
(val BYTECODE-TOOL-FILES #{"packages/doeff-cluster/src/doeff_cluster/foundation/bytecode_pool.hy"
                           "packages/doeff-cluster/src/doeff_cluster/worker/entry/code_prepare.hy"
                           "packages/doeff-cluster/src/doeff_cluster/worker/core/code_prepare.hy"
                           "packages/doeff-cluster/src/doeff_cluster/worker/core/code_plan.hy"
                           "packages/doeff-cluster/src/doeff_cluster/worker/core/bake_plan.hy"
                           "packages/doeff-cluster/src/doeff_cluster/worker/intent/code_model.hy"
                           "packages/doeff-cluster/src/doeff_cluster/sim/env_world.hy"})
;; 走査で降りない dir(作業木・venv・生成物)と、検と文書の置き場。
(val SCAN-SKIP-PARTS #{".git" ".venv" ".worktrees" "node_modules" "target" "__pycache__" "tests" "conformance"})
(val SCAN-SKIP-PREFIXES #("docs/" ".github/"))


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


(defk spells-bytecode-placement [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "source が .pyc の置き場の知識(PLACEMENT-TOKEN)を綴るか(; と # で始まる註の行は数えない)— R3b の判断。"
  (val code (.join "\n" (gfor line (.splitlines text) :if (not (.startswith (.lstrip line) #(";" "#"))) line)))
  (bool (.search PLACEMENT-TOKEN code)))


(defk placement-violations [found]
  {:pre [(: found frozenset)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "doeff-cluster の中で .pyc の置き場を綴る file の集合 found のうち、焼く道具の module と模擬の世界(BYTECODE-TOOL-FILES)でない物を挙げる。"
  (lfor rel (sorted found) :if (not-in rel BYTECODE-TOOL-FILES)
        f"{rel}: .pyc の置き場(__pycache__・.pyc・pyc-path)を綴る — .pyc を root に置くのは焼く道具だけ(code_store の link-pyc 経由)。前の root から .pyc を持ち越す 2 本目の道になる"))

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


(defk placement-files [repo-root]
  {:pre [(: repo-root Path)] :post [(: % frozenset)] :tags {:context "doeff-build-adr" :role "foundation"}}
  "doeff-cluster の source(CLUSTER-SRC の下の Python と Hy)のうち、.pyc の置き場の知識を綴る file の相対 path を集める。"
  (var found #{})
  (for [p (sorted (.rglob (/ repo-root CLUSTER-SRC) "*"))
        :if (and (in p.suffix #(".py" ".hy")) (not (.is-symlink p)) (.is-file p) (not-in "__pycache__" p.parts))]
    (<- hit bool (spells-bytecode-placement (.read-text p :encoding "utf-8" :errors "replace")))
    (when hit
      (:= found (| found #{(.as-posix (.relative-to p repo-root))}))))
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
       "利用者の決定 2026-10-07: full test は 4 時間に 1 回までとし、1 つのテストは 30 秒を超えない事を確実にする。"
       :evidence "#3858 の本文")
     (fact
       "利用者の指示 2026-10-07: image を作る時も Makefile と cache の層を使い、組み直しの無駄を出さない。"
       :evidence "#3860 の本文")
     (fact
       "#3858 の決定(2026-10-07 01:5x・戻せる): test_same_program_daily.hy は実行環境の用意を速くする側で直す — worker の bytecode の保存を、版をまたいで中身で引ける形にする(版が替わっても、変わった file だけを焼き直す)。依存と写しの保存も日次の間で残す。失敗ケースの件は、テストの中の起こし直しの上限を短くする。"
       :evidence "#3858 の comment(決定)")
     (fact
       "測り(日次の Pod・#3858): test_same_program_daily.hy の正常の件 162 秒のうち 148.6 秒が実行環境の用意で、bytecode の準備 1,130 file が 94.7 秒。zeus の手元(nice 10)でも正常の件 153.8 秒のうち bytecode の処理ステージ 123.9 秒(閉包の歩み 61.3 秒・焼き 59.0 秒)。テストの worker は空の /work で起き、引き継ぎ元の root が無いので全部を焼いた。"
       :evidence "#3858 の調べ・手元の測りの完成マーカーの bytecode の欄")
     (fact
       "直す前の形: worker の準備は .pyc を root の木の中にだけ置き、次の版は完成済みの root の木から変わっていない file(git diff の外)の .pyc を hardlink で引き継いだ。閉包の歩みの import の名も、木の根に残す表を引き継ぎ元の木から使い回した。引き継ぎ元の選びは Python と Hy の版・同じ repo の完成済みの root に限るので、新しい worker・空の /work・root を消した後・テストの 1 台の cluster では全部を焼き直した。同じ中身の code の保存先は doeff-hy の import の口にだけ在り(DOEFF_HY_CODE_STORE)、準備の道具は使わなかった。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/worker/core/env_prepare.hy の carry-source(この変更の前の版)・worker/entry/code_prepare.hy の --from と --changed")
     (fact
       "測り(2026-10-12 09:0x JST・zeus・card ki-6828e9af04b4 — 調べは card ki-4c2f5878e524): 実行環境の準備は同じ版の source でも 1 回に 100〜600 本の Hy の bytecode を焼き直した(02:53 JST より前は 0〜10 本・bytecode の段 10〜40 秒 → 後は 30〜600 本・40〜550 秒。06 時台の 1 時間は 6 回で 2,763 本・1,366 秒)。鍵 1 つに entry 1 つの並びで、準備(worker の道具 — 記録の印 6)と作業木の import(DOEFF_HY_CODE_STORE を worker から継ぐ・doeff の版は作業木ごとに違う — 記録の印 5 など)が、同じ鍵の entry を互いに古いと見て焼き直し、上書きし合った。最新の root の Hy の source 1,094 本の entry を、その root の環境で照らすと、使える 797・記録の印 5 で使えない 295(どれも作業木が書いた)・digest が合わない 2。"
       :evidence "card ki-6828e9af04b4 の本文(所見)")]
  :context
    [(interpretation
       "版が替わっても中身の同じ source を焼き直さない事は、鍵が中身だけで決まる保存先を、準備の道具も import の口も同じ 1 つの入口で引く時だけ保証できる。前の木からの引き継ぎは、引き継ぎ元の root が在る時にだけ効き、在るかは worker の state と掃除に依る — 鍵を版や木に結ぶと、効かない場合(新しい worker・テスト)が必ず残る。")
     (interpretation
       "Hy の source の展開は macro の出所の中身に依るが、出所は compile するまで分からない。だから出所の中身は鍵に入れず、code の定数の末尾の記録(doeff-hy の records — 出所の module 名と中身の sha256)を、使う時に今の環境の import の路で引き直した file と照らす。合わなければ焼き直し、同じ鍵の entry を書き直す。Python の source の compile は module 名と Hy の版に依らないので、鍵は中身・Python の版の印・最適化の段だけ。")
     (interpretation
       "準備の道具は準備する root の venv の python で走り、root の版の doeff を import する。root の版の doeff-hy は保存先の入口を持たない版でありうるので、道具は worker の版の doeff-hy の code_store.py を path で読む(bake_plan と同じ置き方)。入口は標準 library だけを import する。")
     (interpretation
       "保存先の並びと掃除は native の wheel の保存先(ADR-DOE-BUILD-001)と同じ作法: <dir>/<鍵の頭>/<鍵の残り><種類>(source の code は鍵の dir の下に macro の環境ごと — R2b)・使うたびに時刻を進める・7 日使われない物を worker の掃除が消す・書きは一時の file からの置き換え・壊れた entry は名指して除き作り直す。保存先の dir は env DOEFF_HY_CODE_STORE で、worker の起動の script が $WORK_DIR/state/doeff-hy-code-store を既定に置く(日次の全体検証の task と同じ dir)。")]
  :decision
    [(rule R1 "bytecode を引く・足す保存先は doeff-hy の code_store.py 1 つ。並び・鍵・使った印・壊れた entry の扱いはここだけが綴り、import の口(loader_hooks)と worker の準備の道具(code_prepare・bytecode_pool)はこの関数を呼ぶ。")
     (rule R2 "code の鍵 = source の中身・Python の版の印・最適化の段、Hy の source はさらに module 名と Hy の版。macro の出所の中身は、当たった code の記録を今の環境の file と照らして確かめ、合わなければ焼き直して同じ鍵の entry を書き直す。")
     (rule R2b "R2 の追補(2026-10-12・card ki-6828e9af04b4 の案 2 = 根治): 鍵 1 つの dir に macro の環境ごとの code の entry を並べる — <dir>/<鍵の頭 2 字>/<鍵の残り>/<記録の指紋>.code(鍵の印 doeff-hy/code-store/3)。記録の指紋 = code の定数の末尾(doeff-hy の記録)の sha256 の頭 32 字(code_store.code-fingerprint — 記録の欄の意味は読まず、記録の印の違う版の記録にも同じ作り方が効く)。引く側は使った時刻の新しい順に照らし、合う最初の 1 つを使い、その entry の時刻だけを進める。焼き直した code は今の環境の entry として足し、他の環境の entry は上書きしない(読めない記録の entry も除かない — 他の版の書き手の物)。足した entry の時刻は鍵の他の entry のどれよりも前に置き(今から 1 日より前にはしない)、照らして使われて初めて前へ出す。鍵 1 つの entry は 16 個(CODE-ENTRIES-PER-KEY)までで、越えたら今足した entry を残して時刻の古い物から除く — 一度きりの環境(macro を直している作業木の検)の entry は互いを除き合い、使い続けている entry を押し出さない。上限を越える数の環境が同じ鍵を順に使っても、外れるのは越えた分だけ(設計検証 lt-Y2ZEDKRFVZR3S6T34PNQZ28Z97 の盲検 A — 足した entry を頭に置いた形では、17 の環境で毎巡全部が外れた)。R2 の「同じ鍵の entry を書き直す」は、記録の同じ entry を書き直す事に読み替える。訳: 鍵 1 つに entry 1 つでは、doeff の版の違う書き手(実行環境の準備と作業木の import)が同じ鍵を交互に上書きし、互いに焼き直した(上の測り)。失敗ケース = packages/doeff-hy/tests/test_bytecode_guard.py の test_two_macro_versions_taking_turns_on_one_store_stop_expanding_from_the_second_round・test_two_record_generations_taking_turns_on_one_store_keep_each_others_entries(盲検 B)・packages/doeff-cluster/tests/test_code_prepare_content_store.py の test_e_two_macro_versions_taking_turns_do_not_rebake_each_other・packages/doeff-hy/tests/test_code_store_entries.py。")
     (rule R2c "R2b の追補(2026-10-12・card ki-11a90ffff495): import の口は、作業木の .pyc が source に合っても、その .pyc の記録が今の環境の macro に合わない時は、compile し直す前に鍵の entry を R2b と同じ照らしで引く。合う entry が在れば compile せずにそれを使い、.pyc を読んだ .pyc と同じ方式(timestamp か hash)で書き直す。合う entry が無ければ今どおり compile し、今の環境の entry として足す。訳: .pyc が在るというだけで保存先を引かないと、兄弟の doeff を替えて使い回す作業木(配備の宣言の木・長く使う会話の木)の最初の import が、保存先に同じ版の entry が揃っていても木の Hy の module を全部 compile し直した(2026-10-12・agora_sim の検 1 本の収集で約 880 本・CPU 約 670 秒 — card ki-ff53506fda43 の調べ)。失敗ケース = packages/doeff-hy/tests/test_bytecode_guard.py の test_a_stale_pyc_in_a_reused_tree_takes_the_stores_matching_entry_without_compiling・test_a_stale_checked_hash_pyc_takes_the_stores_matching_entry_and_is_rewritten_in_the_same_form(合う entry が無い側 = test_a_stale_pyc_with_no_matching_entry_in_the_store_compiles_and_adds_the_entry)。")
     (rule R3 "worker の bytecode の準備は前の版の木から .pyc も import の名も引き継がない(--from・--changed・引き継ぎ元の選びを持たない)。.pyc は保存先の code から書き、無い物と今の macro に合わない物だけを焼いて足す。閉包の import の名も source の中身の鍵で保存先から引く(種類 .imports)。")
     (rule R3b "R3 の追補(2026-10-17・card ki-d51b7baa38bc・設計検証 lt-4KTRWD3K1PH2VG5W5X294EZJP4 の盲検 B): root の木に .pyc を置くのは焼く道具だけ。doeff-cluster の中で .pyc の置き場の知識(\"__pycache__\"・\".pyc\" の綴り・pyc-path・cache_from_source)を持つのは、焼く道具の module(bytecode_pool・code_prepare・code_plan・bake_plan・code_model)と模擬の世界だけ。訳: 写し元の root の .pyc を、木を置く翻訳の別の effect で新しい root へ hardlink する実装は、R3 の検(道具の --from・--changed)も木の形の検も通った。持ち越した .pyc は保存先の使った印を進めないので、掃除が root の使う保存先の .pyc と code を 7 日で消し、変わった Python の source の古い .pyc が完成した root に残った(盲検 B の実測 — 保存先の .pyc 24 → 0・次の root の新しい byte 188,416)。失敗ケース = この冊の test-adr-doe-build-002-only-the-bytecode-tool-places-pyc と judgments-reject-the-counterexamples の盲検 B の行。")
     (rule R4 "保存先の dir は env DOEFF_HY_CODE_STORE。worker の起動の script は既定を $WORK_DIR/state/doeff-hy-code-store に置き、呼び手の値を優先する(手元の 1 台の cluster のテストは件をまたいで同じ dir を渡す)。")
     (rule R5 "7 日使われない entry は worker の掃除が消す(native の wheel と同じ 7 日 — CODE-STORE-UNUSED-SECONDS = WHEEL-UNUSED-SECONDS)。")
     (rule R5b "R5 の追補(2026-10-12・card ki-6828e9af04b4): 掃除は、自分も下の物も全部 7 日動いていない保存先の dir を dir ごと消す(env_store の unused-store-names)。使った entry の在る dir と、作ったばかりの空の dir は残す。訳: R2b の鍵の dir は版ごとの source の数だけ増えるので、entry だけを消すと空の dir が残り続け、保存先の歩きが読む数が増え続ける。失敗ケース = packages/doeff-cluster/tests/test_env_store.hy の test-the-sweep-removes-unused-code-entries-in-key-dirs-and-the-dirs-left-unused。")
     (rule R6 "R3 の追補(2026-10-17・card ki-d51b7baa38bc): worker の焼く道具は、木の .pyc を保存先の中身の同じ file への hardlink で置く — 種類 .pyc・名 = .pyc の中身の sha256・読み取り専用(code_store.link-pyc)。保存先の code から置く .pyc は頭の後ろに entry の中身をそのまま並べ(code_store.current-entry — marshal し直さない)、焼いた code の entry は焼いた .pyc の頭の後ろの bytes そのものにする(add-code の data)ので、同じ code の .pyc は root をまたいで 1 つの file。置けない時(別の file system・link の数の上限)は今どおり中身を書き、理由を準備の記録に 1 度ずつ出す。保存先の .pyc も R5 の掃除で消える(木の名は残る)。木の .pyc を書き直す手(焼く道具・import の口・Python の import)はどれも一時の file から置き換えるので、hardlink の先(他の木の .pyc と保存先)は変わらない。前の版の木からは引き継がない(R3 のまま — 分け合う先は保存先)。訳: root ごとに同じ中身の .pyc を新しく書くと、root を消した後も macOS の local snapshot がその中身を 24 時間持ち、Mac の worker の作業 disk の空きが root の作り直しの数だけ減った(10-17 の Mac の root 1 本で木の .pyc 100.6 MB が私有)。失敗ケース = packages/doeff-cluster/tests/test_code_prepare_content_store.py の test_f_trees_baked_from_the_store_share_one_file_per_pyc・test_g_rebaking_one_tree_does_not_write_through_the_shared_pyc・test_h_a_one_commit_root_writes_a_tenth_or_less_where_cp_refuses_gnu_options・packages/doeff-hy/tests/test_code_store_pyc_links.py。")]
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
     (law only-the-bytecode-tool-places-pyc
       :statement "for_all file f in packages/doeff-cluster/src: spells_bytecode_placement(f) => f in BYTECODE-TOOL-FILES"
       :counterexamples
         [(counterexample "木を置く翻訳(env_translation)に (val PYCACHE-DIR \"__pycache__\") と、写し元の木の .pyc を新しい root へ LinkFile で置く effect の答えを足す(盲検 B — R3 の検と木の形の検を通り、保存先の使った印が進まず掃除が使っている .pyc を消した)")])
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
     (deftest test-adr-doe-build-002-only-the-bytecode-tool-places-pyc
       (val repo-root (. (Path __file__) parent parent parent))
       (val found (run (placement-files repo-root)))
       (val violations (run (placement-violations found)))
       (assert (= violations []) (+ ".pyc を root に置く 2 本目の道(ADR-DOE-BUILD-002 R3b): " (str violations)))
       (assert (in "packages/doeff-cluster/src/doeff_cluster/foundation/bytecode_pool.hy" found) "焼く道具の綴りを見つけない — 走査の範囲が壊れた"))
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
       (assert (not (run (carries-from-a-previous-tree ";; 前は \"--from\" で引き継いだ\n"))))
       ;; 反例(R3b・盲検 B): 木を置く翻訳が .pyc の置き場を綴る行は赤。註の行と、文の中の __pycache__ の語(引用符の無い物)は数えない。
       (assert (run (spells-bytecode-placement "(val PYCACHE-DIR \"__pycache__\")\n")))
       (assert (run (spells-bytecode-placement "(when (.endswith e.name \".pyc\") (<- (LinkFile a b)))\n")))
       (assert (run (spells-bytecode-placement "(import doeff_core_effects.python_bytecode [pyc-path])\n")))
       (assert (run (spells-bytecode-placement "cache = importlib.util.cache_from_source(path)\n")))
       (assert (not (run (spells-bytecode-placement ";; 追跡しない物(.venv・__pycache__)は写さない\n"))))
       (assert (not (run (spells-bytecode-placement "\"追跡しない物(.venv・__pycache__・焼いた印)は写さない\"\n"))))
       (val tree "packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_translation.hy")
       (assert (= (len (run (placement-violations (frozenset #{tree "packages/doeff-cluster/src/doeff_cluster/foundation/bytecode_pool.hy"})))) 1))
       (assert (= (run (placement-violations (frozenset #{"packages/doeff-cluster/src/doeff_cluster/worker/core/code_plan.hy"}))) [])))]
  :plans [])
