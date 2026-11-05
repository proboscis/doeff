;;; Definition declarations — the :effects and :tags in the header dict of defk / deff / defp / defpp / defhandler.
;;;
;;; Purpose: the contract dict used to silently drop every key other than :pre / :post (writing :effects or :tags did nothing).
;;; Close the set of accepted keys, and keep :effects (the effect types the definition performs) and :tags (context and role)
;;; as attributes of the definition so that static checks and listing tools can read them.
;;;
;;;   {:pre [...] :post [...]
;;;    :effects [ReadRow PutRow]                    ;; list of effect type names (names resolvable in the module) — an empty list = performs no effects
;;;    :tags {:context "kanban" :role "program"}}   ;; context (a free-form name) and role (the closed list ROLES)
;;;
;;; Attributes of the definition:
;;;   __doeff_effects__  tuple of effect types (None if :effects is absent = not declared)
;;;   __doeff_tags__     DefinitionTags (None if :tags is absent)
;;;   __doeff_needs__    frozenset of required capability names (None if :needs is absent — ADR-DOE-CLUSTER-001 R4b)
;;;
;;; :needs #{"pg-network" "claude-cli"} = the names of the capabilities the definition (mainly a foundation handler and the Program that lines it up) requires of its execution target.
;;; Write capabilities, not placement names (kind=k3s, role=..., machine name). The value must be a set of string literals #{...} only (so it can be read statically).
;;;
;;; Checked at transform time (inside the macro expansion): unknown keys; :effects not being a list of names; :tags keys not being exactly :context and :role;
;;; values that are not string literals; a role outside ROLES. Each is a SyntaxError naming the file written and the definition.
(import dataclasses [dataclass])
(import hy)
(import collections.abc [Iterable])
(import hy.models [Dict List Object Set Symbol String Keyword])
(import doeff_hy._expansion_names [import-form])

