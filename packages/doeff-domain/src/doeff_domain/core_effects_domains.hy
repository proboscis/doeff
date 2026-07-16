;;; Dogfood: doeff-core-effects の effect 語彙を domain 宣言する (ADR-DOE-DOMAIN-001 D8)。
;;;
;;; 分割は doeff_core_effects.handlers / scheduler / http / memo / cache の
;;; handler 構造に沿った提案であり、妥当性は maintainer が PR レビューで判断する。
;;;
;;; - 生 Python handler(installer / factory)には handles() を後付け注釈する
;;;   (core-effects は編集しない — D5)。
;;; - defhandler 製 factory(http / memo)は注釈せずそのまま列挙し、
;;;   __doeff_body__ からの二層導出(D6)を dogfood する。
;;; - このモジュールは doeff-domain の中で唯一 doeff-core-effects に依存して
;;;   よいモジュール(D1、一方向)。import には dogfood extra が必要。

(require doeff-domain.macros [defdomain])

(import doeff_domain.registry [DomainLaw DomainTerm])
(import doeff_domain.introspect [handles])

(import doeff_core_effects.effects [Ask Get Put Local Listen Await Try
                                    WriterTellEffect SlogEffect])
(import doeff_core_effects.http-effects [HttpRequest])
(import doeff_core_effects.memo-effects [MemoGetEffect MemoPutEffect
                                         MemoDeleteEffect MemoExistsEffect])
(import doeff_core_effects.cache-effects [CacheGetEffect CachePutEffect
                                          CacheDeleteEffect CacheExistsEffect])
(import doeff_core_effects.scheduler [Spawn TaskCompleted Gather Wait Cancel Race
                                      CreatePromise CompletePromise FailPromise
                                      CreateSemaphore AcquireSemaphore
                                      ReleaseSemaphore CreateExternalPromise
                                      _SchedulerIntrospection scheduled])
(import doeff_core_effects.handlers [reader state writer slog-handler
                                     slog-discard-handler try-handler
                                     local-handler listen-handler await-handler
                                     lazy-ask env-var-ask])
(import doeff_core_effects.cache-handlers [cache-handler])
;; defhandler 製 factory は実装モジュールから直接列挙する(__doeff_body__ を持つ
;; 実物)。公開 wrapper(http_handlers / memo_handlers)は素の関数で構造情報を
;; 持たないため、導出の dogfood にはならない。
(import doeff_core_effects._http-handlers-impl [_http-production-handler
                                                _http-fixture-record-handler
                                                _http-fixture-replay-handler])
(import doeff_core_effects._memo-handlers-impl [_memo-layer-handler])


;; --- 生 Python handler への後付け注釈(D5)。注釈は「処理に参加する宣言」で
;; あり全域性の保証ではない。実態照合は E2/E3 の SEDA が担う。
((handles Ask) reader)
((handles Ask) env-var-ask)
((handles Ask Local) lazy-ask)
((handles Get Put) state)
((handles WriterTellEffect) writer)
((handles SlogEffect) slog-handler)
((handles SlogEffect) slog-discard-handler)
((handles Try) try-handler)
((handles Local) local-handler)
((handles Listen) listen-handler)
((handles Await) await-handler)
((handles CacheGetEffect CachePutEffect CacheExistsEffect) cache-handler)
((handles Spawn TaskCompleted Gather Wait Cancel Race
          CreatePromise CompletePromise FailPromise
          CreateSemaphore AcquireSemaphore ReleaseSemaphore
          CreateExternalPromise _SchedulerIntrospection) scheduled)


(defdomain doeff-reader
  :title "Reader 語彙 — 環境からの値の照会"
  :effects [Ask]
  :terms [(DomainTerm :name "Ask"
                      :home "doeff_core_effects.effects"
                      :description "環境キー照会の正典 effect")]
  :handlers [reader lazy-ask env-var-ask]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "reader / lazy_ask / env_var_ask が被覆する。lazy_ask は Local も処理する(doeff-scope 参照)。")


