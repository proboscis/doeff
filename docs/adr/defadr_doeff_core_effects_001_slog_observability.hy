;;; Executable ADR: slog は observability effect — Writer(Tell/Listen)からワイヤ型を分離し、
;;; sink は「見える」がデフォルト、収集は Listen の値フローのみ(.log 属性 side-channel 全廃)。

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-CORE-EFFECTS-001
  :title "slog を SlogEffect として Writer から型分離し、slog_handler = stderr terminal sink、収集は Listen(types=(SlogEffect,)) の値フローに一本化する — handler install への mutable 収集属性(.log)は削除済み API として全面禁止"
  :status "proposed"
  :scope ["packages/doeff-core-effects"
          "doeff/cli/run_services.py"
          "packages/doeff-preset"
          "packages/doeff-time"
          "packages/doeff-docker"
          "docs/adr/defadr_doeff_core_effects_001_slog_observability.hy"]
  :problem
    [(fact
       "slog という名前(Go slog 由来の構造化ロギング語彙)は『yield すれば見える』という observability の期待を作るのに、実装は Writer の Tell と同一ワイヤ型(Slog = WriterTellEffect)で、slog_handler() は黙って list に集めるだけ。CLI の default_interpreter は slog_handler() を装備するがその収集結果は誰も読まずに捨てる — ユーザーは slog を yield しても何も見えない(2026-07-13 実測: 素の run() では UnhandledEffect、slog_handler 装備では完全な沈黙)。"
       :evidence "doeff/cli/run_services.py:191; packages/doeff-core-effects/doeff_core_effects/handlers.py:135-154; packages/doeff-core-effects/doeff_core_effects/effects.py:110-134")
     (fact
       "default_interpreter のスタックでは slog_handler が writer() より内側に居るため Tell() も含む全 WriterTellEffect を slog_handler が先に consume し、writer() は何も collect しない — Writer と slog の conflation はデフォルト構成で既に機能していない。"
       :evidence "doeff/cli/run_services.py:190-197 の wrap 順(reversed)")
     (fact
       "handler install に mutable 収集属性を生やす .log side-channel は削除済み API のはずが enforcement 不在で生存/再生した(erosion)。生産者: handlers.py:89(writer)と handlers.py:153(slog_handler)。読者: tests/test_core_effects.py:170-171、tests/effects/http_request_support.hy:85、packages/doeff-vm/tests/test_pyvm.py:210。handlers.py:76 の docstring は実在しない handler_log にも言及。"
       :evidence "packages/doeff-core-effects/doeff_core_effects/handlers.py:76,89,153; git log -S 'install.log'(削除を記録した ADR なし)")
     (fact
       "表示系 preset は現行 API に対して既に壊れている: log_display.py:94 は WriterTellEffect に存在しない effect.message を参照(現行は .msg/.kwargs)、旧 Pass() シグネチャも使用。tests/test_pass_then_typed_handler.py:54 も同属性を参照。sim_time.py:130 と docker dockerfile.hy:58 は WriterTellEffect に横から isinstance しており、型分離の影響面。"
       :evidence "packages/doeff-preset/src/doeff_preset/handlers/log_display.py:90-102; packages/doeff-time/src/doeff_time/handlers/sim_time.py:130; packages/doeff-docker/src/doeff_docker/handlers/dockerfile.hy:58")]
  :context
    [(interpretation
       "slog と Tell は正しいデフォルトが正反対の別エフェクトである: Writer(Tell)は『静かに蓄積してこそ正しい』データフロー、slog は『見えてこそ正しい』observability。同一ワイヤ型を共有する限り、どちらかのハンドラがもう一方の期待を裏切る構成(ログが黙って消える)が常に組めてしまう。業界の収束も同じ教訓を示す: Go slog と Python logging(lastResort)は『デフォルト sink = stderr』へ収束し、デフォルト nop の Rust log / OCaml Logs は有名な footgun になった。")
     (interpretation
       "Listen は tee(observer)であって sink ではない — listen_handler は collect した後 Pass で外へ流し続け、types 指定可能な汎用オブザーバである(handlers.py:205-236)。ゆえに sink の意味論変更と収集要求は独立であり、収集は Listen の値フロー (result, collected) で満たせる。handler オブジェクトに mutable 属性を生やす理由は存在しない。これは doeff-agents の『結果は AwaitOutcome.result のみ、side-channel ファイル禁止』と同型の境界規律。")
     (interpretation
       "VM の『unhandled effect = エラー』は結合核の不変量。Python logging の lastResort 相当を VM に特例として入れると、1 エフェクト型だけ不変量を破る浸食になる。可視性の保証はエントリポイント(CLI・テストハーネス)がハンドラスタックに sink を標準装備することで実現する。")]
  :decision
    [(rule R1 "ワイヤ型分離: slog(msg, **kwargs) は新設 SlogEffect(msg, **kwargs) を返す。Slog エイリアスは SlogEffect を指すよう付け替える。WriterTellEffect は Tell() 専用に戻り、writer() / Listen の既定対象(types=(WriterTellEffect,))は無変更。SlogEffect と WriterTellEffect に継承関係を作らない(isinstance で相互に捕まらないこと)。")
     (rule R2 "slog_handler() は SlogEffect の terminal sink: rich 非依存の 1 行整形(level kwarg があれば使用、なければ INFO、残り kwargs は key=value)を stderr へ出力して consume(Resume k None)する。『slog_handler を入れたらログは見える』がハンドラ契約。stdout は使わない(プログラム結果専用)。")
     (rule R3 "handler install に mutable 収集属性(.log 等)を生やす side-channel は全面禁止(削除済み API の正式記録)。writer() も .log を持たない Tell の terminal sink になる。収集が必要な場面は Listen(prog, types=(SlogEffect,)) または Listen(prog)(Tell 収集)の値フロー (result, collected) のみが公認経路。")
     (rule R4 "unhandled SlogEffect は従来どおり UnhandledEffect(VM に lastResort 特例を入れない)。可視性はエントリポイントの標準装備で保証する: doeff/cli/run_services.py の default_interpreter は slog_handler()(stderr sink)を装備し、テストハーネス(将来の doeff_interpreter fixture を含む)も display sink を既定にする — pytest は stderr を capture するのでテスト出力は汚れず、ログを assert するテストだけ Listen を使う。")
     (rule R5 "静音は明示 opt-in: slog_discard_handler()(SlogEffect を黙って consume する sink)を提供する。slog_capture_handler は導入しない — 収集結果を返す手段が side-channel 属性しかなく(R3 違反)、Listen の値フローと完全に重複するため。")
     (rule R6 "横から WriterTellEffect を isinstance している消費者は型分離に整合させる: sim_time のログ整形は SlogEffect を対象に変更、docker dockerfile.hy は用途に応じて対象型を明示、doeff-preset の表示系は現行 API(.msg/.kwargs、Pass(effect, k))へ修理して rich sink の差し替えとして提供するか退役する。存在しない effect.message 属性の参照は禁止。")]
  :laws
    [(law slog-tell-types-disjoint
       :statement "wire_type(slog) = SlogEffect and wire_type(Tell) = WriterTellEffect and SlogEffect is_not WriterTellEffect"
       :counterexamples
         [(counterexample "writer() が slog の出力を collect する / slog_handler が Tell を consume して Writer が飾りになる(旧 default_interpreter の実態)")
          (counterexample "SlogEffect を WriterTellEffect のサブクラスにして isinstance で両方に捕まる")])
     (law slog-is-visible-by-default
       :statement "installed(slog_handler) => every_yielded_SlogEffect_reaches_stderr"
       :counterexamples
         [(counterexample "sink が list に集めるだけで表示せず、収集結果も捨てられる(2026-07-13 以前の default_interpreter)")
          (counterexample "既定 sink が stdout に書き、プログラム結果と混ざる")])
     (law no-handler-side-channel
       :statement "handler_install_exposes_no_mutable_collection_attribute; capture_flows_as_values_via_Listen"
       :counterexamples
         [(counterexample "install.log = log で handler 属性から収集結果を読む(削除済み API の再生)")
          (counterexample "収集用に slog_capture_handler を新設し、結果を属性やファイルで返す")])
     (law unhandled-slog-is-loud
       :statement "no_handler_in_scope(SlogEffect) => UnhandledEffect; no_vm_lastresort_special_case"
       :counterexamples
         [(counterexample "VM が SlogEffect だけ stderr に流して実行を継続する(不変量の 1 型特例)")])]
  :enforcement
    ;; 実 pytest 群は tests/test_slog_semantics.py に landed(8 tests: stderr 可視性 /
    ;; level 整形 / Listen 既定に slog 不混入 / slog_handler の Tell pass-through /
    ;; Listen(types=(SlogEffect,)) 値フロー収集 / .log 属性不在 / unhandled loud /
    ;; slog_discard_handler 無音)。installed 版 semgrep rules は .semgrep.yaml
    ;; (no-handler-log-attribute / no-effect-message-attribute /
    ;;  no-structured-payload-on-writer-tell / no-writer-tell-alias-slog)。
    ;; ここは静的ガードの inline 自己検証。
    [(defsemgrep no-handler-log-attribute
       :languages ["generic"]
       :pattern "install.log ="
       :message "handler install への mutable 収集属性は ADR-DOE-CORE-EFFECTS-001 R3 違反(削除済み API)。収集は Listen(prog, types=(SlogEffect,)) の値フロー (result, collected) で行う。"
       :bad ["install.log = log  # expose for inspection"]
       :good ["return install"])
     (defsemgrep no-effect-message-attribute
       :languages ["python"]
       :pattern "isinstance($E, WriterTellEffect) and isinstance($E.message, $T)"
       :message "WriterTellEffect / SlogEffect に message 属性は存在しない(現行は .msg / .kwargs)。effect.message 参照は ADR-DOE-CORE-EFFECTS-001 R6 違反(壊れた preset の stale API)。他ドメイン effect の正当な .message フィールドと区別するため isinstance ガード付き形状で照合する。"
       :bad ["if isinstance(effect, WriterTellEffect) and isinstance(effect.message, dict):\n    pass"]
       :good ["if isinstance(effect, SlogEffect):\n    pass"])
     (defsemgrep no-writer-tell-alias-slog
       :languages ["python"]
       :pattern "from doeff_core_effects.effects import WriterTellEffect as Slog"
       :message "WriterTellEffect を Slog 名で別名 import するのは ADR-DOE-CORE-EFFECTS-001 R1 が退役させた conflation の再生(memo_handlers 2026-07-13 実例)。observability は SlogEffect / slog()。"
       :bad ["from doeff_core_effects.effects import WriterTellEffect as Slog"]
       :good ["from doeff_core_effects.effects import Slog"])
     (defsemgrep no-structured-payload-on-writer-tell
       :languages ["python"]
       :pattern "WriterTellEffect($MSG, **$KW)"
       :message "構造化ペイロード付きの WriterTellEffect 構築は ADR-DOE-CORE-EFFECTS-001 R1 違反。observability は SlogEffect(msg, **kwargs)、Tell は単一 message のみ。"
       :bad ["return WriterTellEffect(msg, **kwargs)"]
       :good ["return SlogEffect(msg, **kwargs)"])]
  :plans ["docs/doeff-2026-07-13-slog-semantics-architecture-plan.md"])
