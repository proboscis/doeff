//! The closed list of layer rules (rules judged across the repo). The ID, title, statement, and default one-line fix are written only here.

use serde::Serialize;

/// The rule family (a closed classification that groups rules in the editor's list).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RuleFamily {
    /// Layer direction and what may be placed (DOEFF101 to 103), and effects emitted by translation-layer handlers (DOEFF130).
    Layer,
    /// Tag declarations (:context, role) and mismatches (DOEFF104, 105, 113).
    Tags,
    /// Raw side effects (DOEFF106, 107).
    Raw,
    /// Naming (DOEFF108).
    Naming,
    /// Placement and dependencies (DOEFF109, 114 to 117), and keys the settings do not know (DOEFF100 — it is the same settings matter as the placement declaration architecture.hy, so it goes
    /// into this family. The extension's list of families is closed, so adding a new family makes old extensions discard the whole output).
    Place,
    /// How definitions are written (DOEFF110 to 112, 118).
    Definition,
    /// class (DOEFF119, 204).
    Class,
    /// JsonValue and wire (DOEFF120).
    Wire,
    /// Smells (DOEFF121 to 125, 205).
    Smell,
    /// Jev (semantic judgment, DOEFF201 to 203).
    Jev,
    /// Python per-statement rules (DOEFF001 to 031, NOQA001, unknown IDs).
    Python,
    /// A law with no rules.
    Law,
}

/// The kinds of layer rule. Unlike the Python per-statement rules (DOEFF001 to 031), they are judged by looking at the repo's list of modules and the settings.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ProjectRule {
    /// DOEFF100: the settings (pyproject's [tool.doeff-linter], architecture.hy) contain a key or rule ID this linter does not know
    /// (a notice that the remaining rules were run without reading that key — warning; the linter is older than the settings, or a typo).
    UnknownConfigKey,
    /// DOEFF128: the reader cannot read a Hy file to the end (violations are missing — emitted regardless of the list of enabled rules).
    UnreadableFile,
    /// DOEFF101: layer import direction — do not import modules outside the allowed layers.
    LayerImportDirection,
    /// DOEFF102: do not directly import modules forbidden per layer (such as I/O modules).
    LayerForbiddenModule,
    /// DOEFF103: do not define functions and handlers in a types-only layer.
    LayerTypesOnly,
    /// DOEFF104: a layer's module declares its context and role with tags.
    ModuleDeclaresTags,
    /// DOEFF105: the tag's role is one allowed in that layer.
    RoleMatchesLayer,
    /// DOEFF106: definitions that touch raw side effects directly are placed only in allowed layers.
    RawSideEffectDirect,
    /// DOEFF107: reaching a raw side effect through a called definition (a notice of fact — info).
    RawSideEffectVia,
    /// DOEFF108: do not put environment words in the names of business files, handlers, and assembly functions.
    EnvironmentName,
    /// DOEFF109: service boundary — one service's judgment and translation layers do not read another service's judgment and translation layers.
    ServiceBoundary,
    /// DOEFF110: do not use Hy's defn / defn/a (use defk, which can carry a contract dictionary and :tags).
    DefnForbidden,
    /// DOEFF111: attach a comment giving the reason a deff cannot be a defk.
    DeffNeedsReason,
    /// DOEFF112: defk, deff, defp, defhandler, and defeffect declare the required keys in :tags.
    DefinitionTagsRequired,
    /// DOEFF113: the tag's :context and the dir's service disagree (info; warning if the service is declared).
    ContextMatchesService,
    /// DOEFF114: a module in an undeclared location (none of architecture.hy's service layers, shared, foundation, or legacy).
    UndeclaredPlace,
    /// DOEFF115: a service dir not in the declaration, or a layer dir inside a service not in the declaration.
    UndeclaredDirectory,
    /// DOEFF116: service dependency — reading a service not in the declaration's :depends-on, or reading a layer of the dependency that is not readable (outside the layer's :dependency-layers or :open-layers).
    ServiceDependency,
    /// DOEFF117: a declared dependency that is not used (info).
    UnusedDependency,
    /// DOEFF118: test functions in test locations are written only with deftest (do not place bindings of defn, deff, defk, or fn whose name is test_).
    TestIsDeftest,
    /// DOEFF119: do not write a defclass with methods that hold logic in business code (for a class with only fields, info recommending defrecord).
    ClassWithBehaviour,
    /// DOEFF120: do not use JsonValue (a type that merely wraps a plain dict with a name) outside generic solvers and the foundation send/receive modules listed in :wire-modules.
    JsonValueOutsideWire,
    /// DOEFF121: a judgment-layer definition checks with isinstance a field read with a string key (a JSON shape check has crept into core).
    ShapeCheckInJudgment,
    /// DOEFF122: a match arm receives a failure type and merely returns the received value again (a hand-written re-raise of an exception).
    FailureRethrow,
    /// DOEFF123: `(return x)` comes right after `(<- x T (f …))`, and x is used nowhere else.
    BindThenReturn,
    /// DOEFF124: join two or more fields of the same value into one string.
    FieldsJoinedIntoText,
    /// DOEFF125: rebuild an accumulator on each pass inside for / while.
    RebuiltAccumulator,
    /// DOEFF126: call a defk definition bare (a Program is returned instead of the answer, and flows on silently as a wrong value).
    DefkCalledBare,
    /// DOEFF127: a defk's `:effects` declaration disagrees with inference (raises an effect not in the declaration, or does not raise a declared effect).
    EffectsDisagreeWithInference,
    /// DOEFF175: a branch (match arm, isinstance) that separates as a value, on a variable that binds an effect's answer, the types the effect declared in defeffect as absent or failure —
    /// `<-` and `!` do not bind those types but let them escape as Absent / Raise, so the branch never runs (ADR-DOE-CORE-EFFECTS-003 R14 (4) — an old branch left after
    /// the version that switched the effect. That version is not landed until the hits reach 0). Not lowered by the registry.
    StaleOutcomeBranch,
    /// DOEFF130: a translation-layer handler emits a business intent (an effect of a type in the intent layer) — followed through imported defks.
    TranslationEmitsIntent,
    /// DOEFF174: a definition in a business layer (the setting translation_effects.domain_layers — default core and intent) emits an effect of architecture.hy's :business-fakes
    /// :lower-layer-modules (including shapes reached by following defk calls), or wraps handlers of that module's package with with_handlers —
    /// raw operations on a record service are the job of handlers (protocol, foundation). Not lowered by the known list.
    LowerLayerInDomain,
    /// DOEFF168: a Hy definition in a business layer (architecture.hy's :environment-branches :layers) branches by comparing with a value of an environment name or by a dry-run flag
    /// (business layers do not know the environment — differences between environments are expressed only by swapping the set of handlers).
    EnvironmentBranch,
    /// DOEFF169: a keyword field name in a `match` class pattern contains `-` (Hy does not mangle it into an attribute name, so it never matches).
    MatchFieldHyphen,
    /// DOEFF170: a defwire declaration exists in a layer that passes values by type only (architecture.hy's layer `:wire-free True` — agora's core) (wire types — declarations that hold the
    /// correspondence of field names, and parse / dump — go in the translation layer).
    WireInWireFreeLayer,
    /// DOEFF171: the :pre / :post of a definition in a layer that passes values by type only, and the field types of defclass / defrecord, contain a mapping (dict, Mapping, JsonBody, FrozenMap …,
    /// including typed mappings) or a tuple / list without a content type.
    BareMapInWireFreeLayer,
    /// DOEFF172 (smell, major): where mappings live — a definition body outside a handler builds a mapping (`{…}`, dfor, (dict …)), or the fields of a defclass, defrecord, or defwire outside the layers that pass values by type only
    /// are mappings (counting only).
    DictOutsideItsPlace,
    /// DOEFF131: a definition outside architecture.hy's allowlist (:world-handlers) names a doeff real I/O handler listed in the allowlist's :wraps
    /// (only the allowlist's definitions may touch the outside world).
    WorldHandlerNamedOutsideList,
    /// DOEFF132: an allowlist definition does not exist or is outside the foundation layer (definitions that may touch the outside world are placed only in the foundation layer).
    /// The position is the allowlist element in architecture.hy.
    WorldHandlerMisplaced,
    /// DOEFF133: the kind of test (local / edge) derived from what it reaches disagrees with architecture.hy's :edge-mark marker — it reaches an allowlist definition, a :wraps
    /// handler, or raw I/O but has no marker / it reaches none but has a marker.
    /// Raw I/O inside Python functions in the repo that a Hy definition names, and their callees, also count as reached (python_reach).
    TestKindMismatch,
    /// DOEFF137: a handler in architecture.hy's allowlist (:world-handlers) has not a single edge test (a deftest that has a non-empty `:interpreters` and reaches that handler's
    /// definition) — a real I/O handler for which it is not checked that the real one and the fake pass the same test.
    /// A handler with a reasoned `:contract-test (none …)` is not judged; a reasonless `:contract-test none` fires (how to write it is in the fix text — #1796).
    WorldHandlerWithoutContractTest,
    /// DOEFF135: test forms other than deftest (Python def test_*, per-module skip, check scripts outside pytest, deftest runners) —
    /// files are selected by the spelling patterns of architecture.hy's :test-forms.
    TestFormNotDeftest,
    /// DOEFF146: vocabulary that confines judgment to one place exists outside :except — the vocabulary (regular expressions) named by the groups of architecture.hy's :single-point-vocabulary
    /// is read by :files outside the :except files (the single point of judgment) (#1371).
    VocabularyOutsideSinglePoint,
    /// DOEFF148: a spelling restricted to specific files exists outside :except — the spellings (regular expressions; strings are counted too) named by the groups of architecture.hy's :confined-spellings
    /// are written by :files outside :except (#1436).
    SpellingOutsideItsFiles,
    /// DOEFF161: the number of hits of a counted spelling is not the decided number — the count of the :pattern of architecture.hy's :counted-spellings declaration, counted within :files (or the :within
    /// definitions), does not fit :count / :at-least (#1437).
    SpellingCountDiffers,
    /// DOEFF162: the whole set of effect declarations disagrees with the list — in the :files of architecture.hy's :effect-census, a class declaration inheriting :base is
    /// not in the :effects list, appears twice, or an effect in the list has no declaration (#1438).
    EffectOutsideCensus,
    /// DOEFF149: the lineup of classes that hold type fields disagrees with the list — among the Python classes in the :files of architecture.hy's :field-holders,
    /// a class whose field annotation has :type is not in :holders, or a class in :holders does not have that field.
    FieldHoldersDiffer,
    /// DOEFF136: not a single deftest under the fake environment (:verification-environment) reaches the definitions in a service's entry layer
    /// — a service whose production assembly is not run locally (#1111).
    ServiceUntestedOnSim,
    /// DOEFF163: a service with code (one or more definitions in the entry layer) does not declare :invariants in defservice, the named function does not exist, or
    /// that function's :role is not judgment (the condition for counting business logic as "tested").
    ServiceInvariantsMissing,
    /// DOEFF173: a service with code does not declare :system (the defsystem placed in the cluster) in defservice, the named module is not in
    /// that service's entry layer, the name is not a defsystem, or the defsystem's argument is not exactly one typed base (the
    /// counterpart of DOEFF136. If a defsystem exists, it is automatically included in the generic fake-cluster tests, so the rule asks only for the declaration and the shape).
    ServiceSystemMissing,
    /// DOEFF207: a production-system job has no read/write fields :reads / :writes, or a pair read by a job has neither a production job's writer nor a
    /// row in :outside-writers (#3496). Not lowered by the registry. The declaration is :outside-writers.
    SystemAccessUnwritten,
    /// DOEFF208: production code uses waiting for record changes as a means of waking a job — calls to the factories of doeff-records' signal sources (read-signal-handler,
    /// records-signal-handler), and WatchChanges / WatchEvents with nonzero seconds outside the record effect handlers.
    /// Not lowered by the registry. The population is production code of :business-fakes.
    RecordChangeWakesJob,
    /// DOEFF209: a Program in production code repeatedly fetches the same thing with time waits in between — places that put Delay, WaitTicks, and ArmTimer / ArmedTimer with
    /// now + a fixed number of seconds (including shapes wrapped in helper definitions) inside a repetition, and :poll-seconds passed to a library.
    /// Not lowered by the registry. The population is production code of :business-fakes.
    PollingOnTimer,
    /// DOEFF210: a Hy program (the body of defk or defp) waits on the wall clock with time.sleep / asyncio.sleep — time is waited for with doeff-time effects
    /// (Delay and so on), and only the handler's implementation waits on the wall clock (doeff's ADR-DOE-TIME-001 R1, R2).
    /// The population is the setting `[tool.doeff-linter.program_sleep]` (where programs live — it applies only to repos that write it).
    ProgramSleepsOnWallClock,
    /// DOEFF211: a Hy test waits on the wall clock with time.sleep / asyncio.sleep (and the equivalent `(.wait (threading.Event) seconds)`) without declaring that real time is the subject —
    /// tests run on a virtual clock or wait on events, and only tests whose subject is real time itself declare realtime (doeff's
    /// ADR-DOE-TIME-001 R3). The population is the setting `[tool.doeff-linter.test_sleep]` (where tests live — it applies only to repos that write it).
    TestSleepsOnWallClock,
    /// DOEFF140: a layer module of architecture.hy's :placed-dependencies (service and shared) imports a module under root that is outside the layer locations
    /// — a dependency on a module whose placement is undecided.
    PlacedDependency,
    /// DOEFF141: definitions reachable from a definition in architecture.hy's :blind-definitions read a declared word, or the definition's module has imports
    /// — other material has crept into a definition meant to be judged only on decided material.
    BlindDefinitionReads,
    /// DOEFF147: inside a definition in architecture.hy's :allowed-heads, there is a head form outside the list of allowed heads — a call that can raise has crept into a definition
    /// meant to call only things that do not raise exceptions (#1413).
    DefinitionCallsUnlistedHead,
    /// DOEFF159: a head whose allowed places and counts are fixed by architecture.hy's :call-sites is called outside those places, or the count, outer form, or branch differs from the declaration
    /// — a confined single point silently grows a second one (#1414).
    CallOutsideDeclaredSites,
    /// DOEFF160: a broad exception catch (`(except [] …)`, Exception, BaseException, AssertionError, Python's `except:`) is outside architecture.hy's
    /// :broad-catches :carriers (a boundary that binds the caught exception by name and carries it on a decided event) (#1415).
    BroadCatchOutsideCarrier,
    /// DOEFF150: a spelling (word, regular expression, definition name) decided as not to be used by architecture.hy's :retired-words is in a declared file
    /// (the table of words exists only in the repo's declaration).
    RetiredWord,
    /// DOEFF151: a declared Hy file calls a call decided as not to be used by architecture.hy's :retired-calls (retired effects, clocks …) (#1193).
    RetiredCall,
    /// DOEFF142: a defhandler takes as arguments a client, a mutable container, a mutable object, or a store-name argument (connection targets and settings are Ask, state is held in session —
    /// #1366). The population and the store names are read from architecture.hy's :handler-arguments.
    HandlerArgumentHoldsState,
    /// DOEFF144: a public-surface type annotation (class field, function return, defk / deff :post) is a plain mapping, plain tuple, open-valued mapping, or fixed-length tuple,
    /// or a function answers with a tuple literal of length 2 or more — a structured value is expressed with a type that has field names and types.
    UntypedStructuredValue,
    /// DOEFF145: a kw-only record (a defrecord, or a defclass with a dataclass carrying :kw-only True as decoration) in a .hy of the same name in the same dir
    /// is declared by a type declaration (.pyi) as a @dataclass without kw_only=True — the type checker accepts calls with positional arguments, but at runtime it is a TypeError (#1191).
    RecordStubNotKwOnly,
    /// DOEFF143: a definition reachable from a fake root but not from the production entry answers a business effect without tap (a fake handler) / rot in the outside-world table (stale rows of the counterexample table are DOEFF176).
    /// The declaration is architecture.hy's :business-fakes.
    BusinessEffectFake,
    /// DOEFF176: a row of the counterexample table (:business-fakes :counterexamples) matches no clause of any deliberately broken handler — a stale row.
    /// Judged in the same single place as DOEFF143 (split from DOEFF143 so the number of fakes and the number of stale rows are counted under different numbers). The declaration is :business-fakes.
    StaleCounterexampleRow,
    /// DOEFF155: breaches of the shape of assembly — a set file remains / the shape of the assembly's single-point function (one base list + translation lists imported from the translation layer) /
    /// assembly written with something other than defk / list order / a translation list lists another service's translations / the single point receives the list as an argument /
    /// the verification environment and tests list translation lists outside the single point (#3408). The declaration is :assembly-shape.
    AssemblyShapeBroken,
    /// DOEFF156: a translation handler re-emits a business effect outside its list / a base handler answers a business effect.
    AssemblyAnswerMisplaced,
    /// DOEFF157: a definition reachable only from tests answers a business effect or an effect of a lower layer — a fake that exists only for tests. The declaration is :business-fakes.
    TestOnlyFake,
    /// DOEFF158: the answerer of an intent effect reachable from the production entry is not exactly one translation-layer handler. Layer names are in :assembly-shape.
    IntentAnswererNotTranslation,
    /// DOEFF164: a service with an entry layer has not a single counterexample of a broken handler (a deftest that reaches a clause of the counterexample table and also reaches that service's entry). The declaration is :business-fakes' :counterexamples and :verification-environment.
    ServiceWithoutCounterexample,
    /// DOEFF167: a clause of a service (defservice's :clauses — the clause names returned by the :invariants function) has no counterexample of a broken handler that
    /// declares in the counterexample table (a `breaks:` row) that it breaks that clause, and no reason for exclusion (:clause-exemptions) — a gap in per-clause coverage (ADR R2, "per clause").
    /// If a service with code does not declare :clauses, one is reported under the service's name. The declaration is defservice and :business-fakes' :counterexamples.
    ClauseWithoutCounterexample,
    /// DOEFF165: an effect of the intent layer lacks one of: a definition fired from a local test, a fake answerer, or a production answerer — a gap in the coverage table
    /// (report only at first = severity info. Making it a failure is #1562 K4). Layer names are in :assembly-shape.
    IntentEffectUncovered,
    /// DOEFF206: a handler of the verification environment (:verification-environment) answers an intent-layer effect itself (even on only one branch) /
    /// a row of a table that lets violations through (the outside-world table, the table that passes lower layers, the table of test-only fakes) passes an intent-layer effect — a hole where the production translation handler
    /// never runs locally and yet everything goes green (#3407, #3406). Not lowered by the registry. The declarations are :business-fakes, :verification-environment, and :assembly-shape.
    IntentFakedInVerification,
    /// DOEFF166: a registry key matches no finding of any rule judged in the whole run — a stale row that no longer matches (#1706).
    /// The rule the key names is applied in the same run even if it is not in enable, and its hits are used only for the judgment of 166.
    RegistryEntryStale,
    /// DOEFF201 (semantic, Jev): a translation-layer definition makes a business judgment.
    SemanticBusinessDecision,
    /// DOEFF202 (semantic, Jev): a judgment-layer definition knows the means of communication.
    SemanticTransportKnowledge,
    /// DOEFF203 (semantic, Jev): the kind of reason for a plain function that a deff declared is likely not to match the code.
    SemanticPlainCallable,
    /// DOEFF204 (semantic, Jev): a class with methods that hold logic is likely to be a window to the outside world or something that holds state.
    SemanticClassRole,
    /// DOEFF205 (semantic, Jev): a judgment definition is likely to mix input-shape checks with business judgment.
    SemanticMixedConcerns,
}

