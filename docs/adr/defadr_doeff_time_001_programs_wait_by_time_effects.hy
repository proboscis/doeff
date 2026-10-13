;;; Executable ADR: program は時間を doeff-time の効果(Delay・WaitUntil・WaitWithin など)を yield して待つ。time.sleep・asyncio.sleep で
;;; 壁の時計を待つのは、時間の handler の実装((effect, k) を受ける関数)だけ。新しい素の sleep は semgrep の規則が ERROR で止める。
;;; Hy の program(defk・defp)は semgrep が読めないので、doeff-linter の規則 DOEFF210 が同じ決まりを止める(R7・card ki-3c3d85253c2c)。
;;;
;;; 出自 = 利用者の 2026-10-12 22:38 の原文 2 通(下の :problem の fact)。追跡 = card ki-94380dba532f(親)・ki-495db9a2efa0(この冊)。
;;;
;;; この冊は規則の中身(pattern・paths)を持たない。規則の定義は .semgrep.yaml の 1 か所で、冊は法の :enforced-by に規則の id と検の名を
;;; `<repo の根からの path>::<名>` の形で書き、その名が今の木に在る事・規則が ERROR である事・極性の検がその規則を当てている事を
;;; :enforcement の検で見る(規則を消す・WARNING に下げる・検の名を変えると赤)。名は、semgrep の設定なら規則の id・Python の検なら
;;; module の段の関数・Hy の検なら deftest・Rust の検なら #[test] の関数。
;;;
;;; 戻し方: この冊を足した commit を revert する(冊の file 1 つと enforcement-ledger.json の行)。規則そのものを外す時は、
;;; .semgrep.yaml の doeff-no-sleep-in-programs・doeff-no-sleep-in-tests・doeff-agents-no-sleep-effect-hy と極性の検・名簿・この冊を同じ commit で消し、
;;; docs/adr/enforcement-ledger.json を作り直す(ADR-DOE-ENFORCE-001 R5)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(import doeff-adr.registry [get-adr])
(require doeff-hy.macros [deftest defk val var <-])
(import doeff [run])
(import ast)
(import re)
(import pathlib [Path])
(import hy)
(import yaml)


;; ---------------------------------------------------------------------------
;; 台帳

(val ADR-ID "ADR-DOE-TIME-001")
(val THIS-FILE "docs/adr/defadr_doeff_time_001_programs_wait_by_time_effects.hy")

;; sleep の規則の定義の 1 か所。
(val SEMGREP-CONFIG ".semgrep.yaml")

;; 規則の id → その極性の検の file(検体の `# 鳴る`〔Hy の検体は `; 鳴る`〕の行だけが当たる事を、SEMGREP-CONFIG の規則そのものを当てて見る検)。
(val POLARITY-TESTS
  {"doeff-no-sleep-in-programs" "tests/semgrep/test_no_sleep_in_programs_rule.py"
   "doeff-no-sleep-in-tests" "tests/semgrep/test_no_sleep_in_tests_rule.py"
   "doeff-agents-no-sleep-effect-hy" "tests/semgrep/test_doeff_agents_no_sleep_effect_hy_rule.py"})


;; ---------------------------------------------------------------------------
;; 判断(純関数)

(defk rule-severities [config-text]
  {:pre [(: config-text str)] :post [(: % dict)] :tags {:context "doeff-time-adr" :role "judgment"}}
  "semgrep の設定の文から、規則の id → その id を持つ規則の severity の並び(同じ id の規則が 2 つ在れば 2 つ並ぶ)。"
  (val rules (.get (or (yaml.safe-load config-text) {}) "rules" []))
  (dfor rule-id (sfor r rules (str (get r "id")))
        rule-id (lfor r rules :if (= (str (get r "id")) rule-id) (.get r "severity"))))


