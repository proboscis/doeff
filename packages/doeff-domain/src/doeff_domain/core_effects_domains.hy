;;; Dogfood: doeff-core-effects の effect 語彙を domain 宣言する (ADR-DOE-DOMAIN-001 D8)。
;;;
;;; 分割は doeff_core_effects.handlers / scheduler / http / memo / cache の
;;; handler 構造に沿った提案であり、妥当性は maintainer が PR レビューで判断する。
;;; 土台の語彙(file / http-server / sql / process / channel / compute /
;;; stop-signal)は effect の module ごとに 1 domain、Absent / Raise は束ねの
;;; 境目の受け手(maybe / result / on-raise / absent-as / absent-raises)で 1 domain。
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

;; 想定内の不在(Absent・BoundAbsent)と失敗(Raise)— 受け手は束ねの境目の生 Python 関数。
(import doeff_core_effects.effects [Absent Raise])
(import doeff_core_effects.outcomes [BoundAbsent maybe result on-raise absent-as
                                     absent-raises])

;; 土台の語彙と、その defhandler 製の答え手(注釈せず __doeff_body__ から導出 — D6)。
(import doeff_core_effects.file-effects [StatPath ReadText ReadBytes WriteText
                                         WriteBytes AppendText MakeDirectory
                                         ListDirectory WalkTree RemoveTree
                                         RenamePath CopyFile CopyTree
                                         AcquireLock ReleaseLock ReadDiskFree
                                         ReadMemoryFiles])
(import doeff_core_effects.os-file [os-file-handler])
(import doeff_core_effects.memory-file [memory-file-handler])
(import doeff_core_effects.rooted-file [rooted-file-handler])
(import doeff_core_effects.http-server-effects [HttpListen HttpNextRequest HttpReadBody
                                                HttpRespond HttpForward
                                                HttpShutdown WsAccept WsSendText
                                                WsForward WsClose
                                                TakeWsSendReport
                                                AppendHttpScript ReadHttpServed])
(import doeff_core_effects.aiohttp-http-server [aiohttp-http-server])
(import doeff_core_effects.scripted-http-server [scripted-http-server])
(import doeff_core_effects.sql-effects [SqlQuery SqlInsertRows SqlEnsureTables
                                        SqlTransaction SetSqlOutage])
(import doeff_core_effects.postgres-sql [postgres-sql-handler])
(import doeff_core_effects.clickhouse-http-sql [clickhouse-http-sql-handler])
(import doeff_core_effects.sqlite-sql [sqlite-sql-handler])
(import doeff_core_effects.process-effects [RunProcess ExecutableAt
                                            ReadEnvironment WorkingDirectory])
(import doeff_core_effects.os-process [subprocess-handler])
(import doeff_core_effects.scripted-process [scripted-process-handler])
(import doeff_core_effects.channel-effects [CreateChannel PutChannel TakeChannel])
(import doeff_core_effects.scheduler-channel [scheduler-channel-handler])
(import doeff_core_effects.compute-effects [Compute])
(import doeff_core_effects.thread-pool-compute [thread-pool-compute-handler])
(import doeff_core_effects.inline-compute [inline-compute-handler])
(import doeff_core_effects.stop-signal-effects [StopRequested RaiseStop])
(import doeff_core_effects.stop-signal-handlers [os-signal-stop-handler
                                                 scripted-stop-handler])


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
((handles CacheGetEffect CachePutEffect CacheDeleteEffect CacheExistsEffect) cache-handler)
((handles Spawn TaskCompleted Gather Wait Cancel Race
          CreatePromise CompletePromise FailPromise
          CreateSemaphore AcquireSemaphore ReleaseSemaphore
          CreateExternalPromise _SchedulerIntrospection) scheduled)
;; Absent / Raise の受け手は束ねの境目の関数(scope を包む installer と factory)。
;; BoundAbsent は Absent の子で、印を見て再開し分けるのは absent-as だけ — ほかの受け手には普通の Absent。
((handles Absent) maybe)
((handles Absent) absent-raises)
((handles Absent BoundAbsent) absent-as)
((handles Raise) result)
((handles Raise) on-raise)


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
  :docs "4 effect 全てを _memo-layer-handler が被覆する(defhandler 構造導出)。MemoDeleteEffect は 2026-07-17 実測のドリフト(未処理)だったが、maintainer 裁定 A(2026-07-18)により MemoDeleteEffect 節(broadcast delete)が実装され解消済み。")


