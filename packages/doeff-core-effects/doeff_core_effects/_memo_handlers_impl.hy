;;; Memo layer handler — defhandler implementation.
;;;
;;; The Python-facing API stays in doeff_core_effects.memo_handlers, which
;;; delegates to memo-handler defined here.
;;;
;;; Storage may be a constructed DurableStorage, or a Program[DurableStorage]
;;; resolved lazily on the FIRST handled memo effect. Lazy storage lets
;;; backends whose construction itself requires effects (Ask / GetSecret
;;; credentials) stay completely untouched by runs that never hit this layer.
;;;
;;; Instance state is a per-instantiation cell created by the memo-handler
;;; factory, NOT a defhandler lazy-val: lazy-val state keys are scoped by
;;; (module, handler-name, var-name) only, so the three memo tiers of a
;;; typical stack (L1 / cheap / expensive) would collide on one shared cell.

(require doeff-hy.macros [defk deff <-])
(require doeff-hy.handle [defhandler])

(import doeff [DoExpr EffectBase UnhandledEffect])
(import doeff.program [Pure])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.memo-effects [
  MemoDeleteEffect MemoExistsEffect MemoGetEffect MemoPutEffect])
(import doeff_core_effects.memo-policy [RecomputeCost])
(import doeff_core_effects.storage [DurableStorage])
(import doeff_core_effects.memo-handlers [
  _matches-cost _effect-cost _storage-key])


(defn _handles? [effect cost]
  (_matches-cost (_effect-cost effect) cost))


(defk _ensure-store [cell storage]
  "Resolve the storage for this handler instance, once.
   Accepts a constructed DurableStorage (pre-seeded in the cell) or a
   Program[DurableStorage] resolved on first use."
  {:pre [(: cell list) (: storage #(DurableStorage DoExpr))]
   :post [(: % DurableStorage)]}
  (when (is (get cell 0) None)
    (<- s (if (isinstance storage DurableStorage) (Pure storage) storage))
    (when (not (isinstance s DurableStorage))
      (raise (TypeError
        (+ "memo-handler: lazy storage Program must return a "
           "DurableStorage, got " (. (type s) __name__)))))
    (setv (get cell 0) s))
  (get cell 0))


(defk _outer-exists [memo-effect]
  "Re-perform MemoExists to outer layers.
   UnhandledEffect = no further outer storage = definitively absent."
  {:pre [(: memo-effect EffectBase)] :post [(: % bool)]}
  (try
    (<- outer memo-effect)
    (bool outer)
    (except [UnhandledEffect] False)))


(defk _outer-get [memo-effect]
  "Re-perform MemoGet to outer layers.
   UnhandledEffect = no further outer storage AND not here → established
   miss signal: raise KeyError. Callers (@cache, make_memo_rewriter,
   sessioned-memo) catch KeyError as miss."
  {:pre [(: memo-effect EffectBase)]
   :post [(: % "memoized value — type depends on the wrapped program")]}
  (try
    (<- outer memo-effect)
    outer
    (except [UnhandledEffect] (raise (KeyError memo-effect.key)))))


(defk _broadcast-put [memo-effect]
  "Broadcast MemoPut to outer layers so every storage layer stores.
   UnhandledEffect = this layer was the outermost = broadcast complete."
  {:pre [(: memo-effect EffectBase)]
   :post [(: % "always None — broadcast side effect only")]}
  (try
    (<- _ memo-effect)
    None
    (except [UnhandledEffect] None)))


(defk _broadcast-delete [memo-effect]
  "Broadcast MemoDelete to outer layers so every storage layer deletes.
   Broadcast put / get write-through can place the same key in several
   layers; deleting only one layer would leave a stale value reachable
   by MemoGet. UnhandledEffect = outermost layer = broadcast complete."
  {:pre [(: memo-effect EffectBase)]
   :post [(: % "always None — broadcast side effect only")]}
  (try
    (<- _ memo-effect)
    None
    (except [UnhandledEffect] None)))


(defhandler _memo-layer-handler [cell storage cost label]
  "Caching-proxy memo layer. See memo-handler factory below."

  (MemoExistsEffect [key] :when (_handles? effect cost)
    (<- store (_ensure-store cell storage))
    (setv skey (_storage-key key))
    (<- exists (.exists store skey))
    (if exists
        (resume True)
        (do
          (<- outer (_outer-exists effect))
          (resume outer))))

  (MemoGetEffect [key] :when (_handles? effect cost)
    (<- store (_ensure-store cell storage))
    (setv skey (_storage-key key))
    (setv short-key (cut skey 0 16))
    (<- exists (.exists store skey))
    (if exists
        (do
          (<- value (.get store skey))
          (<- (SlogEffect f"[memo-layer:{label}] HIT key={short-key}..."))
          (resume value))
        (do
          (<- (SlogEffect
                f"[memo-layer:{label}] MISS key={short-key}... → re-performing"))
          (<- outer (_outer-get effect))
          (<- (.put store skey outer))
          (<- (SlogEffect
                f"[memo-layer:{label}] WRITE-THROUGH key={short-key}..."))
          (resume outer))))

  (MemoPutEffect [key value] :when (_handles? effect cost)
    (<- store (_ensure-store cell storage))
    (setv skey (_storage-key key))
    (<- (.put store skey value))
    (<- (SlogEffect
          f"[memo-layer:{label}] PUT key={(cut skey 0 16)}..."))
    (<- (_broadcast-put effect))
    (resume None))

  (MemoDeleteEffect [key] :when (_handles? effect cost)
    (<- store (_ensure-store cell storage))
    (setv skey (_storage-key key))
    (<- (.delete store skey))
    (<- (SlogEffect
          f"[memo-layer:{label}] DELETE key={(cut skey 0 16)}..."))
    (<- (_broadcast-delete effect))
    (resume None)))


(defn memo-handler [storage * [cost None] [name None]]
  "Handle memo effects as a caching proxy (defhandler implementation).

   On hit: resume with stored value.
   On miss: re-perform (delegates to outer handler) → cache result → resume.

   storage: a DurableStorage, or a Program[DurableStorage] resolved lazily
   on the first handled memo effect (runs that never touch this layer never
   resolve the backend config at all).
   cost: only handle effects matching this cost tier. None = handle all.
   name: label for log messages (defaults to the storage class name)."
  (setv cost-resolved (if (isinstance cost str) (RecomputeCost cost) cost))
  (setv eager (isinstance storage DurableStorage))
  (setv label (or name (if eager (. (type storage) __name__) "LazyStorage")))
  (setv cell [(if eager storage None)])
  (_memo-layer-handler cell storage cost-resolved label))
