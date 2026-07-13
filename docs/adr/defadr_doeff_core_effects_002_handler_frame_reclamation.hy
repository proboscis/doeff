;;; Executable ADR: ハンドラ節の最終制御命令(Transfer/Resume/Pass/Delegate)は
;;; ハンドラの最上位フレームから実行する — 入れ子 @do 委譲の途中で最終命令が走ると
;;; 委譲中の祖先フレームが永久保持され、terminal task sweep が構造的に破綻する。
;;; 末尾 resume は Transfer(即時解放)であり Resume(GC 依存)ではない。

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-CORE-EFFECTS-002
  :title "handler frame reclamation: ハンドラ節の最終制御命令(Transfer/TransferThrow/Resume/Pass/Delegate)は必ずハンドラ本体の最上位フレームから yield する — `yield sub-@do(effect, k)` 委譲の深部で最終命令を実行する形は、委譲中の全祖先フレームを回収不能のまま放置し、Task ハンドル weakref 経由で scheduler の terminal-entry sweep を永久に阻止する。末尾 resume は Transfer/TransferThrow を用いる(Resume 停止フレームはサイクル GC 待ちでしか回収されない)"
  :status "accepted"
  :scope ["packages/doeff-core-effects/doeff_core_effects/handlers.py"
          "packages/doeff-time/src/doeff_time/handlers"
          "packages/doeff-google-secret-manager/src/doeff_google_secret_manager/handlers/production.py"
          "packages/doeff-secret/src/doeff_secret/handlers.py"
          "tests/test_handler_tail_transfer_reclamation.py"
          "docs/adr/defadr_doeff_core_effects_002_handler_frame_reclamation.hy"]
  :problem
    [(fact
       "ライブ稼働(proboscis-ema burst-taker X5'/X6)で detect-to-submit レイテンシが経過時間に比例して悪化(2.87ms@0h → 79.31ms@4.5h)、RSS +97MB/2.5h。プロセス再起動でリセット。Rust 側 detect_internal_ms はフラット — 全ドリフトが Python/doeff 側。py-spy on-CPU は gc_collect_main + PyObject_GetAttr + generator 機構が支配的。"
       :evidence "proboscis-ema ISSUE-TRD-185; experiments/2026-07-13-doeff-scheduler-accum-repro/")
     (fact
       "ドメイン無関係の最小再現: 50ms ビートの `Spawn x2 keepalive(Await asyncio.sleep) + Gather + Delay` ループが、ビート毎に task-dict エントリ +2・suspended generator +5 を厳密に線形蓄積(200/400/600 beats → tasks 400/800/1200, generators 1003/2003/3003)。promise sweep は機能(有界 ~600)。"
       :evidence "proboscis-ema experiments/2026-07-13-doeff-scheduler-accum-repro/repro_accum_pure_doeff_20260713.hy + .out.txt")
     (fact
       "2026-07-14 の co_filename 付き forensics で保持フレームの正体を特定: 残留 generator は全て doeff-time async_time.py のものだった — wrapper `handler`(旧92行, `return (yield runtime.handle(effect, k))` の yield で停止)がビート毎 +4、dispatcher `handle`(旧71行, `yield self._handle_delay(effect, k)` の yield で停止)が +1。doeff_core_effects/handlers.py のフラットな await_handler のフレームは(末尾 Resume のままでも)gc.collect で回収されていた。"
       :evidence "2026-07-14 診断スクリプト実行結果: ('async_time.py','handler',92) 480/120beats, ('async_time.py','handle',71) 120/120beats; 修正前 handlers.py 変換単独ではリーク率 5/beat 不変")
     (fact
       "保持されたフレームは full gc.collect() を生き延びる(census は毎回 collect 後に計数)。停止フレームの locals が Task ハンドルを強参照し、scheduler.sweep_terminal_unobserved_entries の『全ハンドル weakref 死亡』条件が永久に成立しない — terminal task エントリ 400 個全てに生存 weakref がちょうど1個(参照先 Task)。"
       :evidence "proboscis-ema repro_accum_forensics_20260713.hy: tasks 400 全て terminal, handle_refs 全キー alive=1")
     (fact
       "対照実験: フラットな(委譲なし)ハンドラは末尾 `Resume` のままでもビートループで増加ゼロ(gc.collect 後計数)。ただし回収はサイクル GC 依存。Transfer 末尾は defhandler の TCO(doeff-hy handle.hy `_tail-resume-to-transfer`, _tco-seq が全クローズに適用済み)と同形で、フレームは参照カウントで即時解放される。"
       :evidence "tests/test_handler_tail_transfer_reclamation.py 2026-07-14 実測(flat-Resume 対照 growth=0); packages/doeff-hy/src/doeff_hy/handle.hy:399")
     (fact
       "入れ子委譲を排したフラット化 + Transfer 末尾で同一ビートループが完全フラット(generators 固定 3, tasks ~390 で sweep 稼働, objs 微減)。"
       :evidence "proboscis-ema repro_accum_transfer_fix_20260713.hy(doeff main f445a7d7 上で 2026-07-14 再検証); tests/test_handler_tail_transfer_reclamation.py")]
  :context
    [(interpretation
       "VM は最終制御命令(Transfer/Pass 等)を実行したフレーム自身は解放するが、その時点で `yield <sub-program>` 委譲中の祖先フレームは『二度と駆動されない』のに生きた root から到達可能なまま残る。つまり漏れの主因は Resume か Transfer かではなく、ハンドラ本体のフレーム構造(委譲の深さ)にある。祖先フレーム放置は VM 側の潜在課題でもあるが、doeff VM のフレーム/ファイバ機構への変更は Change Protocol の最重警戒領域であり、ハンドラ側契約(最上位フレームから最終命令)で不変量を回復するのが所有レイヤの正しい修正。VM 側で放置祖先フレームを回収する改善は将来の独立課題。")
     (interpretation
       "サブプログラム委譲そのものは禁止しない: 最終命令の前に完了する値返しサブプログラム(`(yield attempt())` や `_wait_for_time`)は、Transfer 実行時にスタック上に停止フレームを残さないため安全。禁止すべきは『effect と k を受け取り自分で resume/transfer/pass まで行うサブ @do への委譲』という dispatcher 分解パターン。")
     (interpretation
       "scheduler.py(handle_scheduler_effect)の Resume サイト 22 箇所は本 ADR の対象外: TailEval トランポリンが独自にフレームを駆動しており、ISSUE-TRD-185 forensics でも scheduler フレームの残留はゼロ。再現する障害なしに最繊細コードを『整合性のため』書き換えることは Change Protocol 違反。")
     (interpretation
       "Hy defhandler 製ハンドラは `_tail-resume-to-transfer` TCO がクローズ本体に適用され(handle.hy:399)、かつクローズ本体は単一フレームに展開されるため本 ADR に既に適合している。露出面は手書き Python @do ハンドラ。doeff-traverse/gemini/agentic/openai 等の未変換パッケージの `return (yield Resume(k, ...))` サイト(約90箇所)は R2 違反として残存 — burst-taker ライブスタック外のため本変更セットでは未修正、変換時に本 ADR の enforcement を repo 全体へ昇格する。")]
  :decision
    [(rule R1 "ハンドラ節の最終制御命令(Transfer/TransferThrow/Resume/ResumeThrow/Pass/Delegate)は、インストールされるハンドラ callable の最上位 @do フレームから直接 yield する。effect と k を渡して深部で最終命令を実行するサブ @do への `yield` 委譲(wrapper/dispatcher 分解)は禁止。")
     (rule R2 "末尾 resume(最終命令が k の resume で、その後に処理が無い)は Transfer / TransferThrow を用いる。Resume / ResumeThrow の末尾使用は、フラットフレームでもサイクル GC 待ちの遅延回収になるため hot path で禁止。resume 後に処理が残る真の非末尾(例: 旧 try_handler の包み込み)のみ Resume が正当。")
     (rule R3 "ハンドラを分解したい場合は『値を返すサブプログラム + 最上位フレームで Transfer』の形にする: `return (yield Transfer(k, (yield runtime.get-secret effect)))`。サブプログラムは k を受け取らず、最終命令の前に必ず完了する。(doeff-google-secret-manager production.py が参照実装。)")
     (rule R4 "scheduler.py 内部の Resume/ResumeThrow サイトは TailEval トランポリン管理下にあり本 ADR の対象外。フレーム残留の再現証拠なしに変更しない。")
     (rule R5 "本契約の実行時 enforcement は tests/test_handler_tail_transfer_reclamation.py の 2 テスト(stock スタックのフラット性 + 入れ子委譲反例の検出能力)。stock スタックにハンドラを追加する変更は同テストが回帰網になる。")]
  :laws
    [(law handler-final-instruction-from-top-frame
       :statement "for_all handler_clause: frame_yielding(final_control_instruction) == top_level_handler_frame; delegating_ancestor_frames_at_final_instruction == 0"
       :counterexamples
         [(counterexample "wrapper @do が `return (yield runtime.handle(effect, k))` で dispatcher に委譲し、leaf が Transfer する(2026-07-14 以前の doeff-time 全 3 ハンドラ) — wrapper+dispatcher フレームがビート毎に永久残留、terminal task sweep 停止、ライブ dts 2.9ms→79ms/4.5h")
          (counterexample "GSM production_handlers の `return (yield runtime.handle_get_secret(effect, k))` 形 — GetSecret 毎に dispatcher フレーム残留")])
     (law tail-resume-is-transfer
       :statement "for_all handler_clause: last_action_resumes_k => uses(Transfer | TransferThrow); Resume_parked_flat_frames は uncollectable ではないが GC サイクル依存であり hot path で不許可"
       :counterexamples
         [(counterexample "`result = yield Resume(k, value); return result`(2026-07-14 以前の handlers.py await_handler/state/lazy_ask 等 25 サイト) — フラットなら GC で回収されるが、回収がサイクル GC 頻度に律速され、hot path の割り当て圧を増やす")
          (counterexample "defhandler の TCO を Python 側だけ持たない非対称(Hy ハンドラは transfer、Python ハンドラは resume)")])
     (law terminal-task-entries-are-swept
       :statement "beat_loop(Spawn+Await+Gather+Delay) 下で scheduler tasks-dict の terminal エントリと suspended handler generator 数は有界(per-beat 増加 0)"
       :counterexamples
         [(counterexample "ビート毎 tasks +2 / generators +5 の線形蓄積(repro_accum_pure_doeff_20260713.hy) — 数時間で数万オブジェクト、GC 全走査時間がレイテンシに直乗り")])]
  :enforcement
    ;; 実行時 enforcement は tests/test_handler_tail_transfer_reclamation.py
    ;; (stock スタックのフラット性 assert + 入れ子委譲の反例が検出されることの
    ;; assert の対)。以下は静的ガードの inline 自己検証 — installed 版
    ;; .semgrep.yaml への昇格は、残存 R2 違反パッケージ(doeff-traverse 等)の
    ;; 変換完了後に行う(現状で repo 全体に張ると未変換パッケージが即时違反する)。
    [(defsemgrep no-resume-tail-oneliner-in-handlers
       :languages ["python"]
       :pattern "return (yield Resume($K, $V))"
       :message "末尾 resume は ADR-DOE-CORE-EFFECTS-002 R2 により Transfer を用いる: `return (yield Transfer(k, value))`。resume 後に処理が残る真の非末尾のみ Resume が正当(その場合この形にはならない)。"
       :bad ["return (yield Resume(k, value))"]
       :good ["return (yield Transfer(k, value))"])
     (defsemgrep no-effect-k-delegation-in-handler
       :languages ["python"]
       :pattern "return (yield $RT.$METHOD(effect, k))"
       :message "effect と k を深部サブ @do に渡して最終命令を委譲する dispatcher 分解は ADR-DOE-CORE-EFFECTS-002 R1 違反 — 委譲中の祖先フレームが永久残留する。値返しサブプログラム + 最上位フレーム Transfer(R3)に書き換える。"
       :bad ["return (yield runtime.handle_get_secret(effect, k))"]
       :good ["return (yield Transfer(k, (yield runtime.get_secret(effect))))"])]
  :plans [])