(defdomain doeff-cache
  :title "Cache 語彙 — 内容アドレスの永続キャッシュ"
  :effects [CacheGetEffect CachePutEffect CacheDeleteEffect CacheExistsEffect]
  :handlers [cache-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "4 effect 全てを cache-handler が被覆する(handles 後付け注釈)。CacheDeleteEffect は 2026-07-17 実測のドリフト(未処理)だったが、maintainer 裁定 A(2026-07-18)により CacheDeleteEffect 分岐が実装され解消済み。")


(defdomain doeff-outcome
  :title "Outcome 語彙 — 想定内の不在(Absent)と失敗(Raise)"
  :effects [Absent BoundAbsent Raise]
  :terms [(DomainTerm :name "open_bind"
                      :home "doeff_core_effects.outcomes"
                      :description "<- と ! が宣言に従って答えを開き、不在を Absent・失敗を Raise にして出す 1 点")]
  :handlers [maybe absent-raises absent-as result on-raise]
  :adrs ["ADR-DOE-DOMAIN-001" "ADR-DOE-CORE-EFFECTS-003"]
  :docs "受け手は束ねの境目の関数で、どれも再開しない(absent-as が自分の字面の BoundAbsent を既定値で再開するのだけが例外)。maybe は Absent を Nothing に、absent-raises は Absent を Raise に、result は Raise を Err に、on-raise は型の合う Raise を業務の答えに畳む。")


(defdomain doeff-file
  :title "File 語彙 — 汎用の file system の読み書き"
  :effects [StatPath ReadText ReadBytes WriteText WriteBytes AppendText
            MakeDirectory ListDirectory WalkTree RemoveTree RenamePath
            CopyFile CopyTree AcquireLock ReleaseLock ReadDiskFree
            ReadMemoryFiles]
  :handlers [os-file-handler memory-file-handler rooted-file-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "os-file-handler(本物)と memory-file-handler(I/O なし)が答え、rooted-file-handler は path を 1 つの dir の下へ移して外側の答え手へ撃ち直す。ReadMemoryFiles は memory-file-handler の置き場を読む effect で、答えるのは memory-file-handler だけ。")


(defdomain doeff-http-server
  :title "HTTP server 語彙 — HTTP と WebSocket の待ち受け"
  :effects [HttpListen HttpNextRequest HttpReadBody HttpRespond HttpForward HttpShutdown
            WsAccept WsSendText WsForward WsClose TakeWsSendReport
            AppendHttpScript ReadHttpServed]
  :handlers [aiohttp-http-server scripted-http-server]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "aiohttp-http-server(本物・extra http-server)と scripted-http-server(I/O なし)が答える。AppendHttpScript / ReadHttpServed は台本を足す・受けた命令を読む effect で、答えるのは scripted-http-server だけ。")


(defdomain doeff-sql
  :title "SQL 語彙 — 汎用の SQL の問い合わせ・投入・transaction"
  :effects [SqlQuery SqlInsertRows SqlEnsureTables SqlTransaction SetSqlOutage]
  :handlers [postgres-sql-handler clickhouse-http-sql-handler sqlite-sql-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "postgres-sql-handler / clickhouse-http-sql-handler(本物)と sqlite-sql-handler(I/O なし)が答える。SqlTransaction の手順は sql_transaction の run-in-transaction を答え手が共有する(それ自体は handler ではない)。SetSqlOutage は模擬の障害を切り替える effect で、答えるのは sqlite-sql-handler だけ。")


(defdomain doeff-process
  :title "Process 語彙 — 子 process と自分の環境"
  :effects [RunProcess ExecutableAt ReadEnvironment WorkingDirectory]
  :handlers [subprocess-handler scripted-process-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "subprocess-handler(本物)と scripted-process-handler(I/O なし・台本)が 4 effect 全てに答える。")


(defdomain doeff-channel
  :title "Channel 語彙 — 届いた順に読む列"
  :effects [CreateChannel PutChannel TakeChannel]
  :handlers [scheduler-channel-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "答え手は scheduler-channel-handler 1 つ(I/O を持たず本番と模擬で同じ物)。scheduled の下で使う。")


(defdomain doeff-compute
  :title "Compute 語彙 — 純粋な計算を回して答えを値で受ける"
  :effects [Compute]
  :handlers [thread-pool-compute-handler inline-compute-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "thread-pool-compute-handler(thread の pool)と inline-compute-handler(I/O なし・同期)が答える。")


(defdomain doeff-stop-signal
  :title "Stop signal 語彙 — process の停止の求め"
  :effects [StopRequested RaiseStop]
  :handlers [os-signal-stop-handler scripted-stop-handler]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs "os-signal-stop-handler は本物の SIGINT / SIGTERM を StopRequested で読むだけ。RaiseStop で停止を起こすのは I/O なしの scripted-stop-handler だけ。")