(defk polarity-subject [source]
  {:pre [(: source str)] :post [(: % tuple)] :tags {:context "doeff-time-adr" :role "judgment"}}
  "極性の検の source が当てる物: module の段の RULE の値(規則の id)と、CONFIG の式の中の文字列(設定の file の名)。書いていなければ None。"
  (val assigned (dfor node (. (ast.parse source) body)
                      :if (and (isinstance node ast.Assign)
                               (= (len node.targets) 1)
                               (isinstance (get node.targets 0) ast.Name))
                      (. (get node.targets 0) id) node.value))
  (val rule-value (.get assigned "RULE"))
  (val config-value (.get assigned "CONFIG"))
  #((if (isinstance rule-value ast.Constant) rule-value.value None)
    (if (is config-value None)
        None
        (next (gfor n (ast.walk config-value)
                    :if (and (isinstance n ast.Constant) (isinstance n.value str))
                    n.value)
              None))))


(defk deftest-names [text]
  {:pre [(: text str)] :post [(: % frozenset)] :tags {:context "doeff-time-adr" :role "judgment"}}
  "Hy の source のどの深さでも、(deftest 名 …) の形の名を集める(文字列と註の中の綴りは形でないので数えない)。"
  (var pending (list (hy.read-many text)))
  (var names #{})
  (while pending
    (val form (get pending -1))
    (:= pending (+ (cut pending 0 -1) (if (isinstance form hy.models.Sequence) (list form) [])))
    (when (and (isinstance form hy.models.Expression)
               (> (len form) 1)
               (= (str (get form 0)) "deftest"))
      (:= names (| names #{(str (get form 1))}))))
  (frozenset names))


;; Rust の検: 行の頭(字下げだけ)の #[test] の次の行の fn の名。註にした検(// #[test]・// fn …)は行の頭が // なので当たらない。
(val RUST-TEST (re.compile r"^[ \t]*#\[test\][ \t]*\n[ \t]*fn[ \t]+([A-Za-z_][A-Za-z0-9_]*)" re.MULTILINE))


(defk rust-test-names [text]
  {:pre [(: text str)] :post [(: % frozenset)] :tags {:context "doeff-time-adr" :role "judgment"}}
  "Rust の source が検として定義する名(#[test] の付いた関数)。#[test] の無い関数と、註にした検は数えない。"
  (frozenset (.findall RUST-TEST text)))


(defk check-names [rel text]
  {:pre [(: rel str) (: text str)] :post [(: % frozenset)] :tags {:context "doeff-time-adr" :role "judgment"}}
  "file の中身 text が検として定義する名: semgrep の設定は規則の id・Python の検は module の段の関数の名・Hy の検は deftest の名・
   Rust の検は #[test] の関数の名。"
  (<- severities dict (rule-severities (if (= rel SEMGREP-CONFIG) text "")))
  (<- deftests frozenset (deftest-names (if (.endswith rel ".hy") text "")))
  (<- rust-tests frozenset (rust-test-names (if (.endswith rel ".rs") text "")))
  (cond
    (= rel SEMGREP-CONFIG) (frozenset severities)
    (.endswith rel ".py") (frozenset (gfor node (. (ast.parse text) body)
                                           :if (isinstance node #(ast.FunctionDef ast.AsyncFunctionDef))
                                           node.name))
    (.endswith rel ".rs") rust-tests
    True deftests))


;; ---------------------------------------------------------------------------
;; 土台(file を読む)

(defk dead-checks [repo-root entries]
  {:pre [(: repo-root Path) (: entries list)] :post [(: % list)] :tags {:context "doeff-time-adr" :role "foundation"}}
  "法の :enforced-by の項(`<repo の根からの path>::<名>`)のうち、今の木に在る規則・検を指していない物を、訳つきで挙げる。"
  (var dead [])
  (for [entry entries]
    (val parts (.partition entry "::"))
    (val rel (get parts 0))
    (val name (get parts 2))
    (val path (/ repo-root rel))
    (val text (if (.is-file path) (.read-text path :encoding "utf-8") ""))
    (<- names frozenset (check-names rel text))
    (when (not-in name names)
      (:= dead (+ dead [f"{entry}: {rel} に {(or name "<名なし>")} という規則・検が無い"]))))
  dead)


(defadr ADR-DOE-TIME-001
  :title "program は時間を doeff-time の効果(Delay・WaitUntil・WaitWithin など)を yield して待つ。time.sleep・asyncio.sleep で壁の時計を待つのは時間の handler の実装だけで、新しい素の sleep は .semgrep.yaml の規則が ERROR で止める"
  :status "accepted"
  :scope [".semgrep.yaml(規則 doeff-no-sleep-in-programs・doeff-no-sleep-in-tests・doeff-agents-no-sleep-effect-hy)"
          "packages/doeff-flow/examples/"
          "packages/doeff-linter/src/project/program_sleep.rs(規則 DOEFF210)"
          "packages/doeff-linter/tests/program_sleep.rs"
          "pyproject.toml の [tool.doeff-linter.program_sleep]"
          "packages/doeff-agents/src/doeff_agents/(tmux.py・agentd_client.py・io_handlers.hy・sessionhost/launch.hy・sessionhost/host.hy)"
          "tests/semgrep/test_no_sleep_in_programs_rule.py"
          "tests/semgrep/test_no_sleep_in_tests_rule.py"
          "tests/semgrep/test_doeff_agents_no_sleep_effect_hy_rule.py"
          "tests/semgrep/test_realtime_tests_registry.py"
          "tests/realtime_tests.toml"
          "packages/doeff-time/src/doeff_time/"
          "docs/adr/defadr_doeff_time_001_programs_wait_by_time_effects.hy"]
  :problem
    [(fact
       "利用者の 2026-10-12 22:38 の原文: \"someone is using asyncio.sleep??? for what?? why not doeff's time effects?\" / \"i though we have doeff linter checking that\""
       :evidence "card ki-94380dba532f の本文と、commit ba5513169 の message(どちらも原文を引く)")
     (fact
       "2026-10-12 の doeff の main では、sleep の規則は semgrep の doeff-no-sleep-in-tests だけだった。対象は tests/ だけ・severity は WARNING(main への取り込みを止めない)で、本体(src)に規則は無かった。tests/ で asyncio.sleep を含む file は 30 在った。"
       :evidence "card ki-94380dba532f の本文の「今の事実」(2026-10-12 22:4x に main の作業木を git grep で読んだ記録)")
     (fact
       "本体の @do の program の中の素の sleep は 7 か所在った(doeff-gemini structured_llm.py の再試行と upload の待ち・doeff-openai chat.py・embeddings.py・structured_llm.py・doeff-openrouter chat.py・doeff-seedream structured_llm.py の再試行の待ち)。再試行の道を通る検(openai 3・gemini 5)は実時間で 1〜2 秒待つか、asyncio.sleep を monkeypatch で差し替えていた。"
       :evidence "commit ba5513169(2026-10-14 02:21 JST に main・7 か所を Delay に替え、規則 2 つを ERROR にした)")
     (fact
       "2026-10-14 の main(4dffe90e5)では Hy の program に規則が無かった。DOEFF210 をその木に当てると、当たりは doeff-agents の sessionhost/substrate_herdr.hy の defk herdr-send-keys-io の 3 行(595・598・604)。同じ木で doeff-agents の program は自前の待ちの効果 2 つで待っていた: io_effects の Sleep(tmux.py の 3 か所・agentd_client.py の 1 か所 — driver-io-handler が time.sleep で答える)と sessionhost の ClockSleep(launch.hy の wait-for-launch-ready の 7 か所 — real-substrate が time.sleep で答える)。既存の規則 doeff-agents-no-sleep-effect は Python の class SleepEffect / def Sleep の名だけを見るので、どちらにも当たっていなかった。"
       :evidence "card ki-3c3d85253c2c の本文の「今の事実」と、その card の commit(DOEFF210 を足した commit の本文に、main の木への当たり 3 件と変更の後の 0 件を書いた)")]
  :context
    [(interpretation
       "program が Delay を yield すれば、1 秒が何であるかは呼び手が置く時間の handler が決める: async_time_handler・sync_time_handler は壁の時計で待ち、sim_time_handler は仮想の時計を進めるだけで待たない。program が time.sleep・asyncio.sleep を呼ぶと、どの handler を置いても壁の時計で待つので、その program の検は実時間の分だけ遅くなり、込んだ機体では時間切れで赤になる。")
     (interpretation
       "利用者の言う「doeff linter」は Rust の doeff-linter(DOEFFnnn)とも読めるが、commit ba5513169 は .semgrep.yaml の規則で入れた。これは親 card ki-94380dba532f の担い手の選びで、利用者が semgrep を名指した記録ではない。commit の hook と日次の走査が新しい所見を止める点は、どちらの道具でも同じ。")
     (interpretation
       "program でも handler でもない Python の loop(CLI の見張りのポーリング・常時動く thread の loop)の素の sleep は、境界の入口に数えて規則の対象にしない。これは card ki-f44251561832 で親 card の担い手が 2026-10-14 に決めた事(案 A・戻せる判断)で、利用者の決定ではない。")]
  :decision
    [(rule R1 "program(@do の関数・defk)は、時間を待つ時に doeff-time の効果(Delay・WaitUntil・WaitWithin・RaceWithin・ScheduleAt・WaitTicks)を yield する。time.sleep(x)・asyncio.sleep(x) を呼ばない(yield Await(asyncio.sleep(x)) と from time import sleep の sleep(x) を含む)。時間を待たずに 1 度譲るだけの sleep(0) は数えない。")
     (rule R2 "壁の時計を待ってよいのは、時間の handler の実装((effect, k) を受ける関数と、そこからだけ呼ばれる helper)だけ。呼び手は program を走らせる所で時間の handler を選ぶ: 本番は async_time_handler()(await_handler() の内)か sync_time_handler()、検は sim_time_handler()。")
     (rule R3 "検は program を sim_time_handler の仮想の時計で走らせるか、待つ物(出来事)を名指して待つ。time.sleep・asyncio.sleep を呼んでよいのは、実時間そのもの(本物の thread・本物の asyncio の loop・外の process や library)が対象の検だけで、その検は pytest の marker realtime を名乗り、名簿 tests/realtime_tests.toml に理由と一緒に載る。")
     (rule R4 "新しい素の sleep は linter で止める: .semgrep.yaml の規則 doeff-no-sleep-in-programs と doeff-no-sleep-in-tests を severity ERROR で置く。規則の定義(pattern・paths)は .semgrep.yaml の 1 か所で、この冊にも他の冊にも写さない。形の正しさ(鳴るべき行だけが鳴る)は、規則ごとの極性の検が .semgrep.yaml の規則そのものを検体に当てて見る。")
     (rule R5 "今の規則が見ていない所は、子の card が塞ぐまで R1・R3 の法の外に在る: tests/ の nosemgrep の 7 か所と conformance/ と Hy の検(card ki-6aa7cb5ffb83)。塞いだ変更は、足した規則・検を下の法の :enforced-by に同じ変更で足す。")
     (rule R7 "Hy の program(defk・defp の本体)にも R1 が当たり、doeff-linter の規則 DOEFF210 が critical で止める(semgrep は Hy を読めない)。規則の定義は packages/doeff-linter/src/project/program_sleep.rs の 1 か所、母集団(program の置き場)は根の pyproject.toml の [tool.doeff-linter.program_sleep] の 1 か所(本体 — tests/ と examples/ は外)。Hy で壁の時計を待ってよい handler の実装は 3 つの形: defhandler の節・引数の並びが [effect k] の定義・handler からだけ呼ばれて本物の相手(子 process・端末)の実時間を待つ helper。helper は契約の辞書の :tags で :role \"foundation\"(汎用の I/O の役)を自分で名乗り、Delay にしない訳を docstring に書く(2026-10-14 の 2 つ: doeff-agents の herdr-send-keys-io — 本物の TUI が貼り付けを受け終える実時間・doeff-core-effects の group-gone-by — 取り消しの手当ては要求の答え手の積みが届かない新しい VM で回り、doeff-core-effects は doeff-time に依存しない)。deff・defn(素の callable)は効果を出せないので program に数えない — deff の是非は ADR-DOE-HY-004 が持つ。doeff-agents は自前の待ちの効果を持たない: program は doeff_time の Delay を yield し、本番の root(io_handlers.hy の run-driver-io・sessionhost/host.hy の run-hosted)が doeff-time の sync-time-handler を置く。自前の待ちの効果を Hy で定め直す形(名に Sleep / Delay を含む効果の defclass・defeffect と、sleep / …-sleep という名の作り手)は、.semgrep.yaml の規則 doeff-agents-no-sleep-effect-hy が ERROR で止める(R4 と同じ扱い — 定義は .semgrep.yaml の 1 か所・形は極性の検が見る)。")
     (rule R6 "戻し方: この冊だけを外すなら、冊を足した commit を revert する。規則を外す・WARNING に下げるなら、利用者の 2026-10-12 の決定を覆す事になるので、利用者の新しい決定を :problem に原文で書いてから、規則・極性の検・名簿・この冊を同じ commit で直す。")]
  :laws
    [(law a-program-waits-by-a-time-effect
       :statement "for_all 本体(doeff/ と packages の src)と examples/(packages/*/examples/ と根の examples/)の Python の program p(@do の関数)と、本体の Hy の program p(defk・defp の本体)・p の中の待ち w: w は doeff-time の効果の yield ∧ w ∉ {time.sleep(x)・asyncio.sleep(x) | x ≠ 0}(handler の実装の中は除く — Python は (effect, k) を受ける関数・Hy は defhandler の節と [effect k] を受ける定義と :role foundation を名乗る helper〔R7〕)"
       :counterexamples
         [(counterexample "再試行の間を yield Await(asyncio.sleep(delay)) で待つ(doeff-gemini structured_llm.py の ba5513169 の前の形)— 検は実時間で 1〜2 秒待つか asyncio.sleep を monkeypatch で差し替える")
          (counterexample "@do の program の中で time.sleep(1.0) を呼ぶ — sim_time_handler を置いても仮想の時計は進まず、壁の時計で 1 秒かかる")
          (counterexample "doeff-flow の例の @do の中で time.sleep(0.1) を呼ぶ(ki-b2407b326b41 の前の 15 か所の形)— 例は写される物なので、素の sleep が利用者の program に広がる")
          (counterexample "Hy の defk の本体で (time.sleep 0.5) を呼ぶ — semgrep は Hy を読めないので doeff-no-sleep-in-programs は鳴らず、DOEFF210 が無ければ誰も止めない(ki-3c3d85253c2c の前の形)")
          (counterexample "program が package の自前の待ちの効果(doeff-agents の io の Sleep・sessionhost の ClockSleep の形)を yield し、その package の handler が time.sleep で答える — 字面に sleep は無いが、時間の handler を置いても仮想の時計は進まない(ki-3c3d85253c2c の前の 11 か所)")
          (counterexample "program の defk に :role \"foundation\" を名乗らせて眠りを通す — 名乗ってよいのは handler からだけ呼ばれ本物の相手の実時間を待つ helper だけで、訳を docstring に書く(R7)。規則は名乗りを信じるので、名乗りの是非は変更を読む人が見る")]
       :enforced-by [".semgrep.yaml::doeff-no-sleep-in-programs"
                     "tests/semgrep/test_no_sleep_in_programs_rule.py::test_only_the_marked_lines_fire"
                     "tests/semgrep/test_no_sleep_in_programs_rule.py::test_examples_are_in_the_range"
                     ".semgrep.yaml::doeff-agents-no-sleep-effect-hy"
                     "tests/semgrep/test_doeff_agents_no_sleep_effect_hy_rule.py::test_only_the_marked_lines_fire"
                     "packages/doeff-linter/tests/program_sleep.rs::only_the_marked_lines_fire"
                     "packages/doeff-linter/tests/program_sleep.rs::the_hy_programs_of_this_repo_do_not_sleep"
                     "packages/doeff-linter/tests/program_sleep.rs::the_sample_of_a_sleeping_program_is_found_under_the_repo_s_own_settings"
                     "packages/doeff-agents/tests/test_driver_waits_are_time_effects.py::test_a_submitted_paste_waits_the_settle_and_the_first_confirm_on_the_time_handler"
                     "packages/doeff-agents/tests/test_driver_waits_are_time_effects.py::test_each_enter_retry_waits_on_the_time_handler"
                     "packages/doeff-agents/tests/test_driver_waits_are_time_effects.py::test_waiting_for_agentd_polls_on_the_time_handler"
                     "packages/doeff-agents/tests/test_driver_waits_are_time_effects.py::test_the_production_root_answers_a_delay"
                     "packages/doeff-agents/tests/sessionhost_launch_deftests.hy::test-launch-ready-gate-polls-by-the-time-effect"
                     "packages/doeff-agents/tests/sessionhost_launch_deftests.hy::test-launch-dialog-redraw-waits-by-the-time-effect"
                     "packages/doeff-agents/tests/sessionhost_host_deftests.hy::test-run-hosted-answers-a-delay-on-the-wall-clock"]
       :wiring "配線済み(2026-10-14・ba5513169・examples/ は ki-b2407b326b41・Hy の program と doeff-agents は ki-3c3d85253c2c)— Python の規則は commit の hook(.pre-commit-config.yaml の別名 semgrep-rules)と日次の走査が当てる。doeff-agents の自前の待ちの効果を止める規則 doeff-agents-no-sleep-effect-hy は、同じ semgrep の走査が Hy の file に当てる(languages: generic)。Hy の規則 DOEFF210 は、日次の全体検証の処理ステージ rust(make test-rust の cargo test — packages/doeff-linter/tests/program_sleep.rs が根の設定でこの repo の本体を判じ、当たりを file と行で名指す)が当て、手元では make lint-doeff とエディタの 1 file の実行が出す。commit の hook には載っていない(根の doeff-linter の hook は Python の file だけを測る)。範囲は Python の本体と examples/・Hy の本体(R5・R7)。書く前の助言は agent-commons の条件つきルール doeff-program-waits-by-the-time-effect が出す(Hy の defk・defp・deftest と doeff の外の repo にも当たる・止めはしない)")
     (law the-time-handler-decides-what-a-second-is
       :statement "for_all program p・p の中の Delay(s): sim_time_handler の下では仮想の時計がちょうど s 進む ∧ async_time_handler・sync_time_handler の下では壁の時計で s 待つ(p の文は 3 つの時計で同じ)"
       :counterexamples
         [(counterexample "時計ごとに program を書き分ける(検の時だけ待ちを飛ばす引数を program に足す)— 検が走らせる文と本番の文が別になる")]
       :enforced-by ["packages/doeff-time/tests/test_time_contract.hy::test-delay-advances-by-its-span"]
       :wiring "配線済み — 同じ deftest が async・sync・sim の 3 つの解釈器で走る(agora-redesign #1159)")
     (law a-test-sleeps-only-when-it-declares-realtime
       :statement "for_all tests/ の Python の検 t: t が time.sleep(x)・asyncio.sleep(x)(x ≠ 0)を呼ぶ ⇒ t は marker realtime を名乗る ∧ t は名簿 tests/realtime_tests.toml に理由と一緒に載る"
       :counterexamples
         [(counterexample "結果が出るのを time.sleep(0.5) で待つ検 — 込んだ機体では 0.5 秒で足りずに赤、速い機体では毎回 0.5 秒を捨てる")
          (counterexample "realtime を名乗るが名簿に載せない検 — 実時間の何が対象かが残らず、規則の除外が理由なしに増える")]
       :enforced-by [".semgrep.yaml::doeff-no-sleep-in-tests"
                     "tests/semgrep/test_no_sleep_in_tests_rule.py::test_only_the_marked_lines_fire"
                     "tests/semgrep/test_realtime_tests_registry.py::test_registry_lists_exactly_the_realtime_marks"
                     "tests/semgrep/test_realtime_tests_registry.py::test_every_registry_entry_gives_a_reason"]
       :wiring "配線済み(2026-10-14・ba5513169)— 範囲は tests/ の Python で、nosemgrep・conformance/・Hy の検は R5")
     (law the-sleep-rules-stop-as-errors-from-one-definition
       :statement "for_all r ∈ {doeff-no-sleep-in-programs・doeff-no-sleep-in-tests・doeff-agents-no-sleep-effect-hy}: |{.semgrep.yaml の規則 | id = r}| = 1 ∧ severity(r) = ERROR ∧ r の極性の検は .semgrep.yaml の r そのものを当てる"
       :counterexamples
         [(counterexample "severity を WARNING に下げる(2026-10-12 の形)— main への取り込みを止めず、tests/ に 30 file の sleep が残った")
          (counterexample "規則の pattern をこの冊の defsemgrep に写す — 定義が 2 か所になり、片方だけ直した日に冊の検と本物の規則が別の物を見る")
          (counterexample "極性の検の RULE を別の規則の id に書き換える — 検は緑のまま、sleep の規則の形は誰も見ていない")]
       :enforced-by ["docs/adr/defadr_doeff_time_001_programs_wait_by_time_effects.hy::test-adr-doe-time-001-the-sleep-rules-stop-as-errors"
                     "docs/adr/defadr_doeff_time_001_programs_wait_by_time_effects.hy::test-adr-doe-time-001-each-sleep-rule-is-read-by-its-polarity-test"]
       :wiring "配線済み(この冊)")]
  :enforcement
    [(deftest test-adr-doe-time-001-the-sleep-rules-stop-as-errors
       ;; 規則は .semgrep.yaml に 1 つずつ在り、severity は ERROR(R4 — WARNING に下げる・消す・同じ id を 2 つ置くと赤)。
       (val repo-root (. (Path __file__) parent parent parent))
       (val severities (run (rule-severities (.read-text (/ repo-root SEMGREP-CONFIG) :encoding "utf-8"))))
       (for [rule-id POLARITY-TESTS]
         (assert (= (.get severities rule-id) ["ERROR"])
                 (+ rule-id ": .semgrep.yaml に severity ERROR の規則がちょうど 1 つ在るはず(ADR-DOE-TIME-001 R4): "
                    (str (.get severities rule-id))))))
     (deftest test-adr-doe-time-001-each-sleep-rule-is-read-by-its-polarity-test
       ;; 極性の検は、その規則の id を RULE に持ち、.semgrep.yaml を CONFIG に持つ(R4 — 規則の写しではなく本物を当てる)。
       (val repo-root (. (Path __file__) parent parent parent))
       (for [#(rule-id rel) (.items POLARITY-TESTS)]
         (val subject (run (polarity-subject (.read-text (/ repo-root rel) :encoding "utf-8"))))
         (assert (= subject #(rule-id SEMGREP-CONFIG))
                 (+ rel ": 極性の検が .semgrep.yaml の規則 " rule-id " を当てていない(ADR-DOE-TIME-001 R4): " (str subject)))))
     (deftest test-adr-doe-time-001-every-law-names-living-checks
       ;; 法の :enforced-by の項は、どれも今の木に在る規則・検を指す(規則を消す・検の名を変えると赤 — 法が名だけ残らない)。
       (val repo-root (. (Path __file__) parent parent parent))
       (val laws (. (get-adr ADR-ID) laws))
       (val unbound (lfor l laws :if (not (.get l "enforced_by")) (get l "id")))
       (assert (= unbound []) (+ "規則・検に結ばれていない法(ADR-DOE-TIME-001): " (str unbound)))
       (val entries (lfor l laws entry (get l "enforced_by") entry))
       (val rules (sfor entry entries :if (.startswith entry (+ SEMGREP-CONFIG "::")) (get (.partition entry "::") 2)))
       (assert (= rules (set POLARITY-TESTS)) (+ "法が結ぶ規則と、極性の検の表の規則が違う: " (str (sorted rules))))
       (val dead (run (dead-checks repo-root entries)))
       (assert (= dead []) (+ "法の :enforced-by が今の木に無い規則・検を指す(ADR-DOE-TIME-001): " (str dead))))
     (deftest test-adr-doe-time-001-judgments-reject-the-counterexamples
       ;; 反例: WARNING の規則・同じ id の 2 つの規則・別の規則を当てる極性の検・写しの設定を当てる極性の検は、上の検の比べで落ちる値になる。
       (val config "rules:\n  - id: a\n    severity: WARNING\n  - id: b\n    severity: ERROR\n  - id: b\n    severity: ERROR\n")
       (assert (= (run (rule-severities config)) {"a" ["WARNING"] "b" ["ERROR" "ERROR"]}))
       (assert (= (run (rule-severities "")) {}))
       (assert (= (run (polarity-subject "RULE = \"doeff-no-star-imports\"\nCONFIG = REPO_ROOT / \".semgrep.yaml\"\n"))
                  #("doeff-no-star-imports" ".semgrep.yaml")))
       (assert (= (run (polarity-subject "RULE = \"doeff-no-sleep-in-tests\"\nCONFIG = FIXTURES / \"copy.yaml\"\n"))
                  #("doeff-no-sleep-in-tests" "copy.yaml")))
       (assert (= (run (polarity-subject "import pytest\n")) #(None None)))
       ;; 名の読み: 註・文字列の中の綴りと、class の中の method は検の名に数えない。
       (assert (= (run (check-names "tests/sample.py" "# def test_gone(): ...\ndef test_here():\n    pass\nclass T:\n    def test_inner(self):\n        pass\n"))
                  (frozenset #{"test_here"})))
       (assert (= (run (check-names "tests/sample.hy" ";; (deftest test-gone)\n(print \"(deftest test-text)\")\n(defadr X :enforcement [(deftest test-here (assert True))])\n"))
                  (frozenset #{"test-here"})))
       (assert (= (run (check-names SEMGREP-CONFIG config)) (frozenset #{"a" "b"})))
       ;; Rust の検の名: #[test] の付いた関数だけ。補助の関数と、註にした検は数えない。
       (assert (= (run (check-names "tests/sample.rs" "fn helper() {}\n\n#[test]\nfn fires() {}\n\n// #[test]\n// fn gone() {}\n\n    #[test]\n    fn nested_in_a_mod() {}\n"))
                  (frozenset #{"fires" "nested_in_a_mod"})))
       ;; 今の木に無い規則・無い file・名の無い項は、どれも結ばれていない項として挙がる。
       (val repo-root (. (Path __file__) parent parent parent))
       (val missing [".semgrep.yaml::doeff-no-such-sleep-rule"
                     "tests/semgrep/test_no_such_file.py::test_only_the_marked_lines_fire"
                     "tests/realtime_tests.toml"
                     "packages/doeff-linter/tests/program_sleep.rs::no_such_rust_test"
                     (+ THIS-FILE "::test-adr-doe-time-001-no-such-test")])
       (assert (= (len (run (dead-checks repo-root missing))) 5))
       (assert (= (run (dead-checks repo-root [(+ THIS-FILE "::test-adr-doe-time-001-judgments-reject-the-counterexamples")])) [])))]
  :plans ["card ki-94380dba532f"
          "card ki-495db9a2efa0"
          "card ki-b2407b326b41"
          "card ki-3c3d85253c2c"])