impl ProjectRule {
    /// All layer rules (for the output list and the expansion of `ALL`).
    pub const ALL: &'static [ProjectRule] = &[
        ProjectRule::UnknownConfigKey,
        ProjectRule::UnreadableFile,
        ProjectRule::LayerImportDirection,
        ProjectRule::LayerForbiddenModule,
        ProjectRule::LayerTypesOnly,
        ProjectRule::ModuleDeclaresTags,
        ProjectRule::RoleMatchesLayer,
        ProjectRule::RawSideEffectDirect,
        ProjectRule::RawSideEffectVia,
        ProjectRule::EnvironmentName,
        ProjectRule::ServiceBoundary,
        ProjectRule::DefnForbidden,
        ProjectRule::DeffNeedsReason,
        ProjectRule::DefinitionTagsRequired,
        ProjectRule::ContextMatchesService,
        ProjectRule::UndeclaredPlace,
        ProjectRule::UndeclaredDirectory,
        ProjectRule::ServiceDependency,
        ProjectRule::UnusedDependency,
        ProjectRule::TestIsDeftest,
        ProjectRule::ClassWithBehaviour,
        ProjectRule::JsonValueOutsideWire,
        ProjectRule::ShapeCheckInJudgment,
        ProjectRule::FailureRethrow,
        ProjectRule::BindThenReturn,
        ProjectRule::FieldsJoinedIntoText,
        ProjectRule::RebuiltAccumulator,
        ProjectRule::DefkCalledBare,
        ProjectRule::EffectsDisagreeWithInference,
        ProjectRule::StaleOutcomeBranch,
        ProjectRule::TranslationEmitsIntent,
        ProjectRule::LowerLayerInDomain,
        ProjectRule::EnvironmentBranch,
        ProjectRule::MatchFieldHyphen,
        ProjectRule::WireInWireFreeLayer,
        ProjectRule::BareMapInWireFreeLayer,
        ProjectRule::DictOutsideItsPlace,
        ProjectRule::WorldHandlerNamedOutsideList,
        ProjectRule::WorldHandlerMisplaced,
        ProjectRule::TestKindMismatch,
        ProjectRule::WorldHandlerWithoutContractTest,
        ProjectRule::TestFormNotDeftest,
        ProjectRule::VocabularyOutsideSinglePoint,
        ProjectRule::SpellingOutsideItsFiles,
        ProjectRule::SpellingCountDiffers,
        ProjectRule::EffectOutsideCensus,
        ProjectRule::FieldHoldersDiffer,
        ProjectRule::ServiceUntestedOnSim,
        ProjectRule::ServiceInvariantsMissing,
        ProjectRule::ServiceSystemMissing,
        ProjectRule::SystemAccessUnwritten,
        ProjectRule::RecordChangeWakesJob,
        ProjectRule::PollingOnTimer,
        ProjectRule::ProgramSleepsOnWallClock,
        ProjectRule::TestSleepsOnWallClock,
        ProjectRule::PlacedDependency,
        ProjectRule::BlindDefinitionReads,
        ProjectRule::DefinitionCallsUnlistedHead,
        ProjectRule::CallOutsideDeclaredSites,
        ProjectRule::BroadCatchOutsideCarrier,
        ProjectRule::RetiredWord,
        ProjectRule::RetiredCall,
        ProjectRule::HandlerArgumentHoldsState,
        ProjectRule::UntypedStructuredValue,
        ProjectRule::RecordStubNotKwOnly,
        ProjectRule::BusinessEffectFake,
        ProjectRule::StaleCounterexampleRow,
        ProjectRule::AssemblyShapeBroken,
        ProjectRule::AssemblyAnswerMisplaced,
        ProjectRule::TestOnlyFake,
        ProjectRule::IntentAnswererNotTranslation,
        ProjectRule::ServiceWithoutCounterexample,
        ProjectRule::ClauseWithoutCounterexample,
        ProjectRule::IntentEffectUncovered,
        ProjectRule::IntentFakedInVerification,
        ProjectRule::RegistryEntryStale,
        ProjectRule::SemanticBusinessDecision,
        ProjectRule::SemanticTransportKnowledge,
        ProjectRule::SemanticPlainCallable,
        ProjectRule::SemanticClassRole,
        ProjectRule::SemanticMixedConcerns,
    ];

    /// The rule's ID.
    pub fn id(self) -> &'static str {
        match self {
            ProjectRule::UnknownConfigKey => "DOEFF100",
            ProjectRule::UnreadableFile => "DOEFF128",
            ProjectRule::LayerImportDirection => "DOEFF101",
            ProjectRule::LayerForbiddenModule => "DOEFF102",
            ProjectRule::LayerTypesOnly => "DOEFF103",
            ProjectRule::ModuleDeclaresTags => "DOEFF104",
            ProjectRule::RoleMatchesLayer => "DOEFF105",
            ProjectRule::RawSideEffectDirect => "DOEFF106",
            ProjectRule::RawSideEffectVia => "DOEFF107",
            ProjectRule::EnvironmentName => "DOEFF108",
            ProjectRule::ServiceBoundary => "DOEFF109",
            ProjectRule::DefnForbidden => "DOEFF110",
            ProjectRule::DeffNeedsReason => "DOEFF111",
            ProjectRule::DefinitionTagsRequired => "DOEFF112",
            ProjectRule::ContextMatchesService => "DOEFF113",
            ProjectRule::UndeclaredPlace => "DOEFF114",
            ProjectRule::UndeclaredDirectory => "DOEFF115",
            ProjectRule::ServiceDependency => "DOEFF116",
            ProjectRule::UnusedDependency => "DOEFF117",
            ProjectRule::TestIsDeftest => "DOEFF118",
            ProjectRule::ClassWithBehaviour => "DOEFF119",
            ProjectRule::JsonValueOutsideWire => "DOEFF120",
            ProjectRule::ShapeCheckInJudgment => "DOEFF121",
            ProjectRule::FailureRethrow => "DOEFF122",
            ProjectRule::BindThenReturn => "DOEFF123",
            ProjectRule::FieldsJoinedIntoText => "DOEFF124",
            ProjectRule::RebuiltAccumulator => "DOEFF125",
            ProjectRule::DefkCalledBare => "DOEFF126",
            ProjectRule::EffectsDisagreeWithInference => "DOEFF127",
            ProjectRule::StaleOutcomeBranch => "DOEFF175",
            ProjectRule::TranslationEmitsIntent => "DOEFF130",
            ProjectRule::LowerLayerInDomain => "DOEFF174",
            ProjectRule::EnvironmentBranch => "DOEFF168",
            ProjectRule::MatchFieldHyphen => "DOEFF169",
            ProjectRule::WireInWireFreeLayer => "DOEFF170",
            ProjectRule::BareMapInWireFreeLayer => "DOEFF171",
            ProjectRule::DictOutsideItsPlace => "DOEFF172",
            ProjectRule::WorldHandlerNamedOutsideList => "DOEFF131",
            ProjectRule::WorldHandlerMisplaced => "DOEFF132",
            ProjectRule::TestKindMismatch => "DOEFF133",
            ProjectRule::WorldHandlerWithoutContractTest => "DOEFF137",
            ProjectRule::TestFormNotDeftest => "DOEFF135",
            ProjectRule::VocabularyOutsideSinglePoint => "DOEFF146",
            ProjectRule::SpellingOutsideItsFiles => "DOEFF148",
            ProjectRule::SpellingCountDiffers => "DOEFF161",
            ProjectRule::EffectOutsideCensus => "DOEFF162",
            ProjectRule::FieldHoldersDiffer => "DOEFF149",
            ProjectRule::ServiceUntestedOnSim => "DOEFF136",
            ProjectRule::ServiceInvariantsMissing => "DOEFF163",
            ProjectRule::ServiceSystemMissing => "DOEFF173",
            ProjectRule::SystemAccessUnwritten => "DOEFF207",
            ProjectRule::RecordChangeWakesJob => "DOEFF208",
            ProjectRule::PollingOnTimer => "DOEFF209",
            ProjectRule::ProgramSleepsOnWallClock => "DOEFF210",
            ProjectRule::TestSleepsOnWallClock => "DOEFF211",
            ProjectRule::PlacedDependency => "DOEFF140",
            ProjectRule::BlindDefinitionReads => "DOEFF141",
            ProjectRule::DefinitionCallsUnlistedHead => "DOEFF147",
            ProjectRule::CallOutsideDeclaredSites => "DOEFF159",
            ProjectRule::BroadCatchOutsideCarrier => "DOEFF160",
            ProjectRule::RetiredWord => "DOEFF150",
            ProjectRule::RetiredCall => "DOEFF151",
            ProjectRule::HandlerArgumentHoldsState => "DOEFF142",
            ProjectRule::UntypedStructuredValue => "DOEFF144",
            ProjectRule::RecordStubNotKwOnly => "DOEFF145",
            ProjectRule::BusinessEffectFake => "DOEFF143",
            ProjectRule::StaleCounterexampleRow => "DOEFF176",
            ProjectRule::AssemblyShapeBroken => "DOEFF155",
            ProjectRule::AssemblyAnswerMisplaced => "DOEFF156",
            ProjectRule::TestOnlyFake => "DOEFF157",
            ProjectRule::IntentAnswererNotTranslation => "DOEFF158",
            ProjectRule::ServiceWithoutCounterexample => "DOEFF164",
            ProjectRule::ClauseWithoutCounterexample => "DOEFF167",
            ProjectRule::IntentEffectUncovered => "DOEFF165",
            ProjectRule::IntentFakedInVerification => "DOEFF206",
            ProjectRule::RegistryEntryStale => "DOEFF166",
            ProjectRule::SemanticBusinessDecision => "DOEFF201",
            ProjectRule::SemanticTransportKnowledge => "DOEFF202",
            ProjectRule::SemanticPlainCallable => "DOEFF203",
            ProjectRule::SemanticClassRole => "DOEFF204",
            ProjectRule::SemanticMixedConcerns => "DOEFF205",
        }
    }

    /// The rule's default gravity (when the repo's declaration `rules.<ID>.level` is absent). A rule with None is decided from the rule's own severity
    /// (error = major, warning = minor, info = info). Violations of the responsibility boundary are always critical (because the premise is to
    /// keep doeff code in a shape that tests can verify) — copying it per repo would let one side go silently stale, so the default lives in this one place.
    /// Place a new rule in one or the other (an exhaustive match — it cannot be added without deciding).
    pub fn default_level(self) -> Option<RuleLevel> {
        match self {
            // Responsibility boundary: layer direction, modules forbidden per layer, types-only layers, raw side effects, service boundary and dependencies, translation emitting business intents,
            // Jev judgments (business judgment in translation layers, communication means in judgment layers, mixing of shape checks and judgment).
            ProjectRule::LayerImportDirection
            | ProjectRule::LayerForbiddenModule
            | ProjectRule::LayerTypesOnly
            | ProjectRule::RawSideEffectDirect
            | ProjectRule::WorldHandlerNamedOutsideList
            | ProjectRule::WorldHandlerMisplaced
            | ProjectRule::TestKindMismatch
            // A real I/O handler with no edge test (#1363 — known hits listed in the registry are warning, new hits are critical).
            | ProjectRule::WorldHandlerWithoutContractTest
            | ProjectRule::TestFormNotDeftest
            | ProjectRule::VocabularyOutsideSinglePoint
            // A spelling restricted to specific files exists outside them (#1436 — a grandchild of #1373. New hits are critical).
            | ProjectRule::SpellingOutsideItsFiles
            | ProjectRule::SpellingCountDiffers
            | ProjectRule::EffectOutsideCensus
            | ProjectRule::FieldHoldersDiffer
            | ProjectRule::ServiceUntestedOnSim
            | ProjectRule::ServiceInvariantsMissing
            | ProjectRule::ServiceSystemMissing
            | ProjectRule::SystemAccessUnwritten
            | ProjectRule::RecordChangeWakesJob
            | ProjectRule::PollingOnTimer
            // A program bypasses the handler and waits on the wall clock (time is the handler's responsibility — doeff's ADR-DOE-TIME-001).
            | ProjectRule::ProgramSleepsOnWallClock
            // A test waits on the wall clock without declaring that real time is the subject (the same rule for Python tests is semgrep's ERROR — R3 of the same ADR).
            | ProjectRule::TestSleepsOnWallClock
            // A dependency on a module outside the locations (#1188 — known hits listed in the registry are warning, new hits are critical).
            | ProjectRule::PlacedDependency
            // Other material has crept into a definition meant to be judged only on decided material (#1368 — a child of #1188. New hits are critical).
            | ProjectRule::BlindDefinitionReads
            // A call outside the list has crept into a definition whose allowed heads are decided (#1413 — a grandchild of #1372. New hits are critical).
            | ProjectRule::DefinitionCallsUnlistedHead
            // A head with decided allowed places and counts is called outside the places (#1414 — a grandchild of #1372. New hits are critical).
            | ProjectRule::CallOutsideDeclaredSites
            | ProjectRule::BroadCatchOutsideCarrier
            // Spellings and calls decided as not to be used (the decision of #1193 — known hits listed in the registry are warning, new hits are critical).
            | ProjectRule::RetiredWord
            | ProjectRule::RetiredCall
            | ProjectRule::HandlerArgumentHoldsState
            // Plain mappings and plain tuples in public-surface types, and the kw_only mismatch with .pyi (the decision of #1191 — default is critical).
            | ProjectRule::UntypedStructuredValue
            | ProjectRule::RecordStubNotKwOnly
            | ProjectRule::BusinessEffectFake
            | ProjectRule::StaleCounterexampleRow
            | ProjectRule::AssemblyShapeBroken
            | ProjectRule::AssemblyAnswerMisplaced
            | ProjectRule::TestOnlyFake
            | ProjectRule::IntentAnswererNotTranslation
            | ProjectRule::ServiceWithoutCounterexample
            // Gaps in per-clause counterexamples (#1713 — known gaps listed in the registry are warning, new gaps are critical).
            | ProjectRule::ClauseWithoutCounterexample
            // Gaps in coverage of intent effects (the table of #1561 K3 made a failure by #1562 K4 — known gaps listed in the registry are warning, new gaps are critical).
            | ProjectRule::IntentEffectUncovered
            | ProjectRule::IntentFakedInVerification
            // Stale registry rows that match nothing (do not leave a registry that is meant to shrink loosened by forgetting to delete rows — #1706).
            | ProjectRule::RegistryEntryStale
            | ProjectRule::ServiceBoundary
            | ProjectRule::ServiceDependency
            | ProjectRule::TranslationEmitsIntent
            | ProjectRule::LowerLayerInDomain
            // A business layer branches on an environment name or a dry-run flag (#1906 — a violation of the responsibility boundary).
            | ProjectRule::EnvironmentBranch
            // A match clause that never matches (#2036 — a mistake that silently falls to the default branch).
            | ProjectRule::MatchFieldHyphen
            // Old branches of an effect whose declaration was switched (branches that never run — ADR-DOE-CORE-EFFECTS-003 R14 (4), the gate for every switch).
            | ProjectRule::StaleOutcomeBranch
            // defwire and plain mappings in layers that pass values by type only (#2143).
            | ProjectRule::WireInWireFreeLayer
            | ProjectRule::BareMapInWireFreeLayer
            // A location not in the declaration (no layer rule applies), calling a defk bare (a real mistake in which a Program flows as a value),
            // and an unreadable file (judgments are missing, so even 0 findings is not a pass).
            | ProjectRule::UndeclaredPlace
            | ProjectRule::UndeclaredDirectory
            | ProjectRule::DefkCalledBare
            | ProjectRule::UnreadableFile => Some(RuleLevel::Critical),
            // Jev judgments (business judgment in translation layers, communication means in judgment layers, mixing of shape checks and judgment) are not made critical until
            // calibration is done and they can be trusted (reverting what #942 made critical). The severity of the hit is
            // warning, so without a declaration it would become minor — place it at major so it is not overlooked as a reading of the responsibility boundary.
            ProjectRule::SemanticBusinessDecision
            | ProjectRule::SemanticTransportKnowledge
            | ProjectRule::SemanticMixedConcerns
            // Smell for where mappings live (#2143 (2) — counting only; the hit's severity is warning, so without a declaration it becomes minor).
            | ProjectRule::DictOutsideItsPlace => Some(RuleLevel::Major),
            ProjectRule::UnknownConfigKey
            | ProjectRule::ModuleDeclaresTags
            | ProjectRule::RoleMatchesLayer
            | ProjectRule::RawSideEffectVia
            | ProjectRule::EnvironmentName
            | ProjectRule::DefnForbidden
            | ProjectRule::DeffNeedsReason
            | ProjectRule::DefinitionTagsRequired
            | ProjectRule::ContextMatchesService
            | ProjectRule::UnusedDependency
            | ProjectRule::TestIsDeftest
            | ProjectRule::ClassWithBehaviour
            | ProjectRule::JsonValueOutsideWire
            | ProjectRule::ShapeCheckInJudgment
            | ProjectRule::FailureRethrow
            | ProjectRule::BindThenReturn
            | ProjectRule::FieldsJoinedIntoText
            | ProjectRule::RebuiltAccumulator
            | ProjectRule::EffectsDisagreeWithInference
            | ProjectRule::SemanticPlainCallable
            | ProjectRule::SemanticClassRole => None,
        }
    }

    /// Whether the whole repo is needed to judge a hit — whether the presence of a hit attached to file F can change with the contents, existence, or location of source outside F
    /// is true for rules that (consult whole-repo tables — the module index, the definition graph, the inference world, the list of names — count and cross-check several files,
    /// attach hits to architecture.hy or registry rows, or have "emit if absent" hits). Applying it only to changed paths would not emit them, so the gate and
    /// the commit hook use this claim to choose the rules to apply in a whole-repo comparison (formerly the repo's hand-kept list — a new
    /// error of DOEFF149 and 161, which were forgotten to be added, got into main; L3137). False = decided only by F's contents and the settings. Place a new rule in one or the other (an exhaustive match).
    pub fn needs_whole_repo(self) -> bool {
        match self {
            // Consult the module index (location and existence of import targets): layer direction, service boundary and dependencies, translation intents, dependencies outside locations.
            ProjectRule::LayerImportDirection
            | ProjectRule::ServiceBoundary
            | ProjectRule::ServiceDependency
            | ProjectRule::TranslationEmitsIntent
            | ProjectRule::LowerLayerInDomain
            | ProjectRule::PlacedDependency
            // Follow calls into definitions in other files, or consult whole-repo names, types, and inference: reaching raw side effects, base classes outside the class, failure types,
            // defk names, effect inference, the index of handler-argument classes, the .hy next to a .pyi.
            | ProjectRule::RawSideEffectVia
            | ProjectRule::ClassWithBehaviour
            | ProjectRule::FailureRethrow
            // Consult the absent and failure types declared by defeffect in other files (the same declaration collection as DOEFF122).
            | ProjectRule::StaleOutcomeBranch
            | ProjectRule::DefkCalledBare
            | ProjectRule::EffectsDisagreeWithInference
            | ProjectRule::HandlerArgumentHoldsState
            | ProjectRule::RecordStubNotKwOnly
            // Hits attach to a dir, architecture.hy, or a registry row; several files are cross-checked; emit if a declared thing is missing.
            | ProjectRule::UndeclaredDirectory
            | ProjectRule::UnusedDependency
            | ProjectRule::WorldHandlerMisplaced
            | ProjectRule::BlindDefinitionReads
            | ProjectRule::DefinitionCallsUnlistedHead
            | ProjectRule::SpellingOutsideItsFiles
            | ProjectRule::FieldHoldersDiffer
            | ProjectRule::CallOutsideDeclaredSites
            | ProjectRule::BroadCatchOutsideCarrier
            | ProjectRule::SpellingCountDiffers
            | ProjectRule::EffectOutsideCensus
            | ProjectRule::ServiceInvariantsMissing
            | ProjectRule::ServiceSystemMissing
            | ProjectRule::SystemAccessUnwritten
            // DOEFF209 collects helper definitions (defks and handler clauses that wait with a deadline of now + seconds) from the whole repo, and a change in a helper in another file attaches a hit to a caller.
            | ProjectRule::PollingOnTimer
            | ProjectRule::RegistryEntryStale
            // Follow the reach of tests and production (the definition graph): kinds of tests, services not run on fakes, edge tests, fakes, assembly, counterexamples, coverage.
            | ProjectRule::TestKindMismatch
            | ProjectRule::ServiceUntestedOnSim
            | ProjectRule::WorldHandlerWithoutContractTest
            | ProjectRule::BusinessEffectFake
            | ProjectRule::StaleCounterexampleRow
            | ProjectRule::AssemblyShapeBroken
            | ProjectRule::AssemblyAnswerMisplaced
            | ProjectRule::TestOnlyFake
            | ProjectRule::IntentAnswererNotTranslation
            | ProjectRule::ServiceWithoutCounterexample
            | ProjectRule::ClauseWithoutCounterexample
            | ProjectRule::IntentEffectUncovered
            | ProjectRule::IntentFakedInVerification
            // Decided only by F's contents and the declaration, but a commit that changes architecture.hy's declaration (locations, test-form globs) attaches hits to other files
            // — it stays on the whole-repo side for the same reason the repo's hand-kept list added in #1983.
            | ProjectRule::UndeclaredPlace
            | ProjectRule::TestFormNotDeftest => true,
            ProjectRule::UnknownConfigKey
            | ProjectRule::UnreadableFile
            | ProjectRule::LayerForbiddenModule
            | ProjectRule::LayerTypesOnly
            | ProjectRule::ModuleDeclaresTags
            | ProjectRule::RoleMatchesLayer
            | ProjectRule::RawSideEffectDirect
            | ProjectRule::EnvironmentName
            | ProjectRule::DefnForbidden
            | ProjectRule::DeffNeedsReason
            | ProjectRule::DefinitionTagsRequired
            | ProjectRule::ContextMatchesService
            | ProjectRule::TestIsDeftest
            | ProjectRule::JsonValueOutsideWire
            | ProjectRule::ShapeCheckInJudgment
            | ProjectRule::BindThenReturn
            | ProjectRule::FieldsJoinedIntoText
            | ProjectRule::RebuiltAccumulator
            | ProjectRule::EnvironmentBranch
            | ProjectRule::MatchFieldHyphen
            // DOEFF210 is decided only by the file's contents and the settings' program_sleep locations (a handler implementation's claim is also written by the definition in its own dictionary).
            | ProjectRule::ProgramSleepsOnWallClock
            // DOEFF211 is also decided only by the file's contents and the settings' test_sleep locations (the realtime claim is at the module level of the same file or in a deftest's meta).
            | ProjectRule::TestSleepsOnWallClock
            // Decided by one file (and the layer declaration).
            | ProjectRule::WireInWireFreeLayer
            | ProjectRule::BareMapInWireFreeLayer
            | ProjectRule::DictOutsideItsPlace
            | ProjectRule::WorldHandlerNamedOutsideList
            | ProjectRule::VocabularyOutsideSinglePoint
            | ProjectRule::RetiredWord
            | ProjectRule::RetiredCall
            | ProjectRule::UntypedStructuredValue
            // DOEFF208 is decided only by the file's contents and architecture.hy's :business-fakes (a commit that changes the declaration has split_for_change move all rules
            // to the whole-repo comparison — the same as DOEFF170, 171, and 173). It is applied to staged paths and goes through the entry point for rules without a known list (new_critical that
            // is not lowered by the baseline difference).
            | ProjectRule::RecordChangeWakesJob
            // Semantic rules that ask Jev are not applied by the gate and hook (they ask per definition — the unit of judgment is the definition).
            | ProjectRule::SemanticBusinessDecision
            | ProjectRule::SemanticTransportKnowledge
            | ProjectRule::SemanticPlainCallable
            | ProjectRule::SemanticClassRole
            | ProjectRule::SemanticMixedConcerns => false,
        }
    }

    /// Whether a single-file run (`Target::Single` — the editor's `--stdin --path`) judges this rule. A true rule
    /// has its judgment in the `Target::Single` branch of `run_with` and finishes emitting the hits for that file. A false rule is judged only by a whole-repo run
    /// (`Target::Whole`) — it is not run in a single-file run (`run_with` removes it from the enabled rules by this claim) and is not listed in editor-json's
    /// `judged_rules`, so the editor leaves that rule's violations as in the whole run's result (formerly the single-file run's result replaced all of the file's violations,
    /// and violations such as DOEFF166 and 141 vanished from that file).
    ///
    /// This is a different question from `needs_whole_repo` (whether a hit can change with source outside F — the split between the gate and the commit hook): DOEFF101 consults the whole repo's
    /// module index, but a single-file run also builds the index and judges (true here); DOEFF146 is decided by one file but has no single-file branch
    /// (false here). Place a new rule in one or the other (an exhaustive match).
    pub fn judged_on_one_file(self) -> bool {
        match self {
            // Keys the settings do not know (main adds them in both runs), and Hy files that cannot be read (for a single file, its pre-save contents).
            ProjectRule::UnknownConfigKey
            | ProjectRule::UnreadableFile
            // The judgment of one layer module (judge_layer_file — a single-file run also builds the index of the repo's layer modules and applies it).
            | ProjectRule::LayerImportDirection
            | ProjectRule::LayerForbiddenModule
            | ProjectRule::LayerTypesOnly
            | ProjectRule::ModuleDeclaresTags
            | ProjectRule::RoleMatchesLayer
            | ProjectRule::ServiceBoundary
            | ProjectRule::ContextMatchesService
            | ProjectRule::ServiceDependency
            | ProjectRule::PlacedDependency
            | ProjectRule::EnvironmentBranch
            | ProjectRule::MatchFieldHyphen
            | ProjectRule::WireInWireFreeLayer
            | ProjectRule::BareMapInWireFreeLayer
            | ProjectRule::DictOutsideItsPlace
            // The allowlist of raw side effects — layer files use judge_layer_file, and files outside layer locations use judge_unplaced_world, applied to a single file too.
            | ProjectRule::RawSideEffectDirect
            | ProjectRule::RawSideEffectVia
            | ProjectRule::WorldHandlerNamedOutsideList
            | ProjectRule::TranslationEmitsIntent
            | ProjectRule::LowerLayerInDomain
            // Location (DOEFF115 emits on that file in a single-file run — PlaceScope::Single).
            | ProjectRule::UndeclaredPlace
            | ProjectRule::UndeclaredDirectory
            // Definitions, smells, bare calls, and effect mismatches (one definition file at a time).
            | ProjectRule::DefnForbidden
            | ProjectRule::DeffNeedsReason
            | ProjectRule::DefinitionTagsRequired
            | ProjectRule::TestIsDeftest
            | ProjectRule::ClassWithBehaviour
            | ProjectRule::ShapeCheckInJudgment
            | ProjectRule::FailureRethrow
            | ProjectRule::BindThenReturn
            | ProjectRule::FieldsJoinedIntoText
            | ProjectRule::RebuiltAccumulator
            | ProjectRule::DefkCalledBare
            | ProjectRule::EffectsDisagreeWithInference
            | ProjectRule::StaleOutcomeBranch
            // Declaration rules, one file at a time.
            | ProjectRule::JsonValueOutsideWire
            | ProjectRule::EnvironmentName
            | ProjectRule::ProgramSleepsOnWallClock
            | ProjectRule::TestSleepsOnWallClock
            | ProjectRule::HandlerArgumentHoldsState
            | ProjectRule::RetiredWord
            | ProjectRule::RetiredCall
            | ProjectRule::UntypedStructuredValue
            | ProjectRule::RecordStubNotKwOnly
            // Semantic rules (a single-file run reads the cache — per definition).
            | ProjectRule::SemanticBusinessDecision
            | ProjectRule::SemanticTransportKnowledge
            | ProjectRule::SemanticPlainCallable
            | ProjectRule::SemanticClassRole
            | ProjectRule::SemanticMixedConcerns => true,
            // Hits attach to architecture.hy, a registry row, or the declared :files groups, or several files are counted and cross-checked.
            ProjectRule::UnusedDependency
            | ProjectRule::BlindDefinitionReads
            | ProjectRule::DefinitionCallsUnlistedHead
            | ProjectRule::SpellingOutsideItsFiles
            | ProjectRule::SpellingCountDiffers
            | ProjectRule::EffectOutsideCensus
            | ProjectRule::FieldHoldersDiffer
            | ProjectRule::CallOutsideDeclaredSites
            | ProjectRule::BroadCatchOutsideCarrier
            | ProjectRule::ServiceInvariantsMissing
            | ProjectRule::ServiceSystemMissing
            | ProjectRule::SystemAccessUnwritten
            | ProjectRule::RecordChangeWakesJob
            | ProjectRule::PollingOnTimer
            | ProjectRule::RegistryEntryStale
            // Follow the definition graph (reach of tests and production) across the whole repo.
            | ProjectRule::WorldHandlerMisplaced
            | ProjectRule::TestKindMismatch
            | ProjectRule::WorldHandlerWithoutContractTest
            | ProjectRule::ServiceUntestedOnSim
            | ProjectRule::BusinessEffectFake
            | ProjectRule::StaleCounterexampleRow
            | ProjectRule::AssemblyShapeBroken
            | ProjectRule::AssemblyAnswerMisplaced
            | ProjectRule::TestOnlyFake
            | ProjectRule::IntentAnswererNotTranslation
            | ProjectRule::ServiceWithoutCounterexample
            | ProjectRule::ClauseWithoutCounterexample
            | ProjectRule::IntentEffectUncovered
            | ProjectRule::IntentFakedInVerification
            // Decided by one file, but it does not yet have a single-file branch (a whole run walks the groups of files).
            | ProjectRule::TestFormNotDeftest
            | ProjectRule::VocabularyOutsideSinglePoint => false,
        }
    }

    /// Whether the rule is one whose hits listed in the registry (the table of known hits) may be lowered to warning. DOEFF175 is not lowered either — it is the gate for a switch, and the version is not landed
    /// until the hits reach 0 (law declarations-switch-one-effect-at-a-time), so letting the known list pass it would open the gate. DOEFF206 is not lowered — if a rule that stops tables letting violations through
    /// could be bypassed through another table, a hole would remain (do not open the same hole twice). DOEFF207, 208, and 209 are not lowered either —
    /// existing hits are not put into a table to let them pass (DOEFF208, 209). DOEFF211 is not lowered either — the only way out of the test-sleep rule is
    /// the realtime claim (and the repo's roster that lists claims together with reasons).
    pub fn lowered_by_registry(self) -> bool {
        !matches!(
            self,
            ProjectRule::IntentFakedInVerification
                | ProjectRule::SystemAccessUnwritten
                | ProjectRule::RecordChangeWakesJob
                | ProjectRule::PollingOnTimer
                | ProjectRule::LowerLayerInDomain
                | ProjectRule::StaleOutcomeBranch
                | ProjectRule::TestSleepsOnWallClock
        )
    }

    /// Whether it is a smell rule (DOEFF121 to 125 — default severity warning, and can be lowered to info by the settings' severity).
    pub fn is_smell(self) -> bool {
        matches!(
            self,
            ProjectRule::ShapeCheckInJudgment
                | ProjectRule::FailureRethrow
                | ProjectRule::BindThenReturn
                | ProjectRule::FieldsJoinedIntoText
                | ProjectRule::RebuiltAccumulator
        )
    }

    /// Whether it is a semantic rule that asks Jev (DOEFF201 to 205) — the list of false judgments applies only to hits of these rules.
    pub fn is_semantic(self) -> bool {
        matches!(
            self,
            ProjectRule::SemanticBusinessDecision
                | ProjectRule::SemanticTransportKnowledge
                | ProjectRule::SemanticPlainCallable
                | ProjectRule::SemanticClassRole
                | ProjectRule::SemanticMixedConcerns
        )
    }

    /// Look up a rule from the spelling of its ID (case-insensitive). None if it is not a layer rule.
    pub fn parse(id: &str) -> Option<ProjectRule> {
        let upper = id.to_uppercase();
        ProjectRule::ALL.iter().copied().find(|rule| rule.id() == upper)
    }

    /// Whether it is a rule from which, in a layer declaration's `:exempt [(rule rule-ID "reason")]`, a named module can be excluded from the population with a reason.
    /// Only the definition-writing rules judged per definition file — critical (DOEFF126, bare defk call) and rules not judged per file (service, layer,
    /// Jev) cannot be excluded (the one point of exclusion is right after the judgment of a definition file, so writing any other ID would silently have no effect).
    pub fn exemptible_by_layer(self) -> bool {
        matches!(
            self,
            ProjectRule::DefnForbidden
                | ProjectRule::DeffNeedsReason
                | ProjectRule::DefinitionTagsRequired
                | ProjectRule::TestIsDeftest
                | ProjectRule::ClassWithBehaviour
        )
    }

    /// Whether it is a rule judged per layer (a rule where the law's layers take effect). DOEFF108 applies to all business files and has no layers.
    pub fn is_layered(self) -> bool {
        match self {
            ProjectRule::LayerImportDirection
            | ProjectRule::LayerForbiddenModule
            | ProjectRule::LayerTypesOnly
            | ProjectRule::ModuleDeclaresTags
            | ProjectRule::RoleMatchesLayer
            | ProjectRule::RawSideEffectDirect
            | ProjectRule::RawSideEffectVia
            | ProjectRule::WorldHandlerNamedOutsideList
            | ProjectRule::ServiceBoundary
            | ProjectRule::ContextMatchesService
            | ProjectRule::ServiceDependency
            | ProjectRule::PlacedDependency
            | ProjectRule::TranslationEmitsIntent
            | ProjectRule::LowerLayerInDomain
            | ProjectRule::EnvironmentBranch
            | ProjectRule::MatchFieldHyphen
            | ProjectRule::WireInWireFreeLayer
            | ProjectRule::BareMapInWireFreeLayer
            | ProjectRule::DictOutsideItsPlace
            | ProjectRule::SemanticBusinessDecision
            | ProjectRule::SemanticTransportKnowledge => true,
            ProjectRule::UnknownConfigKey
            | ProjectRule::UnreadableFile
            | ProjectRule::UndeclaredPlace
            | ProjectRule::UndeclaredDirectory
            | ProjectRule::UnusedDependency
            | ProjectRule::WorldHandlerMisplaced
            | ProjectRule::TestKindMismatch
            | ProjectRule::WorldHandlerWithoutContractTest
            | ProjectRule::TestFormNotDeftest
            | ProjectRule::VocabularyOutsideSinglePoint
            | ProjectRule::SpellingOutsideItsFiles
            | ProjectRule::SpellingCountDiffers
            | ProjectRule::EffectOutsideCensus
            | ProjectRule::FieldHoldersDiffer
            | ProjectRule::ServiceUntestedOnSim
            | ProjectRule::ServiceInvariantsMissing
            | ProjectRule::ServiceSystemMissing
            | ProjectRule::SystemAccessUnwritten
            | ProjectRule::RecordChangeWakesJob
            | ProjectRule::PollingOnTimer
            | ProjectRule::ProgramSleepsOnWallClock
            | ProjectRule::TestSleepsOnWallClock
            | ProjectRule::RetiredWord
            | ProjectRule::RetiredCall
            | ProjectRule::BlindDefinitionReads
            | ProjectRule::DefinitionCallsUnlistedHead
            | ProjectRule::CallOutsideDeclaredSites
            | ProjectRule::BroadCatchOutsideCarrier
            | ProjectRule::HandlerArgumentHoldsState
            | ProjectRule::UntypedStructuredValue
            | ProjectRule::RecordStubNotKwOnly
            | ProjectRule::BusinessEffectFake
            | ProjectRule::StaleCounterexampleRow
            | ProjectRule::AssemblyShapeBroken
            | ProjectRule::AssemblyAnswerMisplaced
            | ProjectRule::TestOnlyFake
            | ProjectRule::IntentAnswererNotTranslation
            | ProjectRule::ServiceWithoutCounterexample
            | ProjectRule::ClauseWithoutCounterexample => false,
            | ProjectRule::IntentEffectUncovered
            | ProjectRule::IntentFakedInVerification
            | ProjectRule::RegistryEntryStale => false,
            ProjectRule::EnvironmentName
            | ProjectRule::DefnForbidden
            | ProjectRule::DeffNeedsReason
            | ProjectRule::DefinitionTagsRequired
            | ProjectRule::TestIsDeftest
            | ProjectRule::ClassWithBehaviour
            | ProjectRule::JsonValueOutsideWire
            | ProjectRule::ShapeCheckInJudgment
            | ProjectRule::FailureRethrow
            | ProjectRule::BindThenReturn
            | ProjectRule::FieldsJoinedIntoText
            | ProjectRule::RebuiltAccumulator
            | ProjectRule::DefkCalledBare
            | ProjectRule::EffectsDisagreeWithInference
            | ProjectRule::StaleOutcomeBranch
            | ProjectRule::SemanticMixedConcerns
            | ProjectRule::SemanticPlainCallable
            | ProjectRule::SemanticClassRole => false,
        }
    }

    /// A short name (the shape of the violation; used as a heading in the editor's list).
    pub fn label(self) -> &'static str {
        match self {
            ProjectRule::UnknownConfigKey => "Key the settings do not know",
            ProjectRule::UnreadableFile => "Hy file cannot be read",
            ProjectRule::LayerImportDirection => "Import against the layer direction",
            ProjectRule::LayerForbiddenModule => "Import of a module forbidden in the layer",
            ProjectRule::LayerTypesOnly => "Function in a types-only layer",
            ProjectRule::ModuleDeclaresTags => "Missing context and role tags",
            ProjectRule::RoleMatchesLayer => "Role that does not fit the layer",
            ProjectRule::RawSideEffectDirect => "Raw side effect in a disallowed layer",
            ProjectRule::RawSideEffectVia => "Reaches a raw side effect through a callee",
            ProjectRule::EnvironmentName => "Environment word in a business name",
            ProjectRule::ServiceBoundary => "Reads the inside of another service",
            ProjectRule::DefnForbidden => "uses defn",
            ProjectRule::DeffNeedsReason => "deff has no reason comment",
            ProjectRule::DefinitionTagsRequired => "Definition's :tags are incomplete",
            ProjectRule::ContextMatchesService => ":context differs from the service",
            ProjectRule::UndeclaredPlace => "Module in a location not in the declaration",
            ProjectRule::UndeclaredDirectory => "Dir not in the declaration",
            ProjectRule::ServiceDependency => "Dependency on a service not in the declaration",
            ProjectRule::PlacedDependency => "Dependency on a module outside the locations",
            ProjectRule::BlindDefinitionReads => "Judgment definition reading outside its decided material",
            ProjectRule::DefinitionCallsUnlistedHead => "Definition calling outside the list of allowed heads",
            ProjectRule::CallOutsideDeclaredSites => "Head called outside its decided places",
            ProjectRule::BroadCatchOutsideCarrier => "Broad exception catch outside a carrying boundary",
            ProjectRule::UnusedDependency => "Unused dependency",
            ProjectRule::TestIsDeftest => "Test that is not a deftest",
            ProjectRule::ClassWithBehaviour => "class with logic",
            ProjectRule::JsonValueOutsideWire => "JsonValue outside send/receive",
            ProjectRule::ShapeCheckInJudgment => "Input shape check in a judgment layer",
            ProjectRule::FailureRethrow => "Merely returns a received failure again",
            ProjectRule::BindThenReturn => "Merely returns right after <-",
            ProjectRule::FieldsJoinedIntoText => "Joins fields into one string",
            ProjectRule::RebuiltAccumulator => "Rebuilds an accumulator inside a loop",
            ProjectRule::DefkCalledBare => "Calls a defk bare and uses it as the answer",
            ProjectRule::EffectsDisagreeWithInference => ":effects declaration disagrees with inference",
            ProjectRule::StaleOutcomeBranch => "Stale branch separating declared absent/failure types as values",
            ProjectRule::TranslationEmitsIntent => "Translation handler emits a business intent",
            ProjectRule::LowerLayerInDomain => "Business layer uses a lower layer's effect or handler directly",
            ProjectRule::EnvironmentBranch => "Business layer branches on an environment name or dry-run flag",
            ProjectRule::MatchFieldHyphen => "Field name with - in a match class pattern (never matches)",
            ProjectRule::WireInWireFreeLayer => "defwire in a layer that passes values by type only",
            ProjectRule::BareMapInWireFreeLayer => "Mapping or untyped tuple in a contract or field of a layer that passes values by type only",
            ProjectRule::DictOutsideItsPlace => "Mapping built outside a handler, or a mapping field",
            ProjectRule::WorldHandlerNamedOutsideList => "Names a real I/O handler outside the allowlist",
            ProjectRule::WorldHandlerMisplaced => "Allowlist definition missing or outside foundation",
            ProjectRule::TestKindMismatch => "Test kind (local / edge) disagrees with its marker",
            ProjectRule::WorldHandlerWithoutContractTest => "Allowlist handler without an edge test, and its counterpart",
            ProjectRule::TestFormNotDeftest => "Test form other than deftest",
            ProjectRule::VocabularyOutsideSinglePoint => "Same vocabulary read outside the single point of judgment",
            ProjectRule::SpellingOutsideItsFiles => "Spelling outside the files allowed to write it",
            ProjectRule::SpellingCountDiffers => "Spelling whose count is not the decided number",
            ProjectRule::EffectOutsideCensus => "Effect declaration that disagrees with the list",
            ProjectRule::FieldHoldersDiffer => "Holders of type fields that disagree with the list",
            ProjectRule::ServiceUntestedOnSim => "Service not run by tests of the fake environment",
            ProjectRule::ServiceInvariantsMissing => "Service that declares no invariants",
            ProjectRule::ServiceSystemMissing => "Service that declares no system",
            ProjectRule::SystemAccessUnwritten => "Production job reads something that has no writer",
            ProjectRule::RecordChangeWakesJob => "Wakes a job by waiting for record changes",
            ProjectRule::PollingOnTimer => "Fetches the same thing with time waits in between",
            ProjectRule::ProgramSleepsOnWallClock => "Program sleeps on the wall clock",
            ProjectRule::TestSleepsOnWallClock => "Test sleeps on the wall clock without declaring it",
            ProjectRule::RetiredWord => "Spelling decided as not to be used",
            ProjectRule::RetiredCall => "Call decided as not to be used",
            ProjectRule::HandlerArgumentHoldsState => "Handler argument takes a client or mutable store",
            ProjectRule::UntypedStructuredValue => "Public-surface type is a plain mapping or plain tuple",
            ProjectRule::RecordStubNotKwOnly => "@dataclass in the type declaration lacks kw_only=True",
            ProjectRule::BusinessEffectFake => "Fake handler answering a business effect",
            ProjectRule::StaleCounterexampleRow => "Stale row of the counterexample table that matches nothing",
            ProjectRule::AssemblyShapeBroken => "Breach of the shape of assembly",
            ProjectRule::AssemblyAnswerMisplaced => "Translation target or base answer is a business effect",
            ProjectRule::TestOnlyFake => "Test-only fake",
            ProjectRule::IntentAnswererNotTranslation => "Answerer of an intent effect is not exactly one translation",
            ProjectRule::ServiceWithoutCounterexample => "Service with no counterexample of a broken handler",
            ProjectRule::ClauseWithoutCounterexample => "Invariant clause with neither a counterexample nor an exclusion reason",
            ProjectRule::IntentEffectUncovered => "Gap in coverage of an intent effect",
            ProjectRule::IntentFakedInVerification => "Verification environment answers an intent effect itself",
            ProjectRule::RegistryEntryStale => "Stale registry row that matches nothing",
            ProjectRule::SemanticBusinessDecision => "Business judgment in a translation layer (Jev)",
            ProjectRule::SemanticTransportKnowledge => "Judgment layer knows the means of communication (Jev)",
            ProjectRule::SemanticPlainCallable => "deff reason does not fit (Jev)",
            ProjectRule::SemanticClassRole => "class is a window to the outside or holds state (Jev)",
            ProjectRule::SemanticMixedConcerns => "Input shape check mixed with judgment (Jev)",
        }
    }

    /// The rule family (a classification that groups rules in the editor's list).
    pub fn family(self) -> RuleFamily {
        match self {
            ProjectRule::LayerImportDirection
            | ProjectRule::LayerForbiddenModule
            | ProjectRule::LayerTypesOnly
            | ProjectRule::TranslationEmitsIntent
            | ProjectRule::LowerLayerInDomain
            | ProjectRule::EnvironmentBranch
            | ProjectRule::MatchFieldHyphen
            | ProjectRule::WireInWireFreeLayer
            | ProjectRule::BareMapInWireFreeLayer => {
                RuleFamily::Layer
            }
            ProjectRule::ModuleDeclaresTags | ProjectRule::RoleMatchesLayer | ProjectRule::ContextMatchesService => {
                RuleFamily::Tags
            }
            ProjectRule::RawSideEffectDirect | ProjectRule::RawSideEffectVia | ProjectRule::WorldHandlerNamedOutsideList | ProjectRule::WorldHandlerMisplaced
            | ProjectRule::TestKindMismatch
            | ProjectRule::WorldHandlerWithoutContractTest => {
                RuleFamily::Raw
            }
            ProjectRule::EnvironmentName | ProjectRule::RetiredWord | ProjectRule::RetiredCall | ProjectRule::VocabularyOutsideSinglePoint
            | ProjectRule::SpellingOutsideItsFiles
            | ProjectRule::SpellingCountDiffers
            | ProjectRule::EffectOutsideCensus
            | ProjectRule::FieldHoldersDiffer => RuleFamily::Naming,
            ProjectRule::UnknownConfigKey
            | ProjectRule::UnreadableFile
            | ProjectRule::ServiceBoundary
            | ProjectRule::UndeclaredPlace
            | ProjectRule::UndeclaredDirectory
            | ProjectRule::ServiceDependency
            | ProjectRule::PlacedDependency
            | ProjectRule::BlindDefinitionReads
            | ProjectRule::UnusedDependency => RuleFamily::Place,
            ProjectRule::DefnForbidden
            | ProjectRule::DeffNeedsReason
            | ProjectRule::DefinitionTagsRequired
            | ProjectRule::TestIsDeftest
            | ProjectRule::TestFormNotDeftest
            | ProjectRule::ServiceUntestedOnSim
            | ProjectRule::ServiceInvariantsMissing
            | ProjectRule::ServiceSystemMissing
            | ProjectRule::SystemAccessUnwritten
            | ProjectRule::RecordChangeWakesJob
            | ProjectRule::PollingOnTimer
            | ProjectRule::ProgramSleepsOnWallClock
            | ProjectRule::TestSleepsOnWallClock
            | ProjectRule::DefinitionCallsUnlistedHead
            | ProjectRule::CallOutsideDeclaredSites
            | ProjectRule::BroadCatchOutsideCarrier
            | ProjectRule::HandlerArgumentHoldsState
            | ProjectRule::UntypedStructuredValue
            | ProjectRule::RecordStubNotKwOnly
            | ProjectRule::BusinessEffectFake
            | ProjectRule::StaleCounterexampleRow
            | ProjectRule::AssemblyShapeBroken
            | ProjectRule::AssemblyAnswerMisplaced
            | ProjectRule::TestOnlyFake
            | ProjectRule::IntentAnswererNotTranslation
            | ProjectRule::ServiceWithoutCounterexample
            | ProjectRule::ClauseWithoutCounterexample
            | ProjectRule::IntentEffectUncovered
            | ProjectRule::IntentFakedInVerification
            | ProjectRule::RegistryEntryStale
            | ProjectRule::DefkCalledBare
            | ProjectRule::StaleOutcomeBranch
            | ProjectRule::EffectsDisagreeWithInference => RuleFamily::Definition,
            ProjectRule::ClassWithBehaviour | ProjectRule::SemanticClassRole => RuleFamily::Class,
            ProjectRule::JsonValueOutsideWire => RuleFamily::Wire,
            ProjectRule::ShapeCheckInJudgment
            | ProjectRule::FailureRethrow
            | ProjectRule::BindThenReturn
            | ProjectRule::FieldsJoinedIntoText
            | ProjectRule::RebuiltAccumulator
            | ProjectRule::DictOutsideItsPlace
            | ProjectRule::SemanticMixedConcerns => RuleFamily::Smell,
            ProjectRule::SemanticBusinessDecision | ProjectRule::SemanticTransportKnowledge | ProjectRule::SemanticPlainCallable => {
                RuleFamily::Jev
            }
        }
    }

    /// The title (a short human-readable name).
    pub fn title(self) -> &'static str {
        match self {
            ProjectRule::UnknownConfigKey => "Unknown Config Key",
            ProjectRule::UnreadableFile => "Unreadable Hy File",
            ProjectRule::LayerImportDirection => "Layer Import Direction",
            ProjectRule::LayerForbiddenModule => "Layer Forbidden Module",
            ProjectRule::LayerTypesOnly => "Types-Only Layer",
            ProjectRule::ModuleDeclaresTags => "Module Declares Tags",
            ProjectRule::RoleMatchesLayer => "Role Matches Layer",
            ProjectRule::RawSideEffectDirect => "Raw Side Effect Placement",
            ProjectRule::RawSideEffectVia => "Raw Side Effect Via Call",
            ProjectRule::EnvironmentName => "Environment Name In Business Code",
            ProjectRule::ServiceBoundary => "Service Boundary",
            ProjectRule::DefnForbidden => "No defn",
            ProjectRule::DeffNeedsReason => "deff Needs A Reason",
            ProjectRule::DefinitionTagsRequired => "Definition Tags Required",
            ProjectRule::ContextMatchesService => "Context Matches Service",
            ProjectRule::UndeclaredPlace => "Undeclared Place",
            ProjectRule::UndeclaredDirectory => "Undeclared Directory",
            ProjectRule::ServiceDependency => "Service Dependency",
            ProjectRule::PlacedDependency => "Placed Dependency",
            ProjectRule::BlindDefinitionReads => "Blind Definition Reads Beyond Its Inputs",
            ProjectRule::DefinitionCallsUnlistedHead => "Definition Calls A Head Outside Its Allowed List",
            ProjectRule::CallOutsideDeclaredSites => "Head Called Outside Its Declared Sites",
            ProjectRule::BroadCatchOutsideCarrier => "Broad Catch Outside Declared Carriers",
            ProjectRule::UnusedDependency => "Unused Dependency",
            ProjectRule::TestIsDeftest => "Tests Are deftest",
            ProjectRule::ClassWithBehaviour => "Class Touches The World Or Holds State",
            ProjectRule::JsonValueOutsideWire => "JsonValue Outside Wire Modules",
            ProjectRule::ShapeCheckInJudgment => "Shape Check In Judgment",
            ProjectRule::FailureRethrow => "Hand-Written Failure Rethrow",
            ProjectRule::BindThenReturn => "Bind Then Return",
            ProjectRule::FieldsJoinedIntoText => "Fields Joined Into Text",
            ProjectRule::RebuiltAccumulator => "Rebuilt Accumulator",
            ProjectRule::DefkCalledBare => "defk Called Bare",
            ProjectRule::EffectsDisagreeWithInference => "Effects Disagree With Inference",
            ProjectRule::StaleOutcomeBranch => "Stale Branch On A Declared Absent Or Failure",
            ProjectRule::TranslationEmitsIntent => "Translation Emits Intent",
            ProjectRule::LowerLayerInDomain => "Lower Layer In Domain",
            ProjectRule::EnvironmentBranch => "Environment Branch In Business Code",
            ProjectRule::MatchFieldHyphen => "Hyphenated Field In Match Class Pattern",
            ProjectRule::WireInWireFreeLayer => "Wire Type In Wire-Free Layer",
            ProjectRule::BareMapInWireFreeLayer => "Bare Mapping In Wire-Free Layer",
            ProjectRule::DictOutsideItsPlace => "Dict Outside Its Place",
            ProjectRule::WorldHandlerNamedOutsideList => "World Handler Named Outside The List",
            ProjectRule::WorldHandlerMisplaced => "World Handler Misplaced",
            ProjectRule::TestKindMismatch => "Test Kind Mismatch",
            ProjectRule::WorldHandlerWithoutContractTest => "World Handler Without Contract Test",
            ProjectRule::TestFormNotDeftest => "Test Form Not Deftest",
            ProjectRule::VocabularyOutsideSinglePoint => "Vocabulary Outside Single Point",
            ProjectRule::SpellingOutsideItsFiles => "Spelling Outside Its Files",
            ProjectRule::SpellingCountDiffers => "Spelling Count Differs",
            ProjectRule::EffectOutsideCensus => "Effect Outside Census",
            ProjectRule::FieldHoldersDiffer => "Field Holders Differ",
            ProjectRule::ServiceUntestedOnSim => "Service Untested On Sim",
            ProjectRule::ServiceInvariantsMissing => "Service Invariants Missing",
            ProjectRule::ServiceSystemMissing => "Service System Missing",
            ProjectRule::SystemAccessUnwritten => "System Access Unwritten",
            ProjectRule::RecordChangeWakesJob => "Record Change Wakes Job",
            ProjectRule::PollingOnTimer => "Polling On Timer",
            ProjectRule::ProgramSleepsOnWallClock => "Program Sleeps On The Wall Clock",
            ProjectRule::TestSleepsOnWallClock => "Test Sleeps On The Wall Clock",
            ProjectRule::RetiredWord => "Retired Word",
            ProjectRule::RetiredCall => "Retired Call",
            ProjectRule::HandlerArgumentHoldsState => "Handler Argument Holds State",
            ProjectRule::UntypedStructuredValue => "Untyped Structured Value",
            ProjectRule::RecordStubNotKwOnly => "Record Stub Not Kw Only",
            ProjectRule::BusinessEffectFake => "Business Effect Fake",
            ProjectRule::StaleCounterexampleRow => "Stale Counterexample Row",
            ProjectRule::AssemblyShapeBroken => "Assembly Shape Broken",
            ProjectRule::AssemblyAnswerMisplaced => "Assembly Answer Misplaced",
            ProjectRule::TestOnlyFake => "Test Only Fake",
            ProjectRule::IntentAnswererNotTranslation => "Intent Answerer Not Translation",
            ProjectRule::ServiceWithoutCounterexample => "Service Without Counterexample",
            ProjectRule::ClauseWithoutCounterexample => "Clause Without Counterexample",
            ProjectRule::IntentEffectUncovered => "Intent Effect Uncovered",
            ProjectRule::IntentFakedInVerification => "Intent Effect Faked In Verification",
            ProjectRule::RegistryEntryStale => "Registry Entry Stale",
            ProjectRule::SemanticBusinessDecision => "Business Decision In Translation (Jev)",
            ProjectRule::SemanticTransportKnowledge => "Transport Knowledge In Core (Jev)",
            ProjectRule::SemanticPlainCallable => "Plain Callable Reason (Jev)",
            ProjectRule::SemanticClassRole => "Class Role (Jev)",
            ProjectRule::SemanticMixedConcerns => "Shape Check Mixed With Judgment (Jev)",
        }
    }

    /// The rule statement (shown in the list when no law is linked).
    pub fn statement(self) -> &'static str {
        match self {
            ProjectRule::UnreadableFile => "A Hy file can be read to the end by the reader (a file that cannot be read has missing violations, so it is reported as an error regardless of the list of enabled rules)",
            ProjectRule::UnknownConfigKey => "The keys and rule IDs of the settings (pyproject's [tool.doeff-linter], architecture.hy) are only ones this linter knows — an unknown one is reported while the remaining rules run without reading that key (the linter is older than the settings, or a typo)",
            ProjectRule::LayerImportDirection => "A layer's module imports only modules of the layers the settings allow (imports from outside the repo are not counted)",
            ProjectRule::LayerForbiddenModule => "A layer's module does not directly import modules forbidden in that layer (such as I/O modules)",
            ProjectRule::LayerTypesOnly => "A module in a types-only layer defines no functions or handlers",
            ProjectRule::ModuleDeclaresTags => "A definition in a layer's module declares its context and role with the definition's :tags or the module's head tags",
            ProjectRule::RoleMatchesLayer => "The tag's role is one of the roles allowed in that module's layer, and the context is declared too",
            ProjectRule::RawSideEffectDirect => "Definitions that directly touch raw side effects (http, time, randomness, files, processes, environment variables …) are placed only in layers the settings allow",
            ProjectRule::RawSideEffectVia => "A notice of definitions that reach raw side effects through called definitions (a fact, not a violation) — it does not follow past definitions declared in :world-handlers",
            ProjectRule::EnvironmentName => "Do not put environment words in the names of business files, handlers, and assembly functions",
            ProjectRule::ServiceBoundary => "One service's judgment and translation layers (the settings' guarded_layers) do not read the same layers of another service. Only another service's open_layers and the shared location may be read",
            ProjectRule::DefnForbidden => "Write Hy definitions with defk, not defn / defn/a (except functions at macro-expansion time and locations excluded by the settings)",
            ProjectRule::DeffNeedsReason => "On the line of a deff definition or the line before it, write a comment giving the reason it cannot be a defk",
            ProjectRule::DefinitionTagsRequired => "defk, deff, defp, defhandler, and defeffect declare the required keys (settings) in the contract dictionary's :tags",
            ProjectRule::ContextMatchesService => "The tag's :context matches the name of the service the file is placed in (a notice)",
            ProjectRule::UndeclaredPlace => "A module under root is placed in one of the service layers, shared, foundation, or legacy declared in architecture.hy",
            ProjectRule::UndeclaredDirectory => "A dir under root is a declared service or shared, foundation, or legacy, and a dir inside a service is a declared layer",
            ProjectRule::ServiceDependency => "Service A may read only the services in A's :depends-on, at the layers readable from A's module's layer (that layer's :dependency-layers — the assembly layer reads intent and protocol —, or otherwise intent in :open-layers), and shared",
            ProjectRule::BlindDefinitionReads => "Definitions in architecture.hy's :blind-definitions are judged only on decided material — the bodies (excluding comments) of the repo's Hy definitions reached transitively by calls and names from that definition contain no spelling of :forbid-words, and with :no-imports the definition's module has no import or require (a require of a module in :allow-requires is excluded because it loads a macro). The declared definition exists",
            ProjectRule::DefinitionCallsUnlistedHead => "A definition in architecture.hy's :allowed-heads has only the head forms listed in :heads — if the spelling (symbol or keyword) of the head of a `( … )` inside the definition's form (including nesting, excluding strings and comments) is outside :heads, it is a hit for each such head. The declared definition exists",
            ProjectRule::CallOutsideDeclaredSites => "A head in architecture.hy's :call-sites is called, within the Hy files of :files (excluding :except), only inside the definitions in :sites — a place with :count is exactly that number, a call at a place with :parent has the head of the immediately enclosing form with that spelling, and a call at a place with :branch is inside a branch of a cond, when, if, or unless whose condition form contains that symbol. The place's definition exists, and at least one file matches :files",
            ProjectRule::BroadCatchOutsideCarrier => "Do not place broad exception catches (`(except [] …)`, `(except [… Exception …] …)`, BaseException, AssertionError, Python's `except:` and `except Exception:`) in the Hy and Python files of architecture.hy's :broad-catches :files (excluding :except) — the only thing allowed is, inside a :carriers definition, a catch that binds the exception by name and passes that name within the catch to the :event event's argument (a boundary that carries a contract violation into a list instead of swallowing it). The carrier-boundary definition exists, and at least one file matches :files",
            ProjectRule::PlacedDependency => "A layer's module (service and shared) in architecture.hy's :placed-dependencies depends only on modules in the layer locations (a service's layer, shared's layer, foundation, and dirs that have a layer-name segment) — it does not import a module under root whose location is undecided (directly under a service dir, or inside a dir not in the declaration)",
            ProjectRule::UnusedDependency => "No module of the service reads a declared dependency (:depends-on) (a notice)",
            ProjectRule::TestIsDeftest => "Tests in test locations (the settings' test_paths) are written with deftest — do not place bindings of defn, deff, defk, or fn whose name starts with test- / test_",
            ProjectRule::ClassWithBehaviour => "A defclass in business code is only a value class — do not write a class whose methods or field initial values touch raw side effects (error), or a class whose methods rewrite self's fields (warning). For a class with only fields, defrecord is recommended (info). Classes that inherit exceptions, Enum, Protocol, or bases from external libraries are allowed. It is not judged by name",
            ProjectRule::JsonValueOutsideWire => "JsonValue, JSONValue, JsonObject, and JSONObject (types that merely wrap a plain dict, list, str … with a name) may be used only by generic solvers (doeff_hy._wire, doeff_records._wire) and the foundation-layer send/receive modules listed in architecture.hy's :wire-modules. Other modules (protocol, intent, core …) see only typed values whose shape the solver has verified",
            ProjectRule::SemanticBusinessDecision => "A translation-layer definition merely rephrases a request in the other party's way of speaking and makes no business judgment (a Jev judgment; warning or info)",
            ProjectRule::SemanticTransportKnowledge => "A judgment-layer definition does not know the means of communication (URL, HTTP, JSON wire, SQL) (a Jev judgment; warning or info)",
            ProjectRule::SemanticPlainCallable => "The text of a deff's reason comment matches a reason architecture.hy accepts (such as an external library calling a plain function) (a Jev judgment; warning or info)",
            ProjectRule::ShapeCheckInJudgment => "A definition in a judgment layer (the settings' smells.shape_check_layers) does not check the shape of input with a string-key (.get x \"field\") and isinstance on that field",
            ProjectRule::FailureRethrow => "A match arm does not merely receive a failure type (declared by defrecord's :failure True and defeffect's :failure / :absent) and return the received value or a re-wrapped value",
            ProjectRule::BindThenReturn => "Do not put (return x) right after (<- x T (f …)) with x used nowhere else",
            ProjectRule::FieldsJoinedIntoText => "Do not join two or more fields of the same value into one string with + or an f-string",
            ProjectRule::RebuiltAccumulator => "Do not rebuild an accumulator each time inside for / while with (:= xs (+ xs #(…)))",
            ProjectRule::EffectsDisagreeWithInference => "If a defk writes :effects, make it the same set as the effects inferred from the calls fired in the body ((<- …), (! …)) — do not raise an effect not in the declaration, and do not declare an effect that is not raised (a defk without :effects is out of scope)",
            ProjectRule::StaleOutcomeBranch => "Do not separate, as values, the types that defeffect declared with :absent / :failure on a variable that binds the effect's answer ((<- x (E …)), (val x (! (E …)))) — match arms, isinstance (also a tuple of types, and cond branches). <- and ! do not bind those types but let them escape as Absent / Raise, so the branch never runs (ADR-DOE-CORE-EFFECTS-003 R14 (4))",
            ProjectRule::TestFormNotDeftest => "Tests are written only with deftest and collected by pytest — do not place Python def test_*, per-module skip, check scripts that run outside pytest, or runners that run deftests themselves. Files are selected by the spelling patterns of architecture.hy's :test-forms",
            ProjectRule::VocabularyOutsideSinglePoint => "The judgment of a vocabulary (a job's phase, a turn's state, a bearer's field, etc.) is confined to one file (architecture.hy's :single-point-vocabulary :except) — if another file reads a spelling that matches the same regular expression, that becomes a second judgment point, and a fix to only one of them makes them disagree",
            ProjectRule::SpellingOutsideItsFiles => "The spelling (regular expression) of a group in architecture.hy's :confined-spellings is written only in the :except files (if :except is empty, it is written nowhere in :files) — reads the body, with comments dropped (strings are counted too), of the Hy and Python files matching :files. A group with no file matching :files is also a hit (a population of 0 is not made green)",
            ProjectRule::SpellingCountDiffers => "For each declaration in architecture.hy's :counted-spellings, the number of hits of :pattern in the files matching :files (comments dropped for Hy and Python; strings counted) fits :count (exactly) or :at-least (or more) — with :within, it is counted inside each named top-level definition. A declaration with no file or definition to count is also a hit (a population of 0 is not made green)",
            ProjectRule::EffectOutsideCensus => "For each declaration in architecture.hy's :effect-census, the set of class declarations inheriting :base in the Hy and Python files of :files (excluding comments) matches the :effects list — a declaration not in the list, the same name declared twice, and a listed effect with no declaration are hits. A declaration with no file matching :files is also a hit (a population of 0 is not made green)",
            ProjectRule::FieldHoldersDiffer => "For each declaration in architecture.hy's :field-holders, among the module-level classes of the Python files of :files, the classes whose body-level fields (annotated assignments) have an annotation with the spelling :type as a word are exactly the :holders list — a holder not in the list, and a listed class that does not have (or lacks) that field are hits. With :classes, only the named classes are counted, and a missing class is also a hit. A declaration with no Python file matching :files is also a hit (a population of 0 is not made green)",
            ProjectRule::RetiredWord => "Do not write in the files of a group's :files the spellings decided as not to be used by architecture.hy's :retired-words — :words are where it stands alone as a word (not preceded or followed by letters, _, or -), :patterns are per-line regular expressions, :in names looks only at definition names, and :in paths looks only at file names. Lines containing a :rule-lines spelling (lines that state the rule itself), and comments, docstrings, and .md files under :in lines (spellings not in use) are not counted. Words in the key names of contract files in :contract-files and the values of enum / const are not counted in Hy / Python strings whose content is exactly that word or in the names of defwire field definitions (contract and wire field names stay as the contract's spelling)",
            ProjectRule::RetiredCall => "Do not call, in the Hy files of a group's :files, the calls decided as not to be used by architecture.hy's :retired-calls (retired effects, clocks …) — counts forms whose head symbol is a :calls spelling; comments, strings, and discarded forms are not counted",
            ProjectRule::UntypedStructuredValue => "A structured value is expressed with a type that statically has field names and types (frozen dataclass, defrecord, defwire) — in the public surface (names not starting with _) of the files in architecture.hy's :typed-values, do not write in class fields, function and method return values, or the types of defk / deff :post a plain mapping (dict, Mapping, JsonValue …), a plain tuple, a mapping whose values are object / Any, a fixed-length tuple tuple[A, B], or a container holding those, and defn / defk / deff do not answer with a tuple literal #(a b) of length 2 or more. :post is an isinstance contract, so only mappings are red, and for a defk / deff whose name has no type annotation, a plain tuple in :post is red too",
            ProjectRule::RecordStubNotKwOnly => "The type declarations (.pyi) of architecture.hy's :record-stubs do not misrepresent the runtime shape of the .hy of the same name in the same dir — if a record that accepts fields only by name in .hy (a defrecord, or a defclass with (dataclass … :kw-only True …) as decoration) is declared with @dataclass, write kw_only=True",
            ProjectRule::ServiceUntestedOnSim => "A business service is put on the fake environment with its production assembly (entry layer) as is, and is verified by running with only handler swaps — if not a single deftest under the fake environment (:verification-environment) reaches a definition in that service's entry layer, it is red as an unverified service",
            ProjectRule::ServiceInvariantsMissing => "A defservice in architecture.hy (including services without an entry layer) declares :invariants (a list of business-invariant functions `module:function`) in architecture.hy's defservice — the named function exists and its :tags :role is judgment (a pure judgment that takes records and returns a list of violations). A service with no declaration, a nonexistent function, or a function that is not a judgment is red",
            ProjectRule::ServiceSystemMissing => "A defservice in architecture.hy (including services without an entry layer) declares :system (the system placed in the cluster, `module:defsystem name`; a list if there are several systems) in architecture.hy's defservice — the named module is in that service's entry layer, the name is defined by defsystem, and the argument is exactly one typed base (if the defsystem's argument has a type annotation `[#^ T foundation]`, that; otherwise the type of the job function's argument that receives the base is written, and it is not a plain Callable, mapping, or tuple). If a defsystem exists, it is automatically included in the generic fake-cluster tests. If that service's code runs inside another service's system, write {:part-of \"module:defsystem name\"} (the system pointed to is in some service's entry layer); for a service left on the old path and a component service with no process (no main or deploy, only read by other services), write {:exempt \"reason\"} with a reason (the reason's content is not judged; an empty reason is red)",
            ProjectRule::SystemAccessUnwritten => "In a repo whose architecture.hy has :outside-writers (a table of pairs of external storage written by writers that are not production-system jobs), a job of a production system (the defsystem named by defservice's :system) writes :reads and :writes (sets of strings of the pairs of external storage it reads and writes, `\"<storage>:<name>\"`; #{} if unused), and each pair in a job's :reads is in some production job's :writes, in the :writes of the contract of a defk / deff reachable from a production entry point (decided by the :business-fakes declaration — the same as DOEFF143) (writers that are not jobs — such as Programs of tasks sent from a system's job), or in the :outside-writers table — this closes the hole where a record with no production writer is read while a stand-in produces it in the local fake and everything goes green (doeff-linter finds violations of this kind). Not lowered by the registry",
            ProjectRule::RecordChangeWakesJob => "Production code in a repo whose architecture.hy has :business-fakes (Hy files matching :production and not matching :tests, :simulation, or :skip; excluding inside the doeff-records package) does not wake a job by waiting for record changes — it does not call doeff-records' signal-source factories (read-signal-handler, records-signal-handler), and outside the handlers that answer record effects it does not emit WatchChanges or WatchEvents with seconds (:timeout or the third positional) that are a literal nonzero value. Reading once without waiting (seconds 0) and writes to records are not hits (it forbids designs that write records and wait for records, and separates records from starting). Not lowered by the registry",
            ProjectRule::PollingOnTimer => "A Program in production code in a repo whose architecture.hy has :business-fakes (Hy files matching :production and not matching :tests, :simulation, or :skip — including inside the doeff-records package) does not repeatedly fetch the same thing with time waits in between — inside a repetition (the body of while or loop, an event-loop clause, a definition that calls itself) it does not place Delay, WaitTicks, a sleep with a module name (excluding yields of a literal 0 seconds and deadline waits of (- deadline now)), ArmTimer / ArmedTimer whose time is (+ now …) (now = a name bound in the same definition from GetTime, now-ms, or clock-ms), calls to helper definitions that wrap them, and places where a handler clause always emits an effect that waits (collected by re-reading the repo — it does not look inside a Program passed to another task). It also hits :poll-seconds, which passes a re-query interval to a library. Deadline waits whose wake time is derived from a row or schedule, a deadline applied once and exited, the upper-bound seconds when waiting for a change (the DOEFF208 side), and request timeouts are not hits. Only where the other party has no means of announcing changes, write `; 時間で取り直す理由: <word>` (the Japanese marker 'reason to re-fetch by time') on the hit's line or the line before to let it pass — the word is a closed set (`相手に変更の知らせが無い: <party> — <how it was checked>` [no change notification from the other party; neither the party nor the basis of the check is empty, and the party is not one known to have change notifications — Kubernetes, the coordinator's GET /watch, the doeff-events foundation, the record service's change signal] / `書くだけ` [write-only: a liveness mark, a report, a deadline extension] / `届かない間だけの繋ぎ直し` [reconnecting only while unreachable — in the end only hits of category retry]) (every fetching repetition is to be fixed and this is limited to a last resort). Not lowered by the registry",
            ProjectRule::TestSleepsOnWallClock => "Hy files in the test locations of the settings' `[tool.doeff-linter.test_sleep]` (files with a dir named in parts as a path segment, and files whose name matches the names glob — excluding under exclude dirs) do not call time.sleep or asyncio.sleep (including t.sleep with the import alias `(import time :as t)`, names bound by `(import asyncio [sleep])`, the method spelling `(.sleep time seconds)`, calls inside (Await (asyncio.sleep seconds)), and the equivalent `(.wait (threading.Event) seconds)`) — a test has the program emit doeff-time effects (Delay and so on) and runs on a virtual clock (sim-time-handler), or waits by naming the event to wait for. Yields of a literal 0 seconds are not counted. Waiting on the wall clock is allowed only where the test declares that real time itself (a real thread, a real asyncio loop, a real external process) is the subject: all of a file that has the module-level `(val pytestmark pytest.mark.realtime)` (also as a list or tuple of marks), inside a deftest whose meta map's :marks has \"realtime\", and inside a plain-function test `(defn [pytest.mark.realtime] name [] …)` that has the mark in its decorator list. A test that declares it is also listed with its reason in the roster in repos that keep a roster (doeff's ADR-DOE-TIME-001 R3; waiting is written with doeff's time effects)",
            ProjectRule::ProgramSleepsOnWallClock => "Hy programs (bodies of defk and defp — including inside fn, defn, and deff that are textually inside) in the locations of the settings' `[tool.doeff-linter.program_sleep]` do not call time.sleep or asyncio.sleep (including t.sleep with the import alias `(import time :as t)`, names bound by `(import asyncio [sleep])`, and calls inside (Await (asyncio.sleep seconds))) — time is waited for with doeff-time effects (Delay, WaitUntil, WaitWithin …), and what one second is is decided by the time handler the caller places. Yields of a literal 0 seconds are not counted. Waiting on the wall clock is allowed only in handler implementations: the body of a defhandler, definitions whose argument list is [effect k], and a defk or defp that itself declares in the contract dictionary's :tags the :role foundation (a generic I/O role — a helper called only from handlers). A top-level deff or defn (plain callable) is not a program and is not counted (doeff's ADR-DOE-TIME-001 R1, R2; waiting is written with doeff's time effects)",
            ProjectRule::HandlerArgumentHoldsState => "A handler does not receive connection objects or rewritable stores as arguments — read connection targets, credentials, and settings with Ask, create the client only once with (session val client …) at the head of the body, and hold state with (session var …) (so an outer handler can swap and observe them). For arguments that remain, write the reason in the body with architecture.hy's :handler-arguments :keep-mark comment",
            ProjectRule::BusinessEffectFake => "Do not make fakes that answer business effects — fakes are only for effects that touch the outside world. Write business operations with defks that emit effects of lower layers, and tests swap only the handlers of the outside world. Fake roots, production entry points, and business modules are declared in architecture.hy's :business-fakes",
            ProjectRule::StaleCounterexampleRow => "A row of the counterexample table is kept only while the deliberately broken counterexample handler still exists — when the broken handler is fixed or removed, the row is removed too",
            ProjectRule::AssemblyShapeBroken => "Assembly is a single point — a function in the assembly layer (argument = the base value) merely lists one base handler list and the constants of translation lists of each service's translation layer. The only difference between production and fakes is the base value passed, and no set file remains. A translation list holds only its own service's translations, and outside a list that re-emits another service's effect, a list that answers it is placed. Names are declared in architecture.hy's :assembly-shape",
            ProjectRule::AssemblyAnswerMisplaced => "Answers to business effects go in the translation list's handlers — a translation handler re-emits only generic effects that know nothing of the business, effects of the same list (the answering handler is outer), or public effects of other services, and a base handler answers only effects of the outside world",
            ProjectRule::TestOnlyFake => "Do not make test-only fakes — there is exactly one production business handler, and tests are assembled by swapping base handlers (record effects, clocks, external parties). Deliberately broken counterexample handlers are listed with reasons in the counterexample table (:counterexamples)",
            ProjectRule::IntentAnswererNotTranslation => "Only one translation-layer handler answers an intent effect (the same for production and fakes) — do not answer with a separate answerer per environment or with a base handler; fakes swap the base (ADR R9)",
            ProjectRule::ServiceWithoutCounterexample => "Each business service has one or more counterexamples of deliberately broken handlers — if, among the deftests reaching a clause of the counterexample table (:counterexamples), not one reaches a definition in that service's entry layer (the same graph as DOEFF136, in reverse), and the clause's effect is not defined by that service or the base, it is red as a service without a counterexample",
            ProjectRule::ClauseWithoutCounterexample => "Each clause of a business invariant has one or more counterexamples of deliberately broken handlers, or declares why it has none — for each clause of defservice's :clauses, if among the rows of the counterexample table (:counterexamples) that name that clause with `breaks: <service>::<clause>`, no deftest reaching that clause also reaches a definition in the service's entry layer, and there is no :clause-exemptions reason, it is red as a gap in coverage (DOEFF164, which is green with one per service, made finer per clause)",
            ProjectRule::RegistryEntryStale => "The registry of known breaches only shrinks — a listed key points to a finding that still matches. In a whole run, a key whose rule (named by the key's separator, a law name or a rule ID) was judged but that matches no finding is a stale row that was forgotten to be deleted",
            ProjectRule::IntentFakedInVerification => "An intent-layer effect is answered by the production translation handler (layer protocol) in the verification environment too — if a verification-environment handler answers it itself or a table row lets it through, the production translation never runs locally and everything goes green (doeff-linter finds violations of this kind). The only exclusions are clauses where every ending passes the received effect through as is, and deliberately broken clauses listed in the counterexample table (a clause that answers itself on only one branch needs a table row)",
            ProjectRule::IntentEffectUncovered => "An intent-layer effect is emitted by a definition reachable from a local test and has an answerer reachable from both a fake root and the production entry — an effect missing any of the three is a business operation that cannot be said to be tested (#1155)",
            ProjectRule::TestKindMismatch => "There are only two kinds of tests — local (no handler touching the outside world is reachable from the definitions it reaches) / edge (reaches an allowlist definition, a :wraps handler, or raw I/O). The kind is not decided by people but derived from what is reached, and only edge tests carry architecture.hy's :edge-mark marker (until a handler with real I/O is applied, everything is local)",
            ProjectRule::WorldHandlerWithoutContractTest => "Each pair of a handler in architecture.hy's :world-handlers and a :stand-ins pair (counterpart, real one) has one or more edge tests — for a pair, only tests that reach both the counterpart and the real one count; among deftests with a non-empty :interpreters, those that reach that handler's definition by following definition edges (calls, references, nesting — the same graph as DOEFF133). A handler with a reasoned :contract-test (none …) is not judged; a reasonless :contract-test none fires",
            ProjectRule::WorldHandlerMisplaced => "A definition listed in architecture.hy's :world-handlers exists and is in a module of the foundation layer (the only place for definitions that may touch the outside world is foundation)",
            ProjectRule::WorldHandlerNamedOutsideList => "Only definitions in the allowlist (and definitions nested inside them) may name the doeff real I/O handlers (os-file-handler, http-production-handler …) listed in architecture.hy's :world-handlers :wraps — both places where they are passed as values (with-handlers lists) and calls are counted",
            ProjectRule::TranslationEmitsIntent => "Handlers in the translation layer (the settings' handler_layers) — defhandler and functions taking [effect k] — emit only doeff's generic effects and do not emit business intents (types in the settings' intent_layers) — calls executed in the body ((<- …), (! …)) are followed through imported defks",
            ProjectRule::LowerLayerInDomain => "Reading and writing raw operations of a record service (lower-layer effects — architecture.hy's :business-fakes :lower-layer-modules) is the job of handlers (translation layer, base layer) — definitions in business layers (the settings' translation_effects.domain_layers, default core and intent) do not emit those effects (including shapes reached by following defk calls), do not wrap handlers of that module's package with with_handlers, and emit only domain effects",
            ProjectRule::EnvironmentBranch => "Hy definitions in business layers (architecture.hy's :environment-branches :layers) do not compare with values of environment names (:values — production, emulated …) (string literals in the arguments of =, !=, is, is-not, in, not-in, and the types of match clauses) and do not branch on dry-run flags (:flags) (symbols in the conditions of if, when, unless, cond and the subject of match) — differences between environments are expressed only by swapping the set of handlers (business layers do not know the environment)",
            ProjectRule::WireInWireFreeLayer => "Do not place defwire in a layer that passes values by type only (architecture.hy's layer :wire-free True) — wire types (declarations that have :names, the correspondence of field names) and parse / dump go in the translation layer (protocol), and this layer holds only types without field names (defrecord)",
            ProjectRule::BareMapInWireFreeLayer => "In a layer that passes values by type only (:wire-free True), do not write in the :pre / :post of defk, defn, and deff, the field types of defclass and defrecord, and the field annotations of Python classes, a mapping (dict, Dict, Mapping, MutableMapping, JsonValue, JsonBody, JsonObject, OpaqueJson, FrozenMap — including mappings with content types) or a tuple / list without a content type (tuple, list) — data is passed by type (defrecord), or by key → value functions and effects",
            ProjectRule::DictOutsideItsPlace => "Mappings are held only for caching, by the effect layer (defhandler) — do not build mappings ({…}, dfor, (dict …)) in the bodies of defk, deff, and defn outside handlers, and do not hold mappings in the fields of defclass, defrecord, and defwire or in the fields of Python classes (fields in layers that pass values by type only are DOEFF171). Definitions whose purpose is to build a mapping (:post is a mapping — the one point that spells it at a JSON boundary — or the one point that spells or reads an external shape, claiming :spells / :reads in :tags with a shape name [json, http, env, schema, records] — outside core only) and fields of the relay type OpaqueJson, which does not read the inside, are not counted (a smell; counting only)",
            ProjectRule::MatchFieldHyphen => "The keyword field names in a match clause's class pattern (`(Class :field type)`) are written in Python's attribute-name spelling (`_`, not `-`) — Hy does not mangle the keywords of class patterns, so `:a-b` becomes `case Class(a-b=…)` and matches no value",
            ProjectRule::DefkCalledBare => "A defk definition is called only where it is passed as a Program (the right side of (<- …), (! …), (return …), and arguments of calls that take a Program) — calling it bare returns a Program, not the answer",
            ProjectRule::SemanticMixedConcerns => "A definition whose role is judgment / program does not mix input-shape checks with business judgment (a Jev judgment — warning or info)",
            ProjectRule::SemanticClassRole => "A class with methods that hold logic, for which DOEFF119 emits nothing, is a value class (computing only from its fields) (a Jev judgment — warning or info if it is a window to the outside world or holds state)",
        }
    }

    /// The default one-line fix.
    pub fn hint(self) -> &'static str {
        match self {
            ProjectRule::UnreadableFile => "If Hy itself (hy.read_many) can read it, it is an error in doeff-linter's reader, so report it. If Hy cannot read it either, fix the parentheses or strings",
            ProjectRule::UnknownConfigKey => "If the linter is old, wait for the automatic rebuild from the main line (the development build location is replaced within minutes — do not build and swap by hand). If it is a typo, fix the key name",
            ProjectRule::LayerImportDirection => "Remove the import that goes against the direction — move the needed value to an allowed layer (such as intent types), or emit an effect and let a lower layer's handler answer it",
            ProjectRule::LayerForbiddenModule => "Put I/O in handlers of allowed layers (such as foundation), and emit effects from this layer",
            ProjectRule::LayerTypesOnly => "Move functions and handlers to another layer (core, protocol), and place only types in this layer",
            ProjectRule::ModuleDeclaresTags => "Write :tags {:context … :role …} in the contract dictionary, or place MODULE-TAGS at the head of the module",
            ProjectRule::RoleMatchesLayer => "Change the role to one allowed in this layer, or move the module to a layer dir that fits the role",
            ProjectRule::RawSideEffectDirect => "Emit an effect and have a handler in an allowed layer do the I/O",
            ProjectRule::RawSideEffectVia => "Check whether the definition at the end of the path is in a form that emits effects",
            ProjectRule::EnvironmentName => "Express environment differences by swapping base handlers, and remove environment words from business names",
            ProjectRule::ServiceBoundary => "To ask another service for something, emit that service's intent (do not read its judgment or translation modules directly). Move shared things to the shared location",
            ProjectRule::DefnForbidden => "Make it a defk. If a plain function is needed, such as a callback for an external library, make it a deff and write `; defk にできない: <reason>` (the marker means \"cannot be a defk\") and :tags on the same line",
            ProjectRule::DeffNeedsReason => "Write `; defk にできない: <reason>` (the marker means \"cannot be a defk\") on the deff's line or the line before it (if you cannot write one, make it a defk)",
            ProjectRule::DefinitionTagsRequired => "Write :tags {:context \"…\" :role \"…\"} in the contract dictionary",
            ProjectRule::ContextMatchesService => "Make :context match the service's name, or move the file to the dir of the :context service",
            ProjectRule::UndeclaredPlace => "Declare it in architecture.hy, or move it to a declared location (<root>/<service>/<layer>/)",
            ProjectRule::UndeclaredDirectory => "Add a defservice or the service's :layers to architecture.hy, or move the dir to a declared location",
            ProjectRule::ServiceDependency => "Add the dependency to :depends-on, and ask by emitting the dependency's intent (do not read its judgment or translation modules directly)",
            ProjectRule::BlindDefinitionReads => "Receive the material a judgment needs as the definition's arguments, and put the reading of words on the caller's side (outside the judgment) — escaping into a helper it reaches still hits through the transitive closure. Remove the module's imports and pass values as arguments",
            ProjectRule::DefinitionCallsUnlistedHead => "Move calls outside the list out of the definition (to the caller's side, or inside a boundary that catches exceptions) — if a call that cannot be moved truly raises no exception, write the reason in :why and add it to :heads",
            ProjectRule::CallOutsideDeclaredSites => "Return the call to the declared place (do not grow a second confinement or a seat of refusal) — if widening the places is an intended change of the boundary, fix architecture.hy's :sites and :count in the same change and write the reason in :why",
            ProjectRule::BroadCatchOutsideCarrier => "Narrow the catch (only exceptions known to occur, such as ValueError), or remove the catch and let it propagate — if it is a boundary that carries exceptions into a list, bind the caught exception by name and pass it to the :event event, add that definition to :carriers in the same change, and write the reason in :why",
            ProjectRule::PlacedDependency => "Move the module being read to a layer location (<root>/<service>/<layer>/), or move the needed type to intent and read it — remove the dependency on the pre-move location in the change that moves it",
            ProjectRule::UnusedDependency => "Remove the unused dependency from :depends-on",
            ProjectRule::TestIsDeftest => "Make it a deftest (make helpers that build test values defks and call them with `(<- …)` inside the deftest)",
            ProjectRule::ClassWithBehaviour => "Make a window to the outside world a base handler — hold resources (connections, clients, file handles) in (session val …) directly under the defhandler, and answer effects such as ListRows and PutRow (for fakes, a fake base handler). For state: values are defrecords (immutable), behaviour is pure functions that return new values, and state lives in one place, a handler's (session var …) such as a world, with changes flowing as effects. Even when rewriting is needed for speed, rewriting happens only inside the handler",
            ProjectRule::JsonValueOutsideWire => "Turn the JSON shape into a type with defwire, and pass the typed value parsed by the send/receive foundation module (do not write functions that take apart a JsonValue by hand and read it). If it is a foundation module that does the sending and receiving itself, list it in architecture.hy's :wire-modules",
            ProjectRule::SemanticBusinessDecision => "Move the business judgment to core's judgment, and have the translation handler only use its answer (if Jev is wrong, add it to the list of false judgments)",
            ProjectRule::SemanticTransportKnowledge => "Move the means of communication to protocol's translation handler, and have core only emit intents (if Jev is wrong, add it to the list of false judgments)",
            ProjectRule::SemanticPlainCallable => "If the kind does not fit, make it a defk — for assembly (a sequence of handlers) use `(defk handlers-of [foundation])`, for tests a deftest, and for helpers that build values a defk called with `(<- …)` (if Jev is wrong, add it to the list of false judgments)",
            ProjectRule::ShapeCheckInJudgment => "Parse the shape check into a defwire type at the protocol boundary (fail at the parsing step if the shape does not fit), and have this definition receive the typed value and only make the judgment",
            ProjectRule::FailureRethrow => "Raise failures with (<- (Raise failure-value)) (do not return them again to the caller by hand), and only where the failure is received and mapped, receive it on the caller's side with (on-raise body (Refusal r) mapped-value) (ADR-DOE-CORE-EFFECTS-003 R3, R7)",
            ProjectRule::BindThenReturn => "Merge into a single (return (! (f …))), or for a failure raise it with (<- (Raise …))",
            ProjectRule::FieldsJoinedIntoText => "Pass typed values as they are (do not flatten fields into strings) — turn them into text only at the one boundary that shows them to people",
            ProjectRule::RebuiltAccumulator => "Build accumulators at once with a comprehension (lfor) — do not stack rebuilds of (+ xs #(…)) inside a loop",
            ProjectRule::EffectsDisagreeWithInference => "Add the effects being raised to :effects, or remove the effects not being raised (inference does not subtract effects handled by a handler — if the body handles them, list it in the registry)",
            ProjectRule::StaleOutcomeBranch => "Remove the branch and keep only the success path — where you want to do something on absence use (<- x (E …) :absent <failure>) or absent-as, where you want to do something on failure use on-raise, and where absence or failure is carried around as a value wrap with (maybe (E …)) and receive Nothing (R14 (3))",
            ProjectRule::TestFormNotDeftest => "Rewrite it as a deftest (call the judging function in the same process) — if what it protected is gone, delete the whole file. For a per-module skip, fix what is missing, or skip only that test",
            ProjectRule::VocabularyOutsideSinglePoint => "Read the answer of the single point of judgment named by the group's :instead (the :except file) — list existing hits that cannot be fixed in the registry",
            ProjectRule::SpellingOutsideItsFiles => "Move the spelling to the :except file, and have other files go through that file's entry point — if increasing the files allowed to write it, write the reason in :why and add it to :except",
            ProjectRule::SpellingCountDiffers => "Make the number match :count (:at-least) — move added hits to the decided definition or file's entry point, and restore hits that disappeared. If changing the number, write the reason in :why and fix the declaration's number in the same change",
            ProjectRule::EffectOutsideCensus => "If adding an effect, add it to the :effects list in the same change (if removing, remove it from the list too) — merge a second declaration of the same name into one",
            ProjectRule::FieldHoldersDiffer => "Place that type's field only in the classes in the list (other classes hold it through a listed class) — if changing the holders, write the reason in :why and fix :holders in the same change",
            ProjectRule::RetiredWord => "Rewrite it with the group's :instead word. If it is a contract spelling (a wire field name or a contract value), check whether it exists in the group's :contract-files contract file (if it does, strings and defwire field definitions are not counted)",
            ProjectRule::RetiredCall => "Replace it with the group's :instead",
            ProjectRule::HandlerArgumentHoldsState => "Remove the argument, and move connection targets and settings to Ask, the client to (session val …), and state to (session var …) — for arguments that remain, such as lining up the same handler with different settings, write a reason comment in the body",
            ProjectRule::UntypedStructuredValue => "Define, return, and hold a frozen record with field names and types (defrecord, defwire, dataclass) — check :post with [(: % <record type>)]. Write the content types: an index looked up by key is dict[str, Row], and a sequence of the same type is tuple[X, ...]. If it is a boundary where an external system decides the value's shape, list it in the registry with the reason",
            ProjectRule::RecordStubNotKwOnly => "Make the decoration in the type declaration @dataclass(frozen=True, kw_only=True)",
            ProjectRule::BusinessEffectFake => "Rewrite the business operation as a defk that emits effects of a lower layer and remove the fake — for an effect of the outside world, add it to the outside-world table with a reason; for a deliberately broken counterexample, list it in the counterexample table",
            ProjectRule::StaleCounterexampleRow => "Remove the row of the counterexample table that no longer matches (for a dir with one key per file, that file)",
            ProjectRule::AssemblyShapeBroken => "Move the set file's assembly to a function in the assembly layer and delete the file; write the function as a defk whose body is only [base-list translation-list …]; list another service's translations as a separate list in the assembly layer",
            ProjectRule::AssemblyAnswerMisplaced => "Move the place that answers a business effect to a translation list's handler, or re-emit the answer as a generic effect and place the handler that answers the re-emitted effect before (outside) in the list",
            ProjectRule::TestOnlyFake => "Rewrite the test assembly as the production handler plus base swaps and remove the fake — for a deliberately broken counterexample, list it in the counterexample table",
            ProjectRule::IntentAnswererNotTranslation => "Gather the answer to an intent effect into one translation-layer handler, and remove it from base and per-environment handlers",
            ProjectRule::ServiceWithoutCounterexample => "Write in the fake environment a deftest of a counterexample that deliberately breaks a handler answering that service's effect (or a base effect), and list the handler in the counterexample table with a reason — for a service that cannot be written right now, list it in the registry with a reason and an owner",
            ProjectRule::ClauseWithoutCounterexample => "Write in the fake environment a deftest of a counterexample of a broken handler that breaks that clause, and add `breaks: <service>::<clause>` to the counterexample table's row — for a clause that a broken handler cannot break (a structural guarantee), write the reason in defservice's :clause-exemptions; for a clause that cannot be written right now, list it in the registry with a reason and an owner",
            ProjectRule::RegistryEntryStale => "Remove the registry row that no longer matches (for a dir with one key per file, that file; for a file with one key per row, that row)",
            ProjectRule::IntentFakedInVerification => "Remove the verification environment's own answer and the table row, put the production translation handler in the assembly, and swap only the base handlers below it (records, clocks, external parties) — for a deliberately broken counterexample, list the key in the counterexample table (it cannot be lowered by the registry)",
            ProjectRule::IntentEffectUncovered => "Fill the missing column — if it is not emitted from tests, run that business operation through a deftest in the fake environment; if there is no answerer, put the translation-layer handler in the assembly (if the effect is unused, remove the declaration)",
            ProjectRule::ServiceUntestedOnSim => "Add to the fake environment's tests a deftest that runs that service's entry assembly with only handler swaps",
            ProjectRule::ServiceInvariantsMissing => "Add :invariants [\"<module>:<function>\" …] to the defservice, and place the function as a defk with :role \"judgment\" (such as <service>_invariants.hy in the fake environment). List known gaps in the registry with a reason and an owner",
            ProjectRule::ServiceSystemMissing => "Add :system \"<module>:<defsystem name>\" to the defservice, and place the defsystem in that service's entry layer in the form (defsystem name [foundation] …). For the job function that receives the base, write the base's type (defrecord) in :pre. For a service left on the old path and a component service with no process, use :system {:exempt \"reason\"}",
            ProjectRule::SystemAccessUnwritten => "Write :reads #{…} :writes #{…} on the job's row. If the pair read has no writer, add a job that writes that pair to the production system, or, if the writer is not a production-system job (a tool in an external repo, a human operation, a task that is not a job), write the pair and the writer in architecture.hy's :outside-writers",
            ProjectRule::RecordChangeWakesJob => "Stop the design that wakes up by waiting for record changes: receive the waking signal from the event foundation (redis, rabbitmq, and so on) and only write the records. Make the reads used to catch up at startup or reconnection a single read that does not wait (seconds 0). Remove the signal source (read-signal-handler) from the handler list and replace it with a source that receives the event foundation's signals",
            ProjectRule::PollingOnTimer => "Stop fetching at time intervals, and wake on the other party's change notifications (events — doeff-events' Publish / WaitForEvent, the event foundation). For deadline waits whose wake time is decided by a row or schedule, derive that time from the row and apply it. Only where the other party has no means of announcing changes, write-only loops (liveness marks, reports, deadline extensions), and reconnections that only try whether it connects while an unreachable mark or failure answer exists and remove the mark on return, write `; 時間で取り直す理由: <word>` (the marker means \"reason to re-fetch by time\") on the hit's line or the line before",
            ProjectRule::TestSleepsOnWallClock => "Remove waits whose subject is not real time — make a program's wait `(<- (Delay seconds))` and run the test on sim-time-handler's virtual clock, and to wait for the other party's state, name the event to wait for (a threading.Event set, a queue get, a process wait). If real time itself is the subject (overlap of real threads, a real asyncio loop, deadline-bound waits on real external processes or sockets), declare it: for that test alone `(deftest name {:marks [\"realtime\"]} …)` (for a plain-function test `(defn [pytest.mark.realtime] name [] …)`), and for a helper or a whole file `(val pytestmark pytest.mark.realtime)` at the top level. A test that declares it is listed in the repo's realtime roster (doeff's tests/realtime_tests.toml) together with the reason \"what about real time is the subject\"",
            ProjectRule::ProgramSleepsOnWallClock => "Replace the wait with doeff-time effects — `(import doeff_time [Delay])` and `(<- (Delay seconds))` (WaitUntil to wait until a deadline, WaitWithin to wait with an upper bound). Place a time handler where programs run (sync-time-handler or async-time-handler in production, sim-time-handler in tests). For a helper called only from handlers that waits on the real time of a real counterpart (a child process, a terminal), declare it by writing `:tags {:context \"<context>\" :role \"foundation\"}` in that definition's contract dictionary, and write the reason in the docstring",
            ProjectRule::TestKindMismatch => "If it is an edge test, add the marker (it is then excluded from the default pytest); if it is meant to be local, replace the real I/O handler it reaches with a fake handler — if it is local but has the marker, remove it",
            ProjectRule::WorldHandlerWithoutContractTest => "Write a deftest that lists the real one (that handler) and the fake interpreter (for a :stand-ins pair, the real one and the counterpart) in :interpreters, and pass the same test through both — a :stand-ins pair in the roster can also be written with none in the same way; only for a handler that has a reason for having no edge test, write none in the roster row with the name of the test that carries the reason: if it merely wraps a doeff handler in one line and the contract test is on doeff's side, :contract-test (none :doeff-test \"packages/<pkg>/tests/<file>.hy::<test name>\") (a path from the doeff repo's root); if another test in this repo verifies the contract, :contract-test (none :repo-test \"<file>.hy::<test name>\") (a path from this repo's root). A reasonless :contract-test none fires",
            ProjectRule::WorldHandlerMisplaced => "Move the definition to the foundation layer (the dir of architecture.hy's :foundation), or make the roster's spelling (module:name) match the real thing — if the definition is no longer needed, remove it from the roster",
            ProjectRule::WorldHandlerNamedOutsideList => "Run the body under a roster definition (e.g. with-agora-process) and do not wrap real I/O handlers yourself — if a new place that touches the outside world is needed, place that definition in the foundation layer and list it in the roster",
            ProjectRule::TranslationEmitsIntent => "Place business flows that emit intents in a layer-core program, and have the translation handler merely re-emit the received intent as doeff's generic effects (HttpRequest, record reads and writes, clocks …) — if a defk it passes through emits the intent, use the generic effect directly instead of calling that defk",
            ProjectRule::LowerLayerInDomain => "In the business layer, place and emit domain effects (types of layer intent), and let a translation-layer handler rephrase those effects into the record service's raw operations — move the places that wrap handlers of raw effects (signal sources, record clients) to the handler list of the assembly layer (entry)",
            ProjectRule::EnvironmentBranch => "Make behaviour that varies by environment an effect, and let per-environment handlers (production, fake, dry-run) answer it — definitions in the business layer read neither environment names nor dry-run flags",
            ProjectRule::WireInWireFreeLayer => "Move the defwire declaration to the translation layer (protocol), place a defrecord without field names in this layer, and have protocol read it from the wire type and pass it on",
            ProjectRule::BareMapInWireFreeLayer => "Make a mapping a defrecord with field names and types, or make a mapping for looking up data (an index or table) a key → value function or effect (e.g. (ReadRow table key)) — put the spelling whose purpose is to build a mapping at one point in the translation layer (protocol)",
            ProjectRule::DictOutsideItsPlace => "Make a mapping for looking up data a key → value function or effect (e.g. (ReadRow table key)), and if a cache is needed, put it in the handler's session — make a field mapping a defrecord with field names and types. Gather the spelling that builds JSON into the one definition whose :post is a mapping",
            ProjectRule::MatchFieldHyphen => "Rewrite the `-` in the field name to `_` (`:ended-reason` → `:ended_reason`) — fixing it makes clauses that never matched start to match, so fire that definition's tests to check the change in behaviour",
            ProjectRule::DefkCalledBare => "Bind it with (<- x (f …)) or receive the answer with (! (f …)) — if it is inside a plain function, make that function a defk and make the caller a Program",
            ProjectRule::SemanticMixedConcerns => "Parse the shape check into a defwire type at the protocol boundary (fail at the parsing step if the shape does not fit), and have this definition receive the typed value and only make the judgment (if Jev is wrong, add it to the list of false judgments)",
            ProjectRule::SemanticClassRole => "For a window to the outside world, a base handler (resources in (session val …)); for state, one place, a handler's (session var …) (if Jev is wrong, add it to the list of false judgments)",
        }
    }
}