(defdomain doeff-state
  :title "State 語彙 — 可変状態の Get/Put"
  :effects [Get Put]
  :handlers [state]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain doeff-writer
  :title "Writer 語彙 — Tell の静かな蓄積"
  :effects [WriterTellEffect]
  :terms [(DomainTerm :name "Tell"
                      :home "doeff_core_effects.effects"
                      :description "WriterTellEffect の正典コンストラクタ")
          (DomainTerm :name "writer_log"
                      :home "doeff_core_effects.handlers"
                      :description "蓄積ログ読み出しの正典 Program(State 収集)")]
  :handlers [writer]
  :adrs ["ADR-DOE-DOMAIN-001" "ADR-DOE-CORE-EFFECTS-001"])


(defdomain doeff-slog
  :title "Slog 語彙 — 見えてこそ正しい observability"
  :effects [SlogEffect]
  :terms [(DomainTerm :name "slog"
                      :home "doeff_core_effects.effects"
                      :description "SlogEffect の正典コンストラクタ")]
  :handlers [slog-handler slog-discard-handler]
  :laws [(DomainLaw :name "slog-tell-types-disjoint"
                    :statement "wire_type(slog) = SlogEffect and wire_type(Tell) = WriterTellEffect and SlogEffect is_not WriterTellEffect"
                    :counterexamples ["writer() が slog の出力を collect する / slog_handler が Tell を consume する(旧 default_interpreter の実態)"])]
  :adrs ["ADR-DOE-DOMAIN-001" "ADR-DOE-CORE-EFFECTS-001"])


(defdomain doeff-error
  :title "Error 語彙 — Try による Ok/Err 化"
  :effects [Try]
  :handlers [try-handler]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain doeff-scope
  :title "Scoped env 語彙 — Local による環境の局所上書き"
  :effects [Local]
  :includes [doeff-reader]
  :handlers [local-handler lazy-ask]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "Local は Ask 語彙(doeff-reader)を参照して意味を持つ — includes は参照合成であり導入ではない(D3)。")


(defdomain doeff-listen
  :title "Listen 語彙 — effect の値フロー収集(tee)"
  :effects [Listen]
  :handlers [listen-handler]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain doeff-await
  :title "Async bridge 語彙 — coroutine の Await"
  :effects [Await]
  :handlers [await-handler]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain doeff-scheduler
  :title "Scheduler 語彙 — タスク・promise・semaphore の実行基盤"
  :effects [Spawn TaskCompleted Gather Wait Cancel Race
            CreatePromise CompletePromise FailPromise
            CreateSemaphore AcquireSemaphore ReleaseSemaphore
            CreateExternalPromise _SchedulerIntrospection]
  :handlers [scheduled]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "scheduled が単一の実行基盤 prompt として全 scheduler effect を被覆する。")


(defdomain doeff-http
  :title "HTTP 語彙 — HttpRequest の実行・記録・再生"
  :effects [HttpRequest]
  :handlers [_http-production-handler
             _http-fixture-record-handler
             _http-fixture-replay-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "handler は defhandler 製 factory — 処理集合は __doeff_body__ から導出される(D6 二層目)。")


(defdomain doeff-memo
  :title "Memo 語彙 — 階層キャッシュ proxy"
  :effects [MemoGetEffect MemoPutEffect MemoDeleteEffect MemoExistsEffect]
  :handlers [_memo-layer-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "MemoDeleteEffect は語彙として定義済みだがどの handler も処理していない(2026-07-17 実測のドリフト)。被覆検査の known_uncovered として申告済み — 処置は maintainer 裁定待ち。")


(defdomain doeff-cache
  :title "Cache 語彙 — 内容アドレスの永続キャッシュ"
  :effects [CacheGetEffect CachePutEffect CacheDeleteEffect CacheExistsEffect]
  :handlers [cache-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "CacheDeleteEffect は語彙として定義済みだがどの handler も処理していない(2026-07-17 実測のドリフト)。被覆検査の known_uncovered として申告済み — 処置は maintainer 裁定待ち。")
