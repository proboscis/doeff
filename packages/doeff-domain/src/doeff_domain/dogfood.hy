;;; doeff-core-effects vocabulary declarations used to dogfood doeff-domain.

(require doeff-domain.macros [defdomain])

(import doeff_domain.handlers [handles])
(import doeff_domain.registry [DomainLaw DomainTerm])
(import doeff_core_effects.cache-effects [
  CacheDeleteEffect CacheExistsEffect CacheGetEffect CachePutEffect])
(import doeff_core_effects.cache-handlers [cache-handler])
(import doeff_core_effects.effects [
  Ask Await Get Listen Local Put SlogEffect Try WriterTellEffect])
(import doeff_core_effects.handlers [
  await-handler env-var-ask lazy-ask listen-handler local-handler reader
  slog-discard-handler slog-handler state try-handler writer])
(import doeff_core_effects.http-effects [HttpRequest])
(import doeff_core_effects.http-handlers [http-fixture-handler http-production-handler])
(import doeff_core_effects.memo-effects [
  MemoDeleteEffect MemoExistsEffect MemoGetEffect MemoPutEffect])
(import doeff_core_effects.memo-handlers [memo-handler])
(import doeff_core_effects.scheduler [
  AcquireSemaphore Cancel CompletePromise CreateExternalPromise CreatePromise
  CreateSemaphore FailPromise Gather Race ReleaseSemaphore Spawn TaskCompleted Wait
  _SchedulerIntrospection scheduled])


;; Raw Python handlers have no structural declaration, so dogfood attaches the
;; E1 opt-in annotations without changing doeff-core-effects itself.
((handles Ask) reader)
((handles Ask Local) lazy-ask)
((handles Ask) env-var-ask)
((handles Local) local-handler)
((handles Get Put) state)
((handles WriterTellEffect) writer)
((handles Listen) listen-handler)
((handles Try) try-handler)
((handles Await) await-handler)
((handles SlogEffect) slog-handler)
((handles SlogEffect) slog-discard-handler)
((handles HttpRequest) http-production-handler)
((handles HttpRequest) http-fixture-handler)
((handles CacheGetEffect CachePutEffect CacheDeleteEffect CacheExistsEffect) cache-handler)
((handles MemoGetEffect MemoPutEffect MemoDeleteEffect MemoExistsEffect) memo-handler)
((handles
   Spawn TaskCompleted Gather Wait Cancel Race CreatePromise CompletePromise FailPromise
   CreateSemaphore AcquireSemaphore ReleaseSemaphore CreateExternalPromise
   _SchedulerIntrospection)
 scheduled)


(defdomain core-reader-domain
  :title "Reader and scoped environment"
  :effects [Ask Local]
  :terms [(DomainTerm
            :name "environment-key"
            :home "doeff_core_effects.effects"
            :description "A key resolved through Ask and optionally scoped by Local")]
  :handlers [reader lazy-ask env-var-ask local-handler]
  :laws [(DomainLaw
           :name "local-scope-does-not-escape"
           :statement "Local overrides apply only to the wrapped Program"
           :counterexamples ["an override remains visible after Local completes"])]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs ["packages/doeff-core-effects/doeff_core_effects/handlers.py"])


(defdomain core-state-domain
  :title "Keyed state"
  :effects [Get Put]
  :handlers [state]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain core-writer-domain
  :title "Writer accumulation"
  :effects [WriterTellEffect]
  :handlers [writer]
  :adrs ["ADR-DOE-CORE-EFFECTS-001" "ADR-DOE-DOMAIN-001"])


(defdomain core-observability-domain
  :title "Structured observability"
  :effects [SlogEffect]
  :handlers [slog-handler slog-discard-handler]
  :adrs ["ADR-DOE-CORE-EFFECTS-001" "ADR-DOE-DOMAIN-001"])


(defdomain core-control-domain
  :title "Nested program control"
  :effects [Listen Try]
  :handlers [listen-handler try-handler]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain core-async-domain
  :title "Python async bridge"
  :effects [Await]
  :handlers [await-handler]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain core-http-domain
  :title "HTTP transport"
  :effects [HttpRequest]
  :includes [core-async-domain core-observability-domain]
  :handlers [http-production-handler http-fixture-handler]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain core-cache-domain
  :title "Legacy cache storage"
  :effects [CacheGetEffect CachePutEffect CacheDeleteEffect CacheExistsEffect]
  :handlers [cache-handler]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain core-memo-domain
  :title "Cost-aware memo storage"
  :effects [MemoGetEffect MemoPutEffect MemoDeleteEffect MemoExistsEffect]
  :includes [core-observability-domain]
  :handlers [memo-handler]
  :adrs ["ADR-DOE-DOMAIN-001"])


(defdomain core-scheduler-domain
  :title "Cooperative scheduling"
  :effects [Spawn TaskCompleted Gather Wait Cancel Race CreatePromise CompletePromise
            FailPromise CreateSemaphore AcquireSemaphore ReleaseSemaphore
            CreateExternalPromise _SchedulerIntrospection]
  :handlers [scheduled]
  :adrs ["ADR-DOE-DOMAIN-001"]
  :docs ["packages/doeff-core-effects/doeff_core_effects/scheduler.py"])


(setv CORE-EFFECT-DOMAINS
  #(core-reader-domain
    core-state-domain
    core-writer-domain
    core-observability-domain
    core-control-domain
    core-async-domain
    core-http-domain
    core-cache-domain
    core-memo-domain
    core-scheduler-domain))