;; The closed list of roles (layer order is core -> intent -> protocol):
;; core's type / judgment / program; intent (the types of things core asks of the outside — defeffect); protocol (a handler translating intents into the counterpart's language);
;; foundation (general-purpose I/O); entry (assembly). The old list's effect was replaced by intent, and translation by protocol.
;; The layer-entry roles are three — system (system declaration), process (handler lineup), main (thin main) (#1187, operator
;; 2026-09-29 plan A). Which role goes in which layer is decided by the consumer's declaration (:roles in architecture.hy); this file holds only the closed list of spellings.
;; entry is the spelling from before the move to the three, kept until consumers finish switching over.
(setv ROLES #("type" "judgment" "program" "intent" "protocol" "foundation" "entry" "system" "process" "main"))
;; Keys accepted by the contract dict.
(setv CONTRACT-KEYS #(":pre" ":post" ":effects" ":tags" ":needs"))
;; Keys accepted by the contract dict of a function (deff, defk) — CONTRACT-KEYS plus the type parameter :tp (`:tp [T]`; the type-check expansion makes it PEP 695 `def f[T]`,
;; and runtime checking erases it to object — _type-params in doeff_hy/macros.hy). defp and the like reject :tp.
;; :writes is the set of things the definition (a non-job writer — e.g. a task Program sent from a system job) writes to outside stores, in the same form as defsystem's
;; job :writes (a set of string literals, spelled <store>:<name>); it is checked at expansion time and does nothing at runtime. The consumer's
;; doeff-linter rule DOEFF207 reads the source spelling and counts the :writes of definitions reachable from a production entry as the writer's (
;; #3496). :reads is not accepted (there is no rule to check reads of non-job definitions).
(setv FUNCTION-CONTRACT-KEYS (+ CONTRACT-KEYS #(":tp" ":writes")))
;; Keys accepted by definitions whose header dict has no :pre / :post (defhandler).
(setv DECLARATION-KEYS #(":effects" ":tags" ":needs"))
(setv TAG-KEYS #(":context" ":role"))
;; Optional :tags keys. :spells = a claim that the definition exists to spell out an outside shape at one point; :reads = a claim that it exists to read an outside shape into a type at one point —
;; doeff-linter's DOEFF172 (mapping placement) does not count these as 'building a dict is the point' (made writable in #2299;
;; #2515 widened the shape names beyond json and added :reads for the reading side — cisco-c8's line-drawing decision, 2026-10-02). The value is from the closed list SPELLS (shape names).
;; [removed]
;; [removed]
(setv OPTIONAL-TAG-KEYS #(":spells" ":reads"))
(setv SPELLS #("json" "http" "env" "schema" "records"))


(defclass [(dataclass :frozen True)] DefinitionTags []
  "The context and role of a definition. context = the context name (a non-empty string); role = one of ROLES; spells = the outside shape it spells out; reads = the outside shape it reads into a type (both are one of
   SPELLS, None if not claimed)."
  (#^ str context)
  (#^ str role)
  (setv #^ (| str None) spells None)
  (setv #^ (| str None) reads None)
  (defn #^ None __post_init__ [self]
    (when (or (not (isinstance self.context str)) (not self.context))
      (raise (ValueError (.format "DefinitionTags.context must be a non-empty string: {!r}" self.context))))
    (when (not-in self.role ROLES)
      (raise (ValueError (.format "DefinitionTags.role must be one of {}: {!r}" (.join " / " ROLES) self.role))))
    (for [#(name value) [#("spells" self.spells) #("reads" self.reads)]]
      (when (and (is-not value None) (not-in value SPELLS))
        (raise (ValueError (.format "DefinitionTags.{} must be one of {}: {!r}" name (.join " / " SPELLS) value)))))))


(defn #^ None refuse-unknown-keys [#^ Dict contract #^ tuple allowed #^ str where]  ; cannot be a defk: a function called at macro expansion time
  "Raise SyntaxError if the contract dict has a key outside allowed (so keys are not silently dropped). where = the definition name used in the error message."
  (for [key (cut contract None None 2)]
    (when (not-in (str key) allowed)
      (raise (SyntaxError (.format "{}: header dict key {} is not accepted — accepted keys: {}" where (str key) (.join " " allowed)))))))


;; The Hy-side source of truth for how defrecord / defwire fields are read (the Rust-side source of truth is hy_index::fields in doeff-indexer — called by hy-index and doeff-linter).
;; That the two sources give the same answer is checked by both sides reading the same table of inputs, tests/data/record_field_cases.json.
(defn #^ (get list Symbol) field-targets [#^ (get Iterable Object) forms]  ; cannot be a defk: a function called at macro expansion time
  "From the list of forms in a record body, return the field names (symbols) in the order written — a bare symbol x, #^ T x, (#^ T x), and the
   annotated targets of (setv #^ T1 a v1 #^ T2 b v2 ...) (one per pair). The target of an unannotated (setv x v) is not a dataclass field (it is a class attribute), so it is not counted."
  (import hy.models [Expression])
  (setv annotated (fn [form] (when (and (isinstance form Expression) (= (len form) 3)
                                         (= (str (get form 0)) "annotate") (isinstance (get form 1) Symbol))
                               (get form 1)))
        out [])
  (for [form forms]
    (cond
      (isinstance form Symbol) (.append out form)
      (annotated form) (.append out (annotated form))
      (and (isinstance form Expression) (= (len form) 1)) (when (annotated (get form 0)) (.append out (annotated (get form 0))))
      (and (isinstance form Expression) (>= (len form) 2) (= (str (get form 0)) "setv"))
        (for [target (cut form 1 None 2)]
          (when (annotated target) (.append out (annotated target))))))
  out)


(defn #^ (| Object None) declared-value [#^ Dict contract #^ str key]  ; cannot be a defk: a function called at macro expansion time
  "The form of the value of key in the contract dict (None if absent)."
  (setv found None)
  (for [#(k v) (zip (cut contract None None 2) (cut contract 1 None 2))]
    (when (= (str k) key)
      (setv found v)))
  found)


(defn #^ Object effects-form [#^ (| Object None) form #^ str where]  ; cannot be a defk: a function called at macro expansion time
  "The form of the `:effects` value -> a form that builds a tuple of effect types at runtime (a None form if absent)."
  (cond
    (is form None) 'None
    (not (isinstance form List))
      (raise (SyntaxError (.format "{}: :effects must be a list of effect type names (in the form [ReadRow PutRow]): {}" where (hy.repr form))))
    True
      (do
        (for [item form]
          (when (not (isinstance item Symbol))
            (raise (SyntaxError (.format "{}: elements of :effects must be effect type names: {}" where (hy.repr item))))))
        `(doeff_hy._declarations.effect-types ~where (tuple [~@form])))))


(defn #^ (| str None) effect-refusal [#^ object item]  ; cannot be a defk: called when the head of a macro-expanded definition loads the module (outside a Program)
  "The reason item cannot be written in :effects (None if it can). What can be written is an effect type, or a function that makes an effect (its return annotation is an effect type —
   the shape of doeff_time's GetTime / Delay and doeff's Tell)."
  (import typing)
  (import doeff [EffectBase])
  (setv effect-type? (fn [t] (and (isinstance t type) (issubclass t EffectBase))))
  (cond
    (isinstance item type) (if (effect-type? item) None "class that does not inherit EffectBase")
    (not (callable item)) "value that is neither a type nor a function"
    True (try
           (if (effect-type? (.get (typing.get-type-hints item) "return"))
               None
               "function whose return annotation is not an effect type")
           (except [e #(NameError TypeError)]
             (.format "function whose return annotation cannot be resolved ({}: {})" (. (type e) __name__) e)))))


(defn #^ tuple effect-types [#^ str where #^ tuple items]  ; cannot be a defk: called when the head of a macro-expanded definition loads the module (outside a Program)
  "Check the value of `:effects` at definition time and return it — a name that is neither an effect type nor a function making an effect (a function, a value, a class that is not an effect) is refused
   with a TypeError naming the definition and the reason (#800 — it used to check only that the value was a list of names and silently let anything through)."
  (for [item items]
    (setv refusal (effect-refusal item))
    (when (is-not refusal None)
      (raise (TypeError (.format "{}: {!r} in :effects cannot be written ({}) — write a class inheriting EffectBase, or a function whose return annotation is an EffectBase type"
                                 where item refusal)))))
  items)


(defn #^ Object tags-form [#^ (| Object None) form #^ str where]  ; cannot be a defk: a function called at macro expansion time
  "Check the `:tags` value form at transform time and return a form that builds a DefinitionTags at runtime (a None form if absent)."
  (when (is form None)
    (return 'None))
  (when (not (isinstance form Dict))
    (raise (SyntaxError (.format "{}: :tags must be a dict {{:context \"...\" :role \"...\"}}: {}" where (hy.repr form)))))
  (setv keys (lfor k (cut form None None 2) (str k)))
  (when (!= (sorted (lfor k keys :if (not-in k OPTIONAL-TAG-KEYS) k)) (sorted TAG-KEYS))
    (raise (SyntaxError (.format "{}: the keys of :tags must be exactly :context and :role (omittable keys: {}): {}" where (.join " " OPTIONAL-TAG-KEYS)
                                 (.join " " keys)))))
  (setv context (declared-value form ":context")
        role (declared-value form ":role")
        spells (declared-value form ":spells")
        reads (declared-value form ":reads"))
  (for [#(name value) [#(":context" context) #(":role" role)]]
    (when (not (isinstance value String))
      (raise (SyntaxError (.format "{}: {} in :tags must be a string literal: {}" where name (hy.repr value))))))
  (when (not (str context))
    (raise (SyntaxError (.format "{}: :context in :tags is empty" where))))
  (when (not-in (str role) ROLES)
    (raise (SyntaxError (.format "{}: :role {!r} in :tags is not any of {}" where (str role) (.join " / " ROLES)))))
  (for [#(name value) [#(":spells" spells) #(":reads" reads)]]
    (when (and (is-not value None) (not (and (isinstance value String) (in (str value) SPELLS))))
      (raise (SyntaxError (.format "{}: {} in :tags must be a string literal that is one of {}: {}" where name (.join " / " SPELLS) (hy.repr value))))))
  `(doeff_hy._declarations.DefinitionTags :context ~context :role ~role
                                         ~@(if (is spells None) [] [':spells spells])
                                         ~@(if (is reads None) [] [':reads reads])))


;; Keys accepted by the header dict of defeffect (:answer and :tags are required; :fields defaults to no fields; :pre is a check at construction time).
;; :absent / :failure / :value divide up the answer type (ADR-DOE-CORE-EFFECTS-003 R5 — all optional; each is a list of elements of the :answer union).
;; :runs-carried is a list of the names of fields holding a Program that the answerer runs at the place where the effect was performed (under the handler there)
;; (read by runs-carried-of in doeff_core_effects._effects).
;; :tp is the type parameters (`:tp [T]` — same form as :tp of defk / deff; the type-check expansion makes the class PEP 695 `class E[T]`, and :answer and the field types read T.
;; The runtime expansion emits no type parameters — T is read from the module's `(val T (TypeVar "T"))`).
(setv EFFECT-KEYS #(":fields" ":answer" ":tags" ":pre" ":absent" ":failure" ":value" ":runs-carried" ":tp"))
;; Keys for dividing up the answer. <- turns the answer of :absent into Absent and the answer of :failure into Raise(answer), and performs it in the caller's scope.
;; :value is the answer handled as ordinary business (a mark that it is not declared a failure); an element written in none of them is a success.
(setv OUTCOME-KEYS #(":absent" ":failure" ":value"))


(defn #^ (get dict #(str (get list Object))) outcome-type-forms [#^ Dict contract #^ (| Object None) answer #^ str where]  ; cannot be a defk: a function called at macro expansion time (deff is defined by doeff_hy.macros, and macros.hy imports this module, so it cannot be required)
  "Check :absent / :failure / :value of defeffect and turn them into a per-key list of type forms (empty if not written).
   Elements are type names or None. If :answer is a union form (| A B ...) or a single name, refuse at expansion time elements that are not in it and elements that overlap between keys
   (any other :answer is refused at definition time by Outcomes.declare)."
  (setv members (cond
                  (and (isinstance answer hy.models.Expression) (> (len answer) 0) (= (str (get answer 0)) "|"))
                    (lfor m (cut answer 1 None) (hy.repr m))
                  (isinstance answer Symbol) [(hy.repr answer)]
                  True None)
        seen {}
        out {})
  (for [key OUTCOME-KEYS]
    (setv form (declared-value contract key))
    (when (and (is-not form None) (not (isinstance form List)))
      (raise (SyntaxError (.format "{}: {} must be a list of elements of :answer (in the form [Missing]): {}" where key (hy.repr form)))))
    (for [item (or form [])]
      (when (not (isinstance item Symbol))
        (raise (SyntaxError (.format "{}: elements of {} must be type names or None: {}" where key (hy.repr item)))))
      (when (and (is-not members None) (not-in (hy.repr item) members))
        (raise (SyntaxError (.format "{}: {} in {} is not an element of :answer {}" where key (str item) (hy.repr answer)))))
      (when (in (str item) seen)
        (raise (SyntaxError (.format "{}: {} is in both {} and {}" where (str item) (get seen (str item)) key))))
      (setv (get seen (str item)) key))
    (setv (get out key) (list (or form []))))
  out)


(defn #^ (get list Object) bound-answer-methods [#^ Object answer #^ (get dict #(str (get list Object))) outcome-types]  ; cannot be a defk: a function called at macro expansion time
  "For the type-check expansion: a list of forms for a method `__iter__` placed on the class of an effect that declares an answer division (:absent, :failure)
   (empty when not placed). The return type = the :answer union minus the :absent and :failure elements — the type of the name `<-` binds;
   the return type is BoundRun in doeff_hy/_static_types.pyi (`Program[T, E]` reads T from `__iter__`). The runtime `<-` turns absence into Absent and
   failure into Raise and performs it in the caller's scope, so only success and :value elements reach the bound name. The Answer of the effect's base `EffectBase[Answer]`
   stays the whole declaration (handlers answer with values — R4). Not placed when :answer is neither a union nor a single name, or when every element is removed
   (the bound name's type stays the whole answer)."
  (import doeff_hy._static_view [static-view-enabled])
  (setv members (cond
                  (and (isinstance answer hy.models.Expression) (> (len answer) 0) (= (str (get answer 0)) "|"))
                    (list (cut answer 1 None))
                  (isinstance answer Symbol) [answer]
                  True None)
        removed (sfor key #(":absent" ":failure") item (get outcome-types key) (hy.repr item)))
  (if (and (static-view-enabled) removed (is-not members None))
      (let [kept (lfor m members :if (not-in (hy.repr m) removed) m)]
        (if kept
            [`(defn [] #^ (get _doeff_BoundRun ~(if (= (len kept) 1) (get kept 0) `(| ~@kept))) __iter__ [self] (raise NotImplementedError))]
            []))
      []))


(defn #^ (get tuple #((get list Object) (get list str))) effect-field-forms [#^ (| Object None) form #^ str where]  ; cannot be a defk: a function called at macro expansion time
  "Check `:fields [(: name type) (: name type default) ...]` of defeffect and turn it into a list of dataclass field forms and a list of field names
   (both empty if absent). A field without a default cannot follow a field with a default (the dataclass ordering rule is refused at expansion time)."
  (when (is form None)
    (return #([] [])))
  (when (not (isinstance form List))
    (raise (SyntaxError (.format "{}: :fields must be a list of (: name type) or (: name type default): {}" where (hy.repr form)))))
  (setv out [] names [] defaulted None)
  (for [item form]
    (when (not (and (isinstance item hy.models.Expression) (in (len item) #(3 4))
                    (isinstance (get item 0) Keyword) (= (str (get item 0)) ":")
                    (isinstance (get item 1) Symbol)))
      (raise (SyntaxError (.format "{}: elements of :fields must be (: name type) or (: name type default): {}" where (hy.repr item)))))
    (setv field-name (str (get item 1)))
    (when (in field-name names)
      (raise (SyntaxError (.format "{}: field {} of :fields is duplicated" where field-name))))
    (.append names field-name)
    (cond
      (= (len item) 4)
        (do (setv defaulted field-name)
            (.append out `(setv #^ ~(get item 2) ~(get item 1) ~(get item 3))))
      (is-not defaulted None)
        (raise (SyntaxError (.format "{}: field {} of :fields has no default, so place it before the field with a default, {}" where field-name defaulted)))
      True
        (.append out `(#^ ~(get item 2) ~(get item 1)))))
  #(out names))


(defn #^ (get list Symbol) type-param-symbols [#^ (| Object None) form #^ str where]  ; cannot be a defk: a function called at macro expansion time (also reads :tp of the defk / deff contract — _type-params in macros.hy)
  "Turn the form of the value of `:tp [T ...]` in the contract dict (None if absent) into a list of type-parameter symbols (empty if not written). The only accepted shape is a non-empty list of
   non-overlapping symbols — a value that is not a sequence of names, or overlapping names, is a SyntaxError (the :tp of defk, deff and defeffect are refused by the same rule)."
  (cond
    (is form None) []
    (and (isinstance form List) (> (len form) 0)
         (all (gfor p form (isinstance p Symbol)))
         (= (len (sfor p form (str p))) (len form)))
      (list form)
    True
      (raise (SyntaxError (.format "{}: :tp must be a sequence of type-parameter names (e.g. `:tp [T]`, non-overlapping names) — got {}" where (hy.repr form))))))


(defn #^ (get list str) runs-carried-names [#^ (| Object None) form #^ (get list str) names #^ str where]  ; cannot be a defk: a function called at macro expansion time
  "Check :runs-carried of defeffect and turn it into a sorted list of the names (Python attribute names) of fields holding a Program that the answerer runs at the place where the effect was performed
   (empty if not written). The only accepted shape is a list of :fields field names — a name with no such field is refused at expansion time."
  (when (is form None)
    (return []))
  (when (not (isinstance form List))
    (raise (SyntaxError (.format "{}: :runs-carried must be a list of :fields field names (in the form [program]): {}" where (hy.repr form)))))
  (for [item form]
    (when (not (and (isinstance item Symbol) (in (str item) names)))
      (raise (SyntaxError (.format "{}: {} in :runs-carried is not a field name of :fields (fields = {})" where (hy.repr item) names)))))
  (sorted (sfor item form (hy.mangle (str item)))))


(defn #^ Object answer-value-form [#^ Object answer]  ; cannot be a defk: a function called at macro expansion time
  "Build the form of the value placed in __doeff_answer__ in the body of defeffect. At runtime it is the :answer expression itself (the type of the value a handler returns with resume).
   In the type-check expansion (doeff_hy/_static_view.py) it is `cast(object, (A, B, ...))` (a tuple of the elements of the :answer union; a single
   element if not a union): pyright also evaluates `A | B` in a value position as a type, which turns bare generics (`dict | X` — reportMissingTypeArgument) and
   `Callable | None` (reportOperatorIssue) red, but a tuple of the elements only reads the names. cast keeps the attribute type from becoming Unknown even if an element has a name of unknown type
   (a name imported from a module without a stub).
   The alias _doeff-cast for cast is imported by defeffect-form."
  (import doeff_hy._static_view [static-view-enabled])
  (if (static-view-enabled)
      `(_doeff-cast object
                    #(~@(if (and (isinstance answer hy.models.Expression) (> (len answer) 0) (= (str (get answer 0)) "|"))
                            (cut answer 1 None)
                            [answer])))
      answer))


(defn #^ Object defeffect-form [#^ Symbol name #^ (| String None) docstring #^ Dict contract #^ str where #^ (get list Object) pre-code]  ; cannot be a defk: a function called at macro expansion time
  "Expansion of defeffect: build the forms that place a frozen dataclass inheriting EffectBase (with a ClassVar __doeff_answer__ in the body = the type of the value a handler returns with resume),
   and the attributes __doeff_tags__ and __doeff_defeffect__ (a mark that it was made by defeffect — read by the :effects check of defk).
   pre-code = the list of statements made from :pre by the same rules as defk (made by _contract-code in macros.hy). If non-empty, place it in __post_init__
   and make the field names that :pre refers to readable as local names (so that checks of the closed vocabulary and element types refuse at construction time)."
  (refuse-unknown-keys contract EFFECT-KEYS where)
  (setv answer (declared-value contract ":answer")
        tags (declared-value contract ":tags"))
  (when (is answer None)
    (raise (SyntaxError (.format "{}: :answer (the type of the value a handler returns with resume) is required" where))))
  (when (is tags None)
    (raise (SyntaxError (.format "{}: :tags {{:context \"...\" :role \"...\"}} is required" where))))
  (setv #(fields names) (effect-field-forms (declared-value contract ":fields") where))
  (when (and pre-code (not names))
    (raise (SyntaxError (.format "{}: :pre checks fields, so it cannot be written on an effect without :fields" where))))
  ;; Division of the answer (R5) — writing even one places __doeff_outcomes__ (if none is written it is not placed = <- does not convert the answer).
  (setv outcome-types (outcome-type-forms contract answer where))
  (setv outcomes
    (if (any (.values outcome-types))
        [`(import doeff_core_effects._outcomes)
         `(setattr ~name "__doeff_outcomes__"
                   (doeff_core_effects._outcomes.Outcomes.declare
                     ~(str name) ~answer
                     #(~@(get outcome-types ":absent"))
                     #(~@(get outcome-types ":failure"))
                     #(~@(get outcome-types ":value"))))]
        []))
  (setv runs-carried (runs-carried-names (declared-value contract ":runs-carried") names where))
  (setv type-params (type-param-symbols (declared-value contract ":tp") where))
  ;; Bind only the fields that :pre refers to (same shape as :check of defrecord). Binding every field would turn fields :pre does not use into
  ;; unused variables (reportUnusedVariable) red in the type-check expansion, which the writer cannot fix. A dotted symbol like `value.x`
  ;; is read by its head name.
  (setv seen (set)
        pending (list pre-code))
  (while pending
    (setv form (.pop pending)
          head (when (isinstance form Symbol) (get (.split (str form) ".") 0)))
    (cond
      head (.add seen (hy.mangle head))
      (and (isinstance form hy.models.Sequence) (not (isinstance form hy.models.String))) (.extend pending form)))
  (setv post-init
    (if pre-code
        [`(defn __post-init__ [self]
            ~@(lfor n names :if (in (hy.mangle n) seen) `(setv ~(Symbol n) (. self ~(Symbol n))))
            ~@pre-code
            None)]
        []))
  ;; Write the functions at expansion time (same shape as 424a87f58 of defrecord): let dataclass produce only the field tables (__dataclass_fields__,
  ;; __match_args__, the refusal of mutable defaults, __replace__), while the expansion writes here __init__ (positional and named arguments; defaults are values evaluated once in the class body),
  ;; __repr__, __eq__, __hash__, and the frozen __setattr__ / __delattr__ — the functions go into the .pyc as module code, and at load
  ;; time dataclass does not exec (compile) the function source. When a field has a field call as its default, and in the type-check projection (static-view), the
  ;; existing form (dataclass makes the functions) is kept. The functions read the defined type via `__class__` and zero-argument super rather than by name (a type name written
  ;; in the class body cannot be looked up from a method — same as the note on class-form in record.hy).
  (import doeff_hy._static_view [static-view-enabled])
  (setv items (or (declared-value contract ":fields") [])
        field-call? (fn [item] (and (= (len item) 4) (isinstance (get item 3) hy.models.Expression) (get item 3)
                                    (isinstance (get (get item 3) 0) Symbol)
                                    (in (str (get (get item 3) 0)) #("field" "dataclasses.field"))))
        written (not (or (static-view-enabled) (any (gfor item items (field-call? item))))))
  (setv self-sym (hy.gensym "self")
        other (hy.gensym "other")
        attr (hy.gensym "name")
        value (hy.gensym "value")
        syms (lfor item items (get item 1))
        params (lfor item items (if (= (len item) 4)
                                    `(annotate [~(get item 1) ~(get item 1)] ~(get item 2))
                                    `(annotate ~(get item 1) ~(get item 2))))
        guard (if syms
                  `(or (is (type ~self-sym) __class__) (in ~attr #{~@(lfor s syms (String (hy.mangle (str s))))}))
                  `(is (type ~self-sym) __class__)))
  (setv methods
    (if written
        [`(defn __init__ [~self-sym ~@params]
            ;; Place the fields via an alias of the builtin object — so that a field named object (an argument) does not hide the builtin name (same as dataclass's
            ;; __dataclass_builtins_object__).
            ~@(lfor s syms `(_doeff-object.__setattr__ ~self-sym ~(hy.mangle (str s)) ~s))
            ~@(if post-init [`(.__post-init__ ~self-sym)] [])
            None)
         `(defn [(doeff_hy._record_runtime.recursive-repr)] __repr__ [~self-sym]
            (.format ~(+ "{}(" (.join ", " (gfor s syms (+ (hy.mangle (str s)) "={!r}"))) ")") (. ~self-sym __class__ __qualname__)
                     ~@(lfor s syms `(. ~self-sym ~s))))
         ;; Same shape as __eq__ of dataclass (Python 3.14): true for the same object, otherwise per-field == joined with and (comparing tuples differs from this for NaN and an == that
         ;; returns a non-boolean).
         `(defn __eq__ [~self-sym ~other]
            (cond
              (is ~self-sym ~other) True
              (is (. ~other __class__) (. ~self-sym __class__))
                (and ~@(or (lfor s syms `(= (. ~self-sym ~s) (. ~other ~s))) ['True]))
              True NotImplemented))
         `(defn __hash__ [~self-sym]
            (hash #(~@(lfor s syms `(. ~self-sym ~s)))))
         `(defn __setattr__ [~self-sym ~attr ~value]
            (when ~guard
              (raise (doeff_hy._record_runtime.FrozenInstanceError (.format "cannot assign to field {!r}" ~attr))))
            (.__setattr__ (super) ~attr ~value))
         `(defn __delattr__ [~self-sym ~attr]
            (when ~guard
              (raise (doeff_hy._record_runtime.FrozenInstanceError (.format "cannot delete field {!r}" ~attr))))
            (.__delattr__ (super) ~attr))]
        []))
  `(do
     (import dataclasses [dataclass :as _doeff-dataclass])
     (import doeff [EffectBase :as _doeff-effect-base])
     (import doeff_hy._declarations)
     ;; The answer type is placed by `__doeff_answer__: ClassVar[object] = ...` in the class body. The type-check expansion
     ;; drops the bookkeeping setattr (`__doeff_...__`), so placing it by setattr made the :answer expression vanish from type checking, and a name imported
     ;; only for the answer type turned into a reportUnusedImport red (removing it breaks runtime, so the writer cannot fix it). The assignment in the body is
     ;; not bookkeeping, so it stays in type checking and the name is read as a used import. The annotation is object, so the way the answer type is read
     ;; (the type bound by `<-`, the type of resume in a handler) does not change. ClassVar does not become a dataclass field (in pyright and at runtime alike).
     ;; The spelling and placement of the alias are looked up from the table of expansion names (same as defwire — record.hy). The type-annotation body written in the class body
     ;; looks names up from the module level, so the type-check projection moves this import, with the spelling in the table, to the module level (static_check._synthesized_import).
     ~(import-form ["ClassVar" "cast"])
     ~@(if written [`(import doeff_hy._record_runtime) `(import builtins [object :as _doeff-object])] [])
     ;; Type parameters (:tp) go only in the type-check expansion (`class E[T](...)` — the PEP 695 spelling is not emitted into runtime code. The definition
     ;; loads even on a requires-python 3.10 environment, and the class's __type_params__ is empty). T in the runtime answer and field types reads the module's TypeVar (#2939).
     (defclass [~(if written
                     `(_doeff-dataclass :init False :repr False :eq False)
                     `(_doeff-dataclass :frozen True))]
       ~@(if (and type-params (static-view-enabled)) [':tp (hy.models.List type-params)] [])
       ~name [_doeff-effect-base]
       ~@(if (is docstring None) [] [docstring])
       (setv #^ (get _doeff-ClassVar object) __doeff-answer__ ~(answer-value-form answer))
       ~@fields
       ~@post-init
       ~@methods
       ~@(bound-answer-methods answer outcome-types))
     ~@(if written
           [`(doeff_hy._record_runtime.seal-effect ~name #(~@(lfor s syms (String (hy.mangle (str s))))) ~(bool post-init))]
           [])
     (setattr ~name "__doeff_tags__" ~(tags-form tags where))
     (setattr ~name "__doeff_defeffect__" True)
     ~@(if runs-carried
           [`(setattr ~name "__doeff_runs_carried__" (frozenset [~@(lfor n runs-carried (String n))]))]
           [])
     ~@outcomes))


(defn #^ (get list str) needs-names [#^ Object form #^ str where]  ; cannot be a defk: a function called at macro expansion time (also read by defsystem in doeff-cluster)
  "Check the `:needs` value form at transform time and return a sorted list of capability names. The only accepted shape is a set of string literals #{\"a\" \"b\"}
   (names, expressions and empty strings are refused — so the linter can read it without executing)."
  (when (not (isinstance form Set))
    (raise (SyntaxError (.format "{}: :needs must be a set of strings naming capabilities #{{\"pg-network\" ...}}: {}" where (hy.repr form)))))
  (for [item form]
    (when (not (and (isinstance item String) (str item)))
      (raise (SyntaxError (.format "{}: elements of :needs must be non-empty string literals: {}" where (hy.repr item))))))
  (sorted (sfor item form (str item))))


(defn #^ (get list str) record-names [#^ Object form #^ str where #^ str key]  ; cannot be a defk: a function called at macro expansion time (also read by defsystem in doeff-cluster)
  "Check the value form of `:reads` / `:writes` / `:serves` at expansion time and return a sorted list of outside store names — so the linter can read it by spelling without executing,
   the only accepted shape is a set of string literals (an empty set = declared to neither read nor write), spelled <store>:<name> (exactly one `:`, both sides non-empty).
   What a store name means is not checked (it is the vocabulary of the consumer's repo). The readers are the contract :writes of a definition (_extract-contracts in macros.hy) and
   the job rows of defsystem in doeff-cluster (doeff_cluster/_shared/entry/_system_form.hy)."
  (when (not (isinstance form Set))
    (raise (SyntaxError (.format "{}: {} must be a set of strings naming stores #{{\"<store>:<name>\" ...}}: {}" where key (hy.repr form)))))
  (for [item form]
    (when (not (isinstance item String))
      (raise (SyntaxError (.format "{}: elements of {} must be string literals: {}" where key (hy.repr item)))))
    (setv parts (.split (str item) ":"))
    (when (not (and (= (len parts) 2) (all parts)))
      (raise (SyntaxError (.format "{}: the spelling of {} is <store>:<name> (exactly one `:`, both sides non-empty): {}" where key (hy.repr item))))))
  (sorted (sfor item form (str item))))


(defn #^ Object needs-form [#^ (| Object None) form #^ str where]  ; cannot be a defk: a function called at macro expansion time
  "The `:needs` value form -> a form that builds a frozenset at runtime (a None form if absent)."
  (if (is form None)
      'None
      `(frozenset [~@(lfor n (needs-names form where) (String n))])))


(defn #^ (get list Object) declaration-setters [#^ Symbol name #^ (| Dict None) contract #^ str where]  ; cannot be a defk: a function called at macro expansion time
  "A list of forms that place the attributes __doeff_effects__ / __doeff_tags__ / __doeff_needs__ of definition name (contract = the header dict or None)."
  (setv effects (if (is contract None) None (declared-value contract ":effects"))
        tags (if (is contract None) None (declared-value contract ":tags"))
        needs (if (is contract None) None (declared-value contract ":needs")))
  [`(import doeff_hy._declarations)
   `(setattr ~name "__doeff_effects__" ~(effects-form effects where))
   `(setattr ~name "__doeff_tags__" ~(tags-form tags where))
   `(setattr ~name "__doeff_needs__" ~(needs-form needs where))])