/// The rule's gravity (a policy that the repo declares with `[tool.doeff-linter.rules.<ID>] level`). An axis separate from severity,
/// and lowering severity through the registry does not lower gravity — so that the editor can count "how many untouched criticals remain".
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RuleLevel {
    Critical,
    Major,
    Minor,
    Info,
}

impl RuleLevel {
    /// Read the settings' spelling (None outside the closed set).
    pub fn parse(text: &str) -> Option<RuleLevel> {
        match text {
            "critical" => Some(RuleLevel::Critical),
            "major" => Some(RuleLevel::Major),
            "minor" => Some(RuleLevel::Minor),
            "info" => Some(RuleLevel::Info),
            _ => None,
        }
    }

    /// The gravity of a rule with no declaration — decided from the rule's own severity (before lowering by the registry). Only a declaration sets critical.
    pub fn default_for(base: crate::models::Severity) -> RuleLevel {
        match base {
            crate::models::Severity::Error => RuleLevel::Major,
            crate::models::Severity::Warning => RuleLevel::Minor,
            crate::models::Severity::Info => RuleLevel::Info,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// DOEFF ID → the family to assign (the request's table itself).
    const EXPECTED_FAMILIES: &[(&str, RuleFamily)] = &[
        ("DOEFF100", RuleFamily::Place),
        ("DOEFF128", RuleFamily::Place),
        ("DOEFF101", RuleFamily::Layer),
        ("DOEFF102", RuleFamily::Layer),
        ("DOEFF103", RuleFamily::Layer),
        ("DOEFF104", RuleFamily::Tags),
        ("DOEFF105", RuleFamily::Tags),
        ("DOEFF113", RuleFamily::Tags),
        ("DOEFF106", RuleFamily::Raw),
        ("DOEFF107", RuleFamily::Raw),
        ("DOEFF108", RuleFamily::Naming),
        ("DOEFF109", RuleFamily::Place),
        ("DOEFF114", RuleFamily::Place),
        ("DOEFF115", RuleFamily::Place),
        ("DOEFF116", RuleFamily::Place),
        ("DOEFF117", RuleFamily::Place),
        ("DOEFF110", RuleFamily::Definition),
        ("DOEFF111", RuleFamily::Definition),
        ("DOEFF112", RuleFamily::Definition),
        ("DOEFF118", RuleFamily::Definition),
        ("DOEFF119", RuleFamily::Class),
        ("DOEFF204", RuleFamily::Class),
        ("DOEFF120", RuleFamily::Wire),
        ("DOEFF121", RuleFamily::Smell),
        ("DOEFF122", RuleFamily::Smell),
        ("DOEFF123", RuleFamily::Smell),
        ("DOEFF124", RuleFamily::Smell),
        ("DOEFF125", RuleFamily::Smell),
        ("DOEFF205", RuleFamily::Smell),
        ("DOEFF126", RuleFamily::Definition),
        ("DOEFF127", RuleFamily::Definition),
        ("DOEFF175", RuleFamily::Definition),
        ("DOEFF130", RuleFamily::Layer),
        ("DOEFF174", RuleFamily::Layer),
        ("DOEFF168", RuleFamily::Layer),
        ("DOEFF169", RuleFamily::Layer),
        ("DOEFF170", RuleFamily::Layer),
        ("DOEFF171", RuleFamily::Layer),
        ("DOEFF172", RuleFamily::Smell),
        ("DOEFF131", RuleFamily::Raw),
        ("DOEFF132", RuleFamily::Raw),
        ("DOEFF133", RuleFamily::Raw),
        ("DOEFF137", RuleFamily::Raw),
        ("DOEFF135", RuleFamily::Definition),
        ("DOEFF136", RuleFamily::Definition),
        ("DOEFF163", RuleFamily::Definition),
        ("DOEFF173", RuleFamily::Definition),
        ("DOEFF207", RuleFamily::Definition),
        ("DOEFF208", RuleFamily::Definition),
        ("DOEFF209", RuleFamily::Definition),
        ("DOEFF210", RuleFamily::Definition),
        ("DOEFF211", RuleFamily::Definition),
        ("DOEFF140", RuleFamily::Place),
        ("DOEFF141", RuleFamily::Place),
        ("DOEFF147", RuleFamily::Definition),
        ("DOEFF159", RuleFamily::Definition),
        ("DOEFF160", RuleFamily::Definition),
        ("DOEFF144", RuleFamily::Definition),
        ("DOEFF145", RuleFamily::Definition),
        ("DOEFF150", RuleFamily::Naming),
        ("DOEFF151", RuleFamily::Naming),
        ("DOEFF142", RuleFamily::Definition),
        ("DOEFF143", RuleFamily::Definition),
        ("DOEFF176", RuleFamily::Definition),
        ("DOEFF155", RuleFamily::Definition),
        ("DOEFF156", RuleFamily::Definition),
        ("DOEFF157", RuleFamily::Definition),
        ("DOEFF164", RuleFamily::Definition),
        ("DOEFF167", RuleFamily::Definition),
        ("DOEFF158", RuleFamily::Definition),
        ("DOEFF165", RuleFamily::Definition),
        ("DOEFF206", RuleFamily::Definition),
        ("DOEFF166", RuleFamily::Definition),
        ("DOEFF146", RuleFamily::Naming),
        ("DOEFF148", RuleFamily::Naming),
        ("DOEFF161", RuleFamily::Naming),
        ("DOEFF162", RuleFamily::Naming),
        ("DOEFF149", RuleFamily::Naming),
        ("DOEFF201", RuleFamily::Jev),
        ("DOEFF202", RuleFamily::Jev),
        ("DOEFF203", RuleFamily::Jev),
    ];

    #[test]
    fn every_rule_has_a_non_empty_label() {
        for rule in ProjectRule::ALL {
            assert!(!rule.label().is_empty(), "{} has an empty label", rule.id());
        }
    }

    #[test]
    fn jev_rules_are_not_critical_until_calibrated() {
        // Decision B (#1762 / #1801): Jev's 201, 202, and 205 are major. Counterexample = the responsibility-boundary rules (DOEFF101, 130) remain critical as before.
        use super::RuleLevel;
        for id in ["DOEFF201", "DOEFF202", "DOEFF205"] {
            let rule = ProjectRule::parse(id).unwrap_or_else(|| panic!("{} is not in ProjectRule", id));
            assert_eq!(rule.default_level(), Some(RuleLevel::Major), "default gravity of {}", id);
        }
        for id in ["DOEFF101", "DOEFF130"] {
            let rule = ProjectRule::parse(id).unwrap_or_else(|| panic!("{} is not in ProjectRule", id));
            assert_eq!(rule.default_level(), Some(RuleLevel::Critical), "default gravity of {}", id);
        }
        for id in ["DOEFF203", "DOEFF204"] {
            let rule = ProjectRule::parse(id).unwrap_or_else(|| panic!("{} is not in ProjectRule", id));
            assert_eq!(rule.default_level(), None, "{} remains a rule with no declaration", id);
        }
    }

    #[test]
    fn family_matches_the_assignment_table_for_all_33_rules() {
        assert_eq!(EXPECTED_FAMILIES.len(), ProjectRule::ALL.len(), "the assignment table disagrees with the number of ALL");
        for &(id, expected) in EXPECTED_FAMILIES {
            let rule = ProjectRule::parse(id).unwrap_or_else(|| panic!("{} is not in ProjectRule", id));
            assert_eq!(rule.family(), expected, "family of {} is wrong", id);
        }
    }
}
