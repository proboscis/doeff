;;; Executable ADR: Spawn は spawn-site の boundary stack(handler+observer)を継承する。

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-CORE-001
  :title "Spawn は handler と同様に WithObserve 境界も捕捉・再インストールする(interleave 順序保存)"
  :status "accepted"
  :scope ["doeff-core-effects" "scheduler.Spawn" "doeff-vm.GetBoundaries" "WithObserve"]
  :problem
    [(fact
       "scheduled() の内側に置いた WithObserve は Spawn した子タスクが perform する効果を一切観測しない。同じ位置の handler(WithHandler)は子に継承されるのに、observer だけが脱落する(silent drop — fail-fast 原則違反)。"
       :evidence "issues/scheduler-spawn-drops-observer-boundary.md 再現 @ cacc4b85; tests/test_spawn_observer_inheritance.py は修正前 HEAD で 5 件 fail")
     (fact
       "機構: Spawn は get_inner_handlers(= GetHandlers(k))で prompt boundary のみを捕捉して子タスクに再インストールする。intercept(observer)boundary は捕捉対象外。observer 呼び出しは fiber 親チェーン走査であり、Spawn 子 fiber は scheduler の評価文脈を親に持つため、spawn-site〜scheduler 間の observer boundary はチェーン上に存在しない。"
       :evidence "packages/doeff-core-effects/doeff_core_effects/scheduler.py Spawn 分岐; doeff/handler_utils.py; packages/doeff-vm-core/src/vm/step.rs call_all_observers")
     (fact
       "実務影響: doeff-traverse parallel worker 内の slog が observer ベース出力に出ない(proboscis-ema, 2026-04)。OpenTelemetry の effect→span 自動計装をサブツリー限定で書くと、Spawn を含む区間で計装が『付いているのに見えない』。"
       :evidence "issue 動機セクション")]
  :context
    [(interpretation
       "観測の期待値として handler と observer の継承規則は対称であるべき。非対称を正当化する仕様文書は存在しなかった(issue 論点 1)。")
     (interpretation
       "相対順序も意味論の一部: handler(WithObserve(obs, body)) の配置では handler 本体が emit する効果は obs に不可視。フラットな『observer を最外へ積む』再インストールは過剰観測(重複 span 等)を生む。よって捕捉・再インストールは interleaved(innermost-first)で順序を保存する。")
     (interpretation
       "primitive は opt-in クエリ GetBoundaries(k)。GetHandlers(k) と対称で、継続を消費しない(multi-shot 安全)。既存 GetHandlers とその多数の利用箇所(try_handler, doeff-traverse 等)は不変(issue 論点 2 の opt-in 案を採用しつつ、scheduler は既定で継承する)。")]
  :decision
    [(rule R1 "doeff-vm bridge に GetBoundaries(k) を追加する: spawn-site から catching handler までの [\"handler\"|\"observer\", callable] を innermost-first で返す。catching handler の prompt boundary を最終エントリとして含む(GetHandlers と対称)。")
     (rule R2 "doeff.handler_utils.get_inner_boundaries(k) は GetBoundaries(k) の最終エントリ(呼び手自身)を落として返す。最終エントリが handler 種でなければ RuntimeError(fail-fast)。")
     (rule R3 "scheduler の Spawn は get_inner_boundaries で boundary stack を捕捉し、子タスク再構築時に kind に応じて WithHandler / WithObserve を innermost-first で再インストールする。handler と observer の相対 nesting 順序を保存する。")
     (rule R4 "再インストールされた boundary は子タスクの program が所有し、タスク終端(完了・失敗・キャンセル)で _release_task_refs により解放される。scheduler 内部効果(wrap_task の TaskCompleted 等)は boundary の外側で perform され、サブツリー observer から観測されない(issue 論点 3)。")
     (rule R5 "Mask boundary は本 ADR の対象外(従来どおり Spawn 非継承)。継承が必要になった場合は GetBoundaries の kind 追加として本 ADR を改訂する。")]
  :laws
    [(law spawn-observer-inheritance
       :statement "spawn_site_boundary(observer or handler) => reinstalled_on_child preserving_relative_order"
       :counterexamples
         [(counterexample "scheduled(WithObserve(obs, h(main))) で子タスクの効果が observed に含まれない")
          (counterexample "observer をフラットに最外へ積み、handler 本体が emit する効果まで観測してしまう")
          (counterexample "Spawn 経路が get_inner_handlers に退行し observer だけ脱落する")])]
  :enforcement
    [(defsemgrep scheduler-spawn-boundary-capture
       "doeff-scheduler-spawn-must-capture-boundaries"
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/scheduler.py"
         "source" "inner_handlers = yield get_inner_handlers(k)\n"}]
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/handlers.py"
         "source" "inner_hs = yield get_inner_handlers(k)\n"}])]
  :plans ["docs/adr/defadr_doeff_core_001_spawn_boundary_inheritance.hy"
          "tests/test_spawn_observer_inheritance.py"
          "packages/doeff-vm-core/src/continuation.rs (boundary_callables)"
          "packages/doeff-vm/src/do_expr.rs (GetBoundaries pyclass)"
          "packages/doeff-core-effects/doeff_core_effects/scheduler.py (Spawn boundary capture)"])
