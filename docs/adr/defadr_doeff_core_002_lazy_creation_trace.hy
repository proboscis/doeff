;;; Executable ADR: effect 生成ホットパスでの作成文脈捕捉は遅延・最小化する。
;;; トレーシング(class 1 ミドルウェア)の per-effect コストが hot path を
;;; 侵食してはならない。enforcement は C1 spike(捕捉サイト特定)後に追加する。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest defk <-])
(import doeff-adr.macros [fact interpretation counterexample])
(import doeff [Ask run])
(import linecache)


;; R1 回帰ガード用のホットパス・プローブ: エラーなしの Ask dispatch ループ。
(defk _probe-hot-loop []
  {:pre [] :post [(: % int)]}
  (setv total 0)
  (for [_ (range 50)]
    (<- v (Ask "x"))
    (setv total (+ total v)))
  total)


(defadr ADR-DOE-CORE-002
  :title "lazy creation trace(回帰ガード): effect 生成・dispatch のホットパスでは行テキスト解決(linecache)・文字列整形・スタック walk を行わない。捕捉の発火点は opt-in VM 内省命令(GetTraceback / GetExecutionContext)とエラー経路のみ — C1 spike(2026-07-14)で現行 main が既にこの性質を満たすと確認済みのため、本 ADR はこれを不変量として固定し将来の侵食から守る"
  :status "proposed"
  :scope ["doeff/traceback.py"
          "packages/doeff-core-effects"
          "docs/adr/defadr_doeff_core_002_lazy_creation_trace.hy"]
  :problem
    [(fact
       "下流実測(proboscis-ema、2026-06): 当時のシミュレーション interpreter において effect 生成毎の作成文脈捕捉(スタック検査+行テキスト解決)が実行時間の約32%を占め、全体で 3.7x 減速・関数呼び出し 4.1x。同 issue の結論は Rust 化を否定(interpreter core は全体の ~9% に過ぎない)。"
       :evidence "proboscis-ema VAULT issue『doeff Tracing Overhead Causes 3.7x Slowdown』(open, 2026-07-14 時点)")
     (fact
       "C1 spike(2026-07-14)の判定: 現行 main にこの捕捉コストは存在しない。捕捉は opt-in VM 内省命令(GetTraceback / GetExecutionContext、step.rs:320-341)とエラー経路(step_raise step.rs:115、unhandled_effect dispatch.rs:66-74)のみで発火し、通常の Perform→dispatch→Resume/Transfer では一切走らない。行テキスト解決(linecache)は doeff/traceback.py:105-112 のみで、呼び出し元は run() の except 節(doeff/run.py:16-34)= 描画時限定。100k 回の Ask ループ実測でも linecache / stack-walk / 文字列整形は 0 呼び出し(内訳: Rust VM core 48% / @do の generator 再構築グルー ~39% / effect 構築 ~7% / ユーザーコード ~7%)。"
       :evidence "scratchpad bench_ask_loop.py + ask_loop.pstats(2026-07-14 C1 spike); packages/doeff-vm/src/python_generator_stream.rs:371-409; packages/doeff-vm-core/src/vm.rs:76-213")
     (fact
       "残る字義上の非遅延は1箇所のみ: run() の例外経路 _enrich_exception_traceback / _merge_python_frames(doeff/run.py:38-100)が traceback.extract_tb を lookup_line=False なしで呼び、未捕捉例外1回につき linecache を先読みする。hot path 外だが R1 の字義には反する。"
       :evidence "doeff/run.py:38-100(C1 spike 2026-07-14)")
     (fact
       "一般則としての位置づけ: 『class 1 ミドルウェア(トレーシング)の代償は per-effect オーバーヘッド』の実例。現行 main は設計としてこれを回避済みであり、本 ADR の役割は修正ではなく回帰ガード(この性質を将来の変更から守る)である。下流の 3.7x は旧版に対する計測であり、pin 更新で解消される見込み — 下流 issue の再検証が残作業。"
       :evidence "docs/22-capability-classes.md『When Adoption Pays: The Evidence Rule』; C1 spike 2026-07-14")]
  :context
    [(interpretation
       "『call tree が自動で・タダで手に入る』(docs/20-why-effects-over-di.md の利点 #3)は生成時捕捉が常時 on である限り成立しない — タダなのはコード上であって実行時ではない。遅延できるものは遅延し(行テキスト・整形は描画時)、遅延できないもの(スタック情報はフレーム消滅前に取るしかない)は最小の生データに絞る。")
     (interpretation
       "C3 裁定前の仮説: 作成文脈捕捉はトレーシングという observability の関心事であり、handler スタックへ載せ替える案を検討した。本番障害の事後デバッグは『memo replay 下で tracing を有効にして再実行』を正規経路とする(Program-as-Value + class 1 replay による multi-shot 近似)。")
     (interpretation
       "C1 spike の帰結による再スコープ: R1 は現行 main で既に成立している(意図的な遅延化ではなく、捕捉が opt-in VM 内省命令 + エラー経路限定として設計されていたため)。よって本 ADR の enforcement は『直す』テストではなく『この性質が侵食されたら fail する』回帰ガードである。旧 R2(observability handler への opt-in 移行)は前提を失い縮小 — 残る C3 論点は『GetTraceback / GetExecutionContext を VM 組み込み命令のままにするか observability handler 経由に載せ替えるか』という独立の設計判断のみ。")
     (interpretation
       "C3 裁定(maintainer + frontier、2026-07-17): GetTraceback / GetExecutionContext は effect ではなく DoCtrl の VM 制御命令(packages/doeff-vm-core/src/do_ctrl.rs:82,86)であり、yield 分類時に VM が直接処理する(packages/doeff-vm/src/python_generator_stream.rs:659-664。GetTraceback はその場で Pure 値に解決)。effect dispatch を通らず handler スタックからは見えないため、GetHandlers / GetBoundaries と同族の VM 内省命令である。C1 spike で perf 根拠が消滅し、sandbox で内省を拒否する実需要も現存しない以上、Evidence Rule(docs/22-capability-classes.md)に従って Rust 改修・破壊的変更・handler 用特権 API のコストは払わない。")]
  :decision
    [(rule R1 "effect 生成・dispatch のホットパスでは linecache 読み・文字列整形・行テキスト解決・スタック walk を行わない。捕捉の発火点は opt-in VM 内省命令(GetTraceback / GetExecutionContext)とエラー経路のみに限る(現行 main の性質を不変量として固定する)。")
     (rule R2 "run() の例外経路 _merge_python_frames は traceback.extract_tb を lookup_line=False で呼び、行テキスト解決を描画時(format 時)まで遅延する(字義ギャップの解消、hot path 外の小修正)。")
     (rule R3 "GetTraceback / GetExecutionContext は現状どおり VM 内省命令(GetHandlers / GetBoundaries 族)として維持し、observability handler へ載せ替えない。将来 sandbox で内省を拒否する実需要が生じた場合は『内省命令を拒否できる実行モード』を別 ADR で設計する。@do の generator 再構築グルー(実測 ~39%)は本 ADR の対象外 — 別 issue として起票する。")]
  :laws
    [(law effect-creation-is-cheap
       :statement "for_all effect_creation: linecache_calls == 0 AND string_formatting == 0; human_readable_resolution happens_at render_time only"
       :counterexamples
         [(counterexample "effect 生成毎にスタック walk + linecache.getline で行テキストを即時解決する(下流で 3.7x 減速を実測した形)")
          (counterexample "トレース無効時にも捕捉コードが走る(フラグが表示だけを止め、捕捉を止めない)")])]
  :enforcement
    [(deftest test-adr-doe-core-002-no-linecache-on-hot-path
       ;; 回帰ガード(C1 spike 2026-07-14 で確認済みの性質を固定):
       ;; エラーなしの effect dispatch ループで行テキスト解決(linecache.getline)ゼロ。
       ;; per-effect の作成文脈捕捉が将来復活したら、ここが red になる。
       (import doeff_core_effects.handlers [reader])
       (setv calls [])
       (setv orig linecache.getline)
       (defn _counting [#* a #** k]
         (.append calls a)
         (orig #* a #** k))
       (setv (. linecache getline) _counting)
       (try
         (setv result (run ((reader {"x" 1}) (_probe-hot-loop))))
         (finally (setv (. linecache getline) orig)))
       (assert (= result 50) f"probe の結果が不正: {result}")
       (assert (= (len calls) 0)
               f"hot path で linecache.getline が {(len calls)} 回呼ばれた — ADR-DOE-CORE-002 R1"))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
