//! The top-level `architecture.hy` of the repo — the single declaration of services and layers, read without executing, using the Hy reader (doeff-indexer).
//!
//! Shape (details are in the "architecture.hy" section of docs/SPECIFICATION.md):
//! ```hy
//! (defarchitecture agora-controllers
//!   :root "controllers"
//!   :layers [(layer core :summary "…" :knows "…" :does-not-know "…" :question "…" :roles [judgment program type]
//!                        :imports [core intent] :forbid-modules ["httpx"])
//!            (layer intent … :types-only true) (layer protocol …) (layer foundation …)
//!            (layer entry … :dependency-layers [intent protocol])]   ; which layers of a dependency target may be read (default = :open-layers)
//!   :shared "shared"                 ; a location every service can read (root/shared/<layer>/)
//!   :foundation "foundation"         ; a layer outside services (root/foundation/) — :layers needs a layer with the same name
//!   :verification-environment "agora_sim"  ; location of the emulated environment (one root/<dir>/) — a location that is not a service and is not subject to DOEFF114/115. Other rules still apply
//!   :root-modules ["macros"]         ; names (file stems) of public modules allowed directly under root — not DOEFF114. A file with no name is DOEFF114 as before
//!   :open-layers [intent]            ; layers that may be read from another service (corresponds to Tach's interfaces)
//!   :roles {:judgment "a pure function that makes a business judgment" …}
//!   :wire-modules ["controllers.foundation.record_client"]  ; foundation modules that do the JSON sending and receiving themselves (DOEFF120 allows JsonValue)
//!   :world-handlers [(world-handler "controllers.foundation.host:with-agora-process"  ; allowlist of definitions allowed to touch the outside world
//!                       :touches [http file clock env]           ; what they touch (a closed vocabulary — WorldTouch)
//!                       :answers [HttpRequest ReadText]          ; effects they answer (optional)
//!                       :wraps ["doeff_core_effects:os-file-handler"])]  ; doeff real-I/O handlers run inside (optional)
//!   :stand-ins [(stand-in "controllers.agora_sim.peers.live_session_peer:with-live-session-peer"  ; a counterpart that answers in place of the real one in emulation
//!                 :real "doeff_openai.live:with-live-session")]  ; the real handler the counterpart stands in for (DOEFF137 counts the contract tests per pair)
//!   :exclude ["tests" "__pycache__" "conftest.py"]
//!   :shared "shared")
//! (defservice land-notice "landing notice" {:depends-on [messaging] :layers [core intent protocol entry]})
//! ```
//! Shapes that cannot be read (a duplicate service, a nonexistent layer name, a retired key) are returned as a list of reasons with positions in the file (a settings error).
//! A key this binary does not know is not made an error; only that key is left unread and reported as a notice (`Architecture::notices` -> DOEFF100)
//! (declarations may get ahead of the binary).

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::{Delim, Form, Node, ReadIssue, Reader, StrKind};
use doeff_indexer::hy_index::LineIndex;
use serde::Serialize;

use super::names::hy_mangle;
use super::rule::ProjectRule;
use super::semantic::{LineExample, SemanticLine, SemanticQuestion};
use super::layers::{normalize_dir, LayerDescription, LayersSection, PathPatterns, RolesSection};

/// The declaration of one layer.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ArchLayer {
    pub name: String,
    pub summary: Option<String>,
    pub knows: Option<String>,
    pub does_not_know: Option<String>,
    pub question: Option<String>,
    pub roles: Vec<String>,
    /// Layers that may be imported (None = unrestricted).
    #[serde(skip)]
    pub imports: Option<Vec<String>>,
    #[serde(skip)]
    pub forbid_modules: Vec<String>,
    #[serde(skip)]
    pub types_only: bool,
    /// A layer that passes values by type alone (`:wire-free True` — no defwire, mappings, or untyped tuples are placed there; DOEFF170, 171).
    #[serde(skip)]
    pub wire_free: bool,
    /// Which layers of the dependency-target services this layer's modules may read, as declared in :depends-on (None = :open-layers).
    /// Example: the assembly layer entry reads the dependency targets' intent and protocol (translation handlers) to assemble the whole.
    #[serde(skip)]
    pub dependency_layers: Option<Vec<String>>,
    /// Names modules belonging to this layer by spelling (`:modules [NAME …]` — dotted module names; modules below them are included too). So that packages whose layer
    /// cannot be decided by the location dir (modules entered at startup, and the like) get their rule population decided from the layer declaration.
    #[serde(skip)]
    pub modules: Vec<String>,
    /// Names, one by one, the files belonging to this layer by path relative to the dir where the declaration is (`:files ["relative path" …]`). Files with no module name
    /// (design records sealed with a hash, outside a package, and the like) cannot be named by :modules — the name becomes a bare stem, and writing it in a declaration of an upper dir would
    /// include every same-named file below it. A named file that does not exist, `..`, or a dir is a reading error.
    #[serde(skip)]
    pub files: Vec<String>,
    /// Exempts only the named modules and files from that rule's population (`:exempt [(rule RULE-ID "reason") …]`). A reason is required, and it cannot be placed on a layer that has neither :modules nor :files
    /// (a reading error). The Python rules' application reads this field of the nearest architecture.hy above the file (crate::population).
    #[serde(skip)]
    pub exempt: Vec<RuleExemption>,
}

/// One declaration by which a layer exempts named modules from a rule's population (`(rule RULE-ID "reason")`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuleExemption {
    pub rule: String,
    pub reason: String,
}

/// The declaration of one service.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ArchService {
    pub name: String,
    /// The name of the dir under root (the name with `-` replaced by `_`).
    pub dir: String,
    pub description: Option<String>,
    pub depends_on: Vec<String>,
    pub layers: Vec<String>,
    /// The shape of the public contract (`:public-contract`). Omitted = in-process (another service can list it in `:depends-on` and read it).
    pub public_contract: PublicContract,
    /// Functions for business invariants (`:invariants` — a list of `module:function`; omitted = None). DOEFF163 requires a service that has code to have a declaration, existence, and
    /// :role judgment (definition 1 of #1155).
    pub invariants: Option<Vec<DefinitionRef>>,
    /// Names of the clauses of the invariants (`:clauses` — the list of names of the violated clauses returned by the :invariants functions; omitted = None). For each clause, DOEFF167 requires a counterexample of
    /// a broken handler that claims that clause, or a reason for exemption. Clause names cannot be read statically from the functions' return values, so they are declared here.
    pub clauses: Option<Vec<String>>,
    /// Clauses with no counterexample and the reasons (`:clause-exemptions {"clause" "reason" …}` — for example structural guarantees that a broken handler cannot violate). The clauses are within :clauses.
    pub clause_exemptions: Vec<ClauseExemption>,
    /// The entry module of the service's code (`:entry-modules ["pkg.module" …]` — omitted = None). Lets a repo with no layer dir (`<root>/<dir>/entry/`)
    /// (a repo divided by feature dirs, like merge-queue) have DOEFF163's "service that has code" judged by where these modules are.
    ///A service that omits it is judged, as before, by the entry layer's dir.
    pub entry_modules: Option<Vec<String>>,
    /// The declaration of the system placed in the cluster (`:system "module:name of defsystem"`; a list if there are several systems; the reason for not placing one is `{:exempt "reason"}` — omitted = None).
    /// DOEFF173 requires a service that has code to have the declaration, the existence of a defsystem in the entry layer, and exactly one base with typed arguments.
    pub system: Option<SystemDecl>,
    /// The position of the defservice in architecture.hy (the position of the DOEFF117 notice).
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
    /// The declaration file the defservice is written in (`range` is a position in it — the declaration can be split into several files).
    #[serde(skip)]
    pub file: PathBuf,
}

/// The `:system` declaration of defservice (a closed set).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum SystemDecl {
    /// The system's defsystem (one `"module:name"` or a list).
    Systems(Vec<DefinitionRef>),
    /// The service's code runs inside another service's system (`{:part-of "module:name"}` — the system pointed to is a defsystem in some service's entry layer).
    PartOf(DefinitionRef),
    /// The reason for not placing a system (`{:exempt "reason"}` — a service left on the old path, or a component service with no process. The content of the reason is not judged; an empty reason is turned red by DOEFF173).
    Exempt(String),
}

/// One clause with no counterexample, and the reason for having none (one pair of `defservice`'s `:clause-exemptions`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ClauseExemption {
    pub clause: String,
    pub reason: String,
}

/// The shape of a service's public contract (`defservice`'s `:public-contract`).
/// For an `Http` service, the public contract is only the HTTP surface, and other services do not list it in `:depends-on` and read it in-process
/// (this stops the shortcut for a service that holds the state of a store).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum PublicContract {
    InProcess,
    Http,
}

/// One row of the outside-writers table (`:outside-writers` `"<store>:<name>" "writer"` — the spelling of the pair, and the description of the writer).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OutsideWriter {
    pub access: String,
    pub writer: String,
}

/// One kind of reason for allowing a plain function (deff) (`(reason NAME "description")`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ReasonKind {
    pub name: String,
    pub description: String,
    /// How to fix the type of a reason that is not accepted (`:fix "…"` — absent for accepted reasons).
    pub fix: Option<String>,
}

/// Kinds of what touches the outside world (the closed vocabulary of `:touches` in `:world-handlers`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum WorldTouch {
    Http,
    Db,
    File,
    Process,
    Clock,
    Env,
    Cluster,
    Network,
    Thread,
}

impl WorldTouch {
    pub const ALL: [WorldTouch; 9] = [
        WorldTouch::Http,
        WorldTouch::Db,
        WorldTouch::File,
        WorldTouch::Process,
        WorldTouch::Clock,
        WorldTouch::Env,
        WorldTouch::Cluster,
        WorldTouch::Network,
        WorldTouch::Thread,
    ];

    pub fn name(self) -> &'static str {
        match self {
            WorldTouch::Http => "http",
            WorldTouch::Db => "db",
            WorldTouch::File => "file",
            WorldTouch::Process => "process",
            WorldTouch::Clock => "clock",
            WorldTouch::Env => "env",
            WorldTouch::Cluster => "cluster",
            WorldTouch::Network => "network",
            WorldTouch::Thread => "thread",
        }
    }

    pub fn parse(text: &str) -> Option<WorldTouch> {
        WorldTouch::ALL.into_iter().find(|t| t.name() == text)
    }
}

/// Naming of one definition (`"module.path:name"` — the module is `.`-separated and the name keeps the Hy spelling).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct DefinitionRef {
    pub module: String,
    pub name: String,
}

impl DefinitionRef {
    /// Reads `"a.b.c:name"` (an empty module segment, an empty name, or a spelling containing whitespace is None).
    pub fn parse(text: &str) -> Option<DefinitionRef> {
        let (module, name) = text.split_once(':')?;
        let module_ok = !module.is_empty() && module.split('.').all(|segment| !segment.is_empty() && !segment.contains(char::is_whitespace) && !segment.contains('*'));
        let name_ok = !name.is_empty() && !name.contains(char::is_whitespace) && !name.contains(':');
        (module_ok && name_ok).then(|| DefinitionRef { module: module.to_string(), name: name.to_string() })
    }

    pub fn spelling(&self) -> String {
        format!("{}:{}", self.module, self.name)
    }

    /// The dotted spelling with the module spelling mangled (the same shape as the module of an index file and the module part of a fully qualified name).
    pub fn mangled_module(&self) -> String {
        mangle_dotted(&self.module)
    }

    /// The fully qualified name (the same spelling as the index definition's `qualified_name` and the `target` of calls and references).
    pub fn target(&self) -> String {
        format!("{}.{}", self.mangled_module(), doeff_indexer::hy_index::mangle(&self.name))
    }
}

/// Mangles a dotted spelling segment by segment.
pub fn mangle_dotted(dotted: &str) -> String {
    dotted.split('.').filter(|part| !part.is_empty()).map(doeff_indexer::hy_index::mangle).collect::<Vec<_>>().join(".")
}

/// Naming of one test (pytest's node id shape `"path/to/test_x.hy::test-name"` — the file is a `.hy` or `.py` relative to the repo root).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TestNodeId {
    pub file: String,
    pub name: String,
}

impl TestNodeId {
    /// Reads `"file::name"` (an empty file, an absolute path, a file other than `.hy` / `.py`, an empty name, or a spelling containing whitespace or `::` is None).
    pub fn parse(text: &str) -> Option<TestNodeId> {
        let (file, name) = text.split_once("::")?;
        let file_ok = !file.is_empty()
            && !file.starts_with('/')
            && !file.contains(char::is_whitespace)
            && (file.ends_with(".hy") || file.ends_with(".py"))
            && file.rsplit('/').next().is_some_and(|base| base.len() > 3);
        let name_ok = !name.is_empty() && !name.contains(char::is_whitespace) && !name.contains("::");
        (file_ok && name_ok).then(|| TestNodeId { file: file.to_string(), name: name.to_string() })
    }

    pub fn spelling(&self) -> String {
        format!("{}::{}", self.file, self.name)
    }
}

/// Reasons for not requiring an edge test (`:contract-test (none :<reason key> "file::name")` — DOEFF137).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub enum ContractTestWaiver {
    /// The handler only wraps a doeff handler, and the contract test is on the doeff side (`:doeff-test` — a node id inside the doeff repo).
    DoeffContractTest(TestNodeId),
    /// This repo's other test checks the contract (`:repo-test` — for example at the surface that ends a process, where there is an edge test of the handler inside it).
    CoveredByRepoTest(TestNodeId),
}

impl ContractTestWaiver {
    /// The spelling of the reason key (the keyword written in architecture.hy).
    pub const KEYS: [&'static str; 2] = [":doeff-test", ":repo-test"];
}

/// The declaration of a handler's edge test (`:contract-test` — DOEFF137).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub enum ContractTest {
    /// Omitted — an edge test is required.
    Required,
    /// A none with a reason — an edge test is not required.
    Waived(ContractTestWaiver),
    /// A bare symbol none with no reason — DOEFF137 fires (not requiring one is not accepted until a reason is written).
    NoneWithoutReason,
}

/// One definition allowed to touch the outside world (`(world-handler "module:name" :touches [..] :answers [..] :wraps [..])` of `:world-handlers`).
/// Only under a definition in the allowlist do real-I/O answerers run (raw Python I/O, and the doeff real-I/O handlers listed in :wraps).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WorldHandler {
    pub definition: DefinitionRef,
    pub touches: Vec<WorldTouch>,
    /// Names of the effects it answers (may be omitted).
    pub answers: Vec<String>,
    /// The doeff real-I/O handlers it runs inside (may be omitted).
    pub wraps: Vec<DefinitionRef>,
    /// The declaration of the edge test (a deftest with `:interpreters`) (`:contract-test` — required if omitted; DOEFF137).
    pub contract_test: ContractTest,
    /// The position in architecture.hy.
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// One pair of a counterpart handler that answers in place of the real one in emulation, and the real handler it stands in for (`:stand-ins`'s
/// `(stand-in "module:name" :real "module:name" :contract-test …?)`). For each pair, DOEFF137 counts the edge tests that reach both the real one and the counterpart (a deftest with a
/// non-empty `:interpreters`) — the same test is run against both and the answers are compared (verified fake, the Pact shape). The real one may be outside the index (a doeff or other
/// package).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StandIn {
    /// The definition of the counterpart handler.
    pub definition: DefinitionRef,
    /// The definition of the real handler the counterpart stands in for.
    pub real: DefinitionRef,
    /// The declaration of the contract test (`:contract-test` — written and read the same way as :world-handlers; required if omitted).
    pub contract_test: ContractTest,
    /// The position in architecture.hy.
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// One component at the boundary where the emulated environment reaches the real outside world (`(boundary-part "module" :touches [..] :reason "…")` of `:boundary-parts`
/// — DOEFF106). Within the declared module, raw side effects of the kinds in :touches (a real socket client, a real clock, threads, and the like) are
/// not hits for DOEFF106. Raw side effects outside the kinds still hit as before. The evidence of raw side effects remains in the index, so tests that reach this component are counted by
/// DOEFF133 as edges (the :edge-mark marker is required). Unlike the allowlist (:world-handlers), it names not a handler that answers an effect but a component whose job is real I/O itself, such as a human-driven entry point or
/// the stand of an edge.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BoundaryPart {
    /// The component's module (dotted spelling — before mangling).
    pub module: String,
    pub touches: Vec<WorldTouch>,
    /// Why real I/O itself is its job (empty is not accepted).
    pub reason: String,
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// One definition judged only from fixed materials (`(blind "module:name" :forbid-words [..] :no-imports True :allow-requires [..]
/// :why "…")` of `:blind-definitions` — DOEFF141). Requires that the spellings in :forbid-words do not appear in the bodies (including nested ones) of the repo's Hy definitions reached
/// transitively from the definition by calls and references (comments excluded; partial match), and, with :no-imports, that the definition's module has neither imports nor requires
/// (only requires of the modules listed in :allow-requires are allowed, since they load macros).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BlindDefinition {
    pub definition: DefinitionRef,
    pub forbid_words: Vec<String>,
    pub no_imports: bool,
    pub allow_requires: Vec<String>,
    /// Why those words are not read (put in the notice text).
    pub why: String,
    /// The position in architecture.hy.
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// One definition with decided heads that may be called (`(allowed-heads "module:name" :heads [..] :why "…")` of `:allowed-heads` — DOEFF147,
/// #1413). Requires that the spelling of the head of every `( … )` inside the definition's form (including nested; strings and comments excluded) is in :heads.
/// A declaration to keep calls that can raise exceptions out of definitions that run outside the boundary that catches exceptions (the boundary itself, and the single point that builds a refusal).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AllowedHeads {
    pub definition: DefinitionRef,
    /// Spellings of the heads that may be called (symbols and keywords — `defk`, `:`, `<-`, `.get`, and so on, as written at the head of a form).
    pub heads: Vec<String>,
    /// Why only these heads (put in the notice text).
    pub why: String,
    /// The position in architecture.hy.
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// One place where a head may be called (`(site "module:name" :count N :parent "head" :branch "name")` of `:call-sites`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CallSiteSite {
    pub definition: DefinitionRef,
    /// The number of calls inside this definition (not counted if omitted).
    pub count: Option<usize>,
    /// The calls inside this definition are direct elements of a form whose head is this spelling, immediately outside (example: `(try-handler (serve config))` — the innermost).
    pub parent: Option<String>,
    /// The calls inside this definition are inside the branch, among the branches of cond, when, and if, whose condition form has this symbol.
    pub branch: Option<String>,
    /// The position in architecture.hy.
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// One spelling for which the places and number of times a head may be called are decided (`(call-site "head" :files [..] :except [..] :sites [(site …) …] :why "…")` of `:call-sites` —
/// DOEFF159). Every `(head …)` call inside the Hy files of :files (excluding :except) is inside a definition in :sites.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CallSite {
    pub head: String,
    /// Files to search (globs of paths from the repo root — the same shape as :retired-words).
    pub files: Vec<String>,
    pub except: Vec<String>,
    pub sites: Vec<CallSiteSite>,
    /// Why only here (put in the notice text).
    pub why: String,
    /// The position in architecture.hy.
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// One carrier boundary that is allowed to have a broad exception catch (`(carrier "module:name" :event "event")` of `:broad-catches`). A broad catch inside the definition is allowed only when it binds the caught
/// exception to a name and, inside that catch, passes that name as an argument of `(event …)`. The definition is a top-level form of a Hy file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BroadCatchCarrier {
    pub definition: DefinitionRef,
    /// The spelling of the head of the event that carries the exception.
    pub event: String,
    /// The position in architecture.hy.
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// One group of files that do not place broad exception catches (`(broad-catch "name" :files [..] :except [..] :carriers [(carrier …) …]
/// :why "…")` of `:broad-catches` — DOEFF160). Every broad catch in the Hy and Python files of :files (excluding :except) is inside :carriers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BroadCatch {
    /// The name of the group (a detail of the registry key).
    pub name: String,
    pub files: Vec<String>,
    pub except: Vec<String>,
    pub carriers: Vec<BroadCatchCarrier>,
    /// Why broad catches are not placed (put in the notice text).
    pub why: String,
    /// The position in architecture.hy.
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// System values and the entry points that run systems (`:systems`). A defsystem definition is a system value even if not written.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct Systems {
    /// Tools that build system values (the bases inside this call's arguments are only carried as system values, and do not run here).
    pub carriers: Vec<DefinitionRef>,
    /// Entry points that run system values (only tests that reach here run the bases inside system values).
    pub runners: Vec<DefinitionRef>,
}

/// Rules for the shape of tests (`:test-forms`) — a spelling's pattern is a path glob from the repo root (`**` is zero or more segments, and a pattern without `/` is a file name).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct TestForms {
    /// Test files (emits the Python `def test_*` and per-module skips inside them).
    pub tests: Vec<String>,
    /// Scripts for checks that run outside pytest (emitted just by existing).
    pub check_scripts: Vec<String>,
    /// Runners that run deftest themselves (emitted just by existing).
    pub runners: Vec<String>,
}

/// Where to search for spellings decided not to be used (`:in` — a closed set of 3).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum WordPlace {
    /// All lines of the file (including comments, strings, and documents — the default).
    Lines,
    /// Only definition names (the Hy `def…` forms, the left side of `setv`, `val`, and `var`, and Python def and class names).
    Names,
    /// Only file names (before the last `.` — dir names and contents are not looked at). So that files with retired names are not placed again.
    Paths,
}

/// One group of spellings decided not to be used (`(retired-words "name" :words [..] :patterns [..] :files [..] …)` of `:retired-words` — DOEFF150).
/// :files and :except are path globs from the repo root, anchored at the root (`**` is zero or more segments, `*` is any spelling within a segment, and
/// a pattern without `/` is only files directly under the root).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RetiredWords {
    /// The name of the group (a detail of the notice and of the registry key for :patterns hits).
    pub name: String,
    /// Spellings that stand alone as a word (where the characters before and after are not letters, `_`, or `-` — being part of another word is not counted). The registry key's detail is the word itself.
    pub words: Vec<String>,
    /// Regular expressions applied per line (that they are readable is checked at read time).
    pub patterns: Vec<String>,
    pub files: Vec<String>,
    pub except: Vec<String>,
    /// Lines containing this spelling are not counted (lines that state the rule itself — effective only for :in lines).
    pub rule_lines: Vec<String>,
    /// Spellings read from contract files (`:contract-files` — effective only for :in lines). Empty if omitted, and counted as before.
    pub contracts: ContractSpellings,
    pub place: WordPlace,
    /// The word to use instead, and how to fix it (put in the notice text).
    pub instead: String,
}

/// The `:contract-files` of a retired-words group (JSON at paths from the repo root) and the contract spellings collected from them.
/// What is collected = the key names of objects at any depth (wire field names), the string elements of arrays under the key `enum`, and the string values under the key `const` (contract values).
/// If a word of :words is in these spellings, the word is not counted in Hy and Python strings whose content is exactly that word, nor in the names of defwire body field definitions.
/// Read when architecture.hy is read (`Architecture::load`); a file that cannot be read, is not JSON, or has no spelling is a declaration error.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct ContractSpellings {
    /// Contract files in the order written (paths from the repo root).
    pub files: Vec<String>,
    /// The collected spellings (the union over all files).
    pub spellings: BTreeSet<String>,
}

impl ContractSpellings {
    /// The words of the group that are among the contract spellings (words not counted in strings or defwire field definitions).
    pub fn words_in<'w>(&self, words: &'w [String]) -> BTreeSet<&'w str> {
        words.iter().filter(|w| self.spellings.contains(w.as_str())).map(String::as_str).collect()
    }
}

/// Collects contract spellings from a JSON value — the key names of objects at any depth, the string elements of arrays under the key `enum`, and the string values under the key `const`
/// (the fixed values of JSON Schema). Other string values (comments, example contents, descriptions) are not collected.
pub fn collect_contract_spellings(value: &serde_json::Value, out: &mut BTreeSet<String>) {
    match value {
        serde_json::Value::Object(map) => {
            for (key, inner) in map {
                out.insert(key.clone());
                match (key.as_str(), inner) {
                    ("enum", serde_json::Value::Array(items)) => out.extend(items.iter().filter_map(|i| i.as_str()).map(str::to_string)),
                    ("const", serde_json::Value::String(text)) => {
                        out.insert(text.clone());
                    }
                    _ => {}
                }
                collect_contract_spellings(inner, out);
            }
        }
        serde_json::Value::Array(items) => items.iter().for_each(|item| collect_contract_spellings(item, out)),
        serde_json::Value::Null | serde_json::Value::Bool(_) | serde_json::Value::Number(_) | serde_json::Value::String(_) => {}
    }
}

/// Whether a declaration's regular expression (`:patterns`, `:pattern`) is readable. Checks **syntax only**: building even the NFA with `Regex::new` each time a declaration is read (195 times in one run) takes 50 to 130 milliseconds
/// and was nearly half of the hook's CPU for one written file. A declaration holds a spelling and the rule rebuilds it when used, so what was built here was thrown away.
/// Only when it is unreadable, ask `Regex::new` again, and make the notice text the same spelling as before.
fn check_regex(pattern: &str) -> Result<(), regex::Error> {
    match regex_syntax::Parser::new().parse(pattern) {
        Ok(_) => Ok(()),
        Err(_) => regex::Regex::new(pattern).map(|_| ()),
    }
}

/// One group of calls decided not to be used (`(retired-calls "name" :calls [..] :files [..] :except [..] :instead "…")` of `:retired-calls` — DOEFF151).
/// Counts only calls of the form `(name …)` in Hy files (comments, strings, and forms discarded with `#_` are not counted). Globs are the same as :retired-words.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RetiredCalls {
    pub name: String,
    /// The spelling of the head of the call (as written — `time.time` matches `(time.time …)`). The registry key's detail is the call's spelling.
    pub calls: Vec<String>,
    pub files: Vec<String>,
    pub except: Vec<String>,
    pub instead: String,
}

/// Environment-name values and dry-run flags that business layers must not branch on (`:environment-branches {:values [..] :flags [..] :layers [..]}` — DOEFF168).
/// The spellings and the layers they apply to exist only in the repo's declaration, and the linter holds none.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct EnvironmentBranches {
    /// Environment-name values (matched as string literals in arguments of comparison forms and as the pattern of a match clause — e.g. production, emulated).
    pub values: Vec<String>,
    /// Names of dry-run flags (symbols in branch conditions and match subjects — compared after mangling the last dot-separated segment).
    pub flags: Vec<String>,
    /// Names of the layers it applies to (layers declared in :layers — business layers).
    pub layers: Vec<String>,
}

/// Rules for handler arguments (`:handler-arguments {:files [..] :exclude [..] :store-names [..] :store-suffixes [..] :keep-mark "…" :value-types [..]}`)
/// — the population of DOEFF142, and the repo's words (store names, the comment marker for reasons to keep, and outside types treated as values). The defaults for clients, mutable containers, and value types are held by the linter
/// (only general Python names).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct HandlerArguments {
    /// Patterns of the spellings of files to judge (globs from the repo root).
    pub files: Vec<String>,
    /// Patterns of the spellings of files to exclude.
    pub exclude: Vec<String>,
    /// Names that read an argument with no type annotation as a store.
    pub store_names: Vec<String>,
    /// Suffixes of names that read an argument with no type annotation as a store.
    pub store_suffixes: Vec<String>,
    /// If a comment with this spelling is in the handler's body, that handler's arguments are not counted (the marker of a reason for keeping the argument).
    pub keep_mark: Option<String>,
    /// Names of types treated as values even if not in the repo's index (frozen types of outside packages).
    pub value_types: Vec<String>,
}

/// The population of a rule judged per file (`{:files [..] :except [..]}` — :typed-values of DOEFF144 and :record-stubs of DOEFF145).
/// Globs are anchored at the repo root, the same as :retired-words (`**` is zero or more segments, `*` is any spelling within a segment, and a pattern without `/` is a file directly under the root).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct FileSelection {
    pub files: Vec<String>,
    pub except: Vec<String>,
}

/// One group of vocabulary whose judgment is confined to one place (`(vocabulary-scope "name" :patterns [r"…"] :files [..] :except [..] :instead "…")` of
/// `:single-point-vocabulary` — DOEFF146).
/// :files and :except are path globs from the repo root (the same shape as :retired-words). Only the :except files may read this vocabulary
/// (that is the single point of the judgment) — if another file matched by :files reads this vocabulary, it becomes a second point of the judgment.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct VocabularyScope {
    /// The name of the group (a detail of the registry key).
    pub name: String,
    /// Regular expressions applied per line (that they are readable is checked at read time).
    pub patterns: Vec<String>,
    pub files: Vec<String>,
    /// The single point of the judgment (the files allowed to read this vocabulary — not empty).
    pub except: Vec<String>,
    /// How to fix it (put in the notice text — e.g. "read the answer of slice.hy").
    pub instead: String,
}

/// One group of spellings for which the files allowed to write them are decided (`(confined-spelling "name" :patterns [r"…"] :files [..] :except [..] :why "…")` of
/// `:confined-spellings` — DOEFF148).
/// :files and :except are path globs from the repo root. Only the :except files may write this spelling (if empty, it is written nowhere in :files).
/// Reads the text with comments removed and counts inside strings (DOEFF146 does not count strings — verbs at outward-facing surfaces and route spellings are in strings).
/// If `:names-bound-in [<path from the repo root> …]` is written, names bound in those Hy files to a value matching the group's spelling (the names of the `:environ` pairs of a declaration, and
/// the names of top-level `(val NAME VALUE)`) also become spellings of the group (names are derived from their definition rather than held as copies of the regular expression — bound_names::names_bound_to).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ConfinedSpelling {
    /// The name of the group (a detail of the registry key).
    pub name: String,
    /// Regular expressions applied to the text (that they are readable is checked at read time). Spellings of names derived from :names-bound-in are included too (after those written).
    pub patterns: Vec<String>,
    pub files: Vec<String>,
    /// Files allowed to write this spelling (empty = written nowhere in :files).
    pub except: Vec<String>,
    /// Files in which to read names bound to the group's spelling (paths from the repo root — empty = none derived).
    pub names_bound_in: Vec<String>,
    /// Names derived from :names-bound-in (in the order derived).
    pub bound_names: Vec<String>,
    /// Why only these files (put in the notice text).
    pub why: String,
    /// The position in architecture.hy (the position of the hit when no file matches :files).
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
    /// The declaration file the group is written in (`range` is a position in it).
    #[serde(skip)]
    pub file: PathBuf,
}

/// One whole declaration of effects (`(effect-census "name" :files [..] :effects [..] :base "EffectBase" :why "…")` of `:effect-census` —
/// DOEFF162). Collects the class declarations that inherit :base in the Hy and Python files of :files, and compares them with the list in :effects.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct EffectCensus {
    /// The name of the declaration (a detail of the registry key).
    pub name: String,
    pub files: Vec<String>,
    /// The list of effects (class names).
    pub effects: Vec<String>,
    /// The name of the base that the effect classes inherit (EffectBase if omitted).
    pub base: String,
    /// Why the list is closed (put in the notice text).
    pub why: String,
    /// The position in architecture.hy (the position of the hit for an effect with no declaration).
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// One roster of classes that have a typed field (`(field-holders "name" :type "T" :files [..] :classes [..] :holders [..] :why "…")` of
/// `:field-holders` — DOEFF149).
/// Among the classes directly under a module in the Python files of :files, compares with :holders the classes whose field annotation has the :type spelling as a word.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FieldHolders {
    /// The name of the declaration (a detail of the registry key).
    pub name: String,
    /// The type spelling to count in field annotations.
    pub type_name: String,
    pub files: Vec<String>,
    /// The classes to count (empty = all directly under the module in :files).
    pub classes: Vec<String>,
    /// The list of classes that may have a field of that type (empty = no class has one).
    pub holders: Vec<String>,
    /// Why this roster (put in the notice text).
    pub why: String,
    /// The position in architecture.hy (the position of the hit for a missing class from the list).
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
}

/// A decided number (a closed set of 2 — `:count N` is exactly N, `:at-least N` is N or more).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum WantedCount {
    Exactly(usize),
    AtLeast(usize),
}

impl WantedCount {
    /// Whether the counted number found fits this rule.
    pub fn accepts(self, found: usize) -> bool {
        match self {
            WantedCount::Exactly(n) => found == n,
            WantedCount::AtLeast(n) => found >= n,
        }
    }

    /// The spelling for the notice text ("exactly 3", "at least 1").
    pub fn spelling(self) -> String {
        match self {
            WantedCount::Exactly(n) => format!("exactly {}", n),
            WantedCount::AtLeast(n) => format!("at least {}", n),
        }
    }
}

/// One spelling whose count is decided (`(counted-spelling "name" :pattern r"…" :files [..] :within [..] :count N :why "…")` of
/// `:counted-spellings` — DOEFF161).
/// Counts hits of :pattern in the text of the files matched by :files (comments removed for Hy and Python; strings are counted). If :within is given, counts per named definition.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CountedSpelling {
    /// The name of the declaration (a detail of the registry key).
    pub name: String,
    pub pattern: String,
    pub files: Vec<String>,
    /// The names of the definitions to count (empty = one count across all of :files).
    pub within: Vec<String>,
    pub wanted: WantedCount,
    /// Why this count (put in the notice text).
    pub why: String,
    /// The position in architecture.hy (the position of the hit when there is nowhere to count).
    #[serde(skip)]
    pub range: doeff_indexer::hy_index::Range,
    /// The declaration file the counted spelling is written in (`range` is a position in it).
    #[serde(skip)]
    pub file: PathBuf,
}

/// Rules for fake handlers (`:business-fakes {…}` — DOEFF143). The spelling patterns of files are globs from the repo root.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct BusinessFakes {
    /// Files of the emulated environment (definitions are emulation roots, not production code).
    pub simulation: Vec<String>,
    /// Files of the assembly layer (definitions are emulation roots and also production code).
    pub assembly: Vec<String>,
    /// Test files (counted as neither emulation roots nor production code).
    pub tests: Vec<String>,
    /// Files not read.
    pub skip: Vec<String>,
    /// Files in which to look for production entry points (production code and declaration tools — if omitted, everything outside tests, emulation, and unread files).
    pub production: Vec<String>,
    /// Files of the pairing (a definition whose name starts with :simulation-prefix is an emulation root, and one starting with :production-prefix is a production entry point).
    pub sets: Vec<String>,
    pub simulation_prefix: Option<String>,
    pub production_prefix: Option<String>,
    /// The head of the module when a production entry point is pointed to by a `"<module>:<name>"` string (searched from production code and the text of :entry-string-files).
    pub entry_string_modules: Vec<String>,
    /// Declaration files in which to look for entry-point strings (deployment declarations and package declarations).
    pub entry_string_files: Vec<String>,
    /// Spellings of modules that define business effects (`a.b` is a.b and below, and a trailing `*` is a prefix match).
    pub business_modules: Vec<String>,
    /// Spellings of modules that define lower-layer effects (written the same as :business-modules). Clauses in which a counterexample handler in a test file answers these effects are also
    /// counted in matching the counterexample table (not used in judging production fakes).
    pub lower_layer_modules: Vec<String>,
    /// The dir of the table of outside-world effects (one key per file; the first line is the effect's full name, and the second line onward is the reason).
    pub external_effects: Option<String>,
    /// The dir of the table of deliberately broken counterexample handlers (key = `<path>::<handler>::<effect>`).
    pub counterexamples: Option<String>,
    /// The dir of the table of outside-world effects that have no production answerer yet (key = the effect's full name).
    pub unserved: Option<String>,
    /// The dir of the table of dispatches and refusals that answer a lower-layer effect but hold no store (key = `<path>::<handler>::<effect>`). DOEFF206 emits the lines that pass the intent layer's
    /// effects through.
    pub lower_layer_passages: Option<String>,
    /// The dir of the table that pins fakes reached only from tests (key = `<path>::<handler>::<effect>`). DOEFF206 emits the lines that pass the intent layer's effects through.
    pub test_only_fakes: Option<String>,
}

/// Rules for the shape of assembly (`:assembly-shape {…}` — DOEFF155, 156). The assembly layer, the pairing files, business modules, and the table of the outside world are
/// read from `:business-fakes` (judged only when both are written).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct AssemblyShape {
    /// The name pattern of the defk at the single point of the translation list (`*` is one — e.g. `with-*-translation`). The defk of this name in the assembly layer's files
    /// lists the translations with `(with-handlers [#* LIST …] BODY)`.
    pub translation_point: String,
    /// Names of retired assembly functions (a violation if they remain in the assembly layer — not looked at if omitted).
    pub retired_function: Option<String>,
    /// The name of the constant of the translation handler list (directly under the module in the translation layer).
    pub translations: String,
    /// The name of the layer where the translation list is placed.
    pub translation_layer: String,
    /// The name of the layer where business intent effects are placed (translation handlers must not re-emit them; material for the order of the list).
    pub intent_layer: String,
}

/// The whole of architecture.hy.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Architecture {
    pub name: String,
    pub root: String,
    pub layers: Vec<ArchLayer>,
    pub shared: Option<String>,
    pub foundation: Option<String>,
    /// The location of the emulated environment (one dir directly under root, `:verification-environment "agora_sim"`) — a verification environment that runs all services by swapping only
    /// the handlers while keeping the production assembly reads the core and entry of every service, so it belongs to no service (the same reasoning as R8's scripts/).
    /// Modules under this dir are not made location violations by DOEFF114 and 115. Other rules still apply as before.
    /// Only one is accepted (a list is not — so it does not become a field that can let anything escape. The same reason :legacy was retired).
    pub verification_environment: Option<String>,
    /// Names of public modules that may be placed directly under root (`:root-modules ["macros"]` — file stems). A Hy require reads a macro by module path, so the declaration accepts
    /// public boundaries that cannot be made lazy names of the root's entry point (ADR-DOE-API-001 R15). A file directly under root with no name is DOEFF114.
    pub root_modules: Vec<String>,
    /// A table of store pairs outside writers write, who are not jobs of the production system (`:outside-writers {"<store>:<name>" "writer" …}` — tools of outside repos,
    /// human operations, tasks that are not jobs). If written, DOEFF207 requires `:reads` / `:writes` of the jobs of the production system (defservice's `:system`), and
    /// checks that the pairs a job reads have a production job's `:writes` or a row in this table (#3496). If omitted, it does not apply.
    pub outside_writers: Option<Vec<OutsideWriter>>,
    pub open_layers: Vec<String>,
    /// Layers that may depend only on modules in fixed locations (`:placed-dependencies [core intent protocol]` — empty = not declared).
    /// If written, DOEFF140 reports modules of this layer in services and shared that import modules outside the layers' locations under root
    /// (existing dependencies on pre-move locations are removed by the move, and none are newly added).
    pub placed_dependencies: Vec<String>,
    /// Definitions judged only from fixed materials (`:blind-definitions [(blind "module:name" :forbid-words [..] :no-imports True …) …]` — empty = not declared).
    /// If written, DOEFF141 reports forbidden words in the bodies of definitions reached from those definitions by calls and references, and imports in the definition's module.
    pub blind_definitions: Vec<BlindDefinition>,
    /// Definitions with decided heads that may be called (`:allowed-heads [(allowed-heads "module:name" :heads [..] :why "…") …]` — empty = not declared).
    /// If written, DOEFF147 reports heads outside the list inside those definitions.
    pub allowed_heads: Vec<AllowedHeads>,
    /// Places and counts where a head may be called (`:call-sites [(call-site "head" :files [..] :sites [(site "module:name" :count 1) …] :why "…") …]` — empty = not
    /// declared). If written, DOEFF159 reports calls outside the places, count mismatches, and mismatches of the outer form and branch.
    pub call_sites: Vec<CallSite>,
    /// Groups of files that do not place broad exception catches (`:broad-catches [(broad-catch "name" :files [..] :carriers [(carrier "module:name" :event "event") …]
    /// :why "…") …]` — empty = not declared). If written, DOEFF160 reports broad catches outside carrier boundaries.
    pub broad_catches: Vec<BroadCatch>,
    pub services: Vec<ArchService>,
    /// The closed list of kinds of reasons that allow a plain function (the accepted answers of DOEFF203).
    pub plain_callable_reasons: Vec<ReasonKind>,
    /// Types of reasons that are not accepted, and how to fix them (the unaccepted answers of DOEFF203 — loading settings, check helpers, assembly …).
    pub rejected_plain_callable_reasons: Vec<ReasonKind>,
    /// Patterns of spellings of modules that do the JSON sending and receiving themselves (module spellings separated by `.`; `*` is any spelling within a segment and `**` is zero or more segments).
    /// DOEFF120 allows JsonValue only in modules that match here and are also in the foundation layer.
    pub wire_modules: Vec<String>,
    /// The allowlist of definitions allowed to touch the outside world (`:world-handlers` — empty = not declared).
    pub world_handlers: Vec<WorldHandler>,
    /// The roster of counterparts (`:stand-ins` — empty = not declared; DOEFF137).
    pub stand_ins: Vec<StandIn>,
    /// Components at the boundary where the emulated environment reaches the real outside world (`:boundary-parts` — empty = not declared; DOEFF106).
    pub boundary_parts: Vec<BoundaryPart>,
    /// Dirs (a list of spellings from the repo root, `:raw-io-roots ["controllers" "services"]`) where the allowlist rules (DOEFF106, 131) also apply to files outside the layers' locations.
    /// If omitted, only `:root` (services/ outside :root are also bound by the allowlist).
    pub raw_io_roots: Option<Vec<String>>,
    /// The name of the pytest marker that "edge" tests (tests that reach handlers touching the outside world) have (`:edge-mark "real_world"`).
    /// If written, DOEFF133 reports mismatches between the kind derived from where a test reaches and the presence of the marker.
    pub edge_mark: Option<String>,
    /// Definitions that only read a passed value without executing it (`:static-readers ["module:name" …]` — e.g. ClosureCase and open-foundations, which read the closure of the bases with an analyzer).
    /// References inside this call's arguments are not made "reaches" edges for DOEFF133 and 136 (they are only read as values, not executed).
    pub static_readers: Vec<DefinitionRef>,
    /// System values and the entry points that run systems (`:systems {:carriers [..] :runners [..]}`). A test that reaches the outside world from within a system value (the definition of a defsystem and the
    /// arguments of calls of :carriers) is an edge only when it also reaches one of the :runners (a test that only reads a system value is local).
    pub systems: Option<Systems>,
    /// What it touches that counts as an edge (`:edge-touches [http db …]` — all if omitted). agora does not include file or env
    /// (files in a temporary dir are local). The mechanism is the linter's, the values are the repo's declaration.
    pub edge_touches: Option<Vec<WorldTouch>>,
    /// Rules for the shape of tests (`:test-forms {:tests [..] :check-scripts [..] :runners [..]}` — each is a list of file spelling patterns).
    /// If written, DOEFF135 reports test shapes other than deftest (#1144).
    pub test_forms: Option<TestForms>,
    /// Spellings decided not to be used (`:retired-words [(retired-words …) …]` — empty = not declared). If written, DOEFF150 reports hits.
    ///The table of words exists only in the repo's declaration, and the linter holds none.
    pub retired_words: Vec<RetiredWords>,
    /// Calls decided not to be used (`:retired-calls [(retired-calls …) …]` — empty = not declared). If written, DOEFF151 reports hits (#1193).
    pub retired_calls: Vec<RetiredCalls>,
    /// Environment-name values and dry-run flags that business layers must not branch on (`:environment-branches {…}` — if omitted, DOEFF168 does not apply).
    pub environment_branches: Option<EnvironmentBranches>,
    /// The demarcation of the rules that ask Jev (DOEFF201, 202, 205) (`:semantic-lines [(line …) …]` — empty = not declared; the questions stay as they are). If written, the demarcation text and examples go into
    /// the instructions of that rule's question. They are material for the question and the editor does not read them, so they are not put in editor-json.
    #[serde(skip)]
    pub semantic_lines: Vec<SemanticLine>,
    /// Vocabulary whose judgment is confined to one place (`:single-point-vocabulary [(vocabulary-scope …) …]` — empty = not declared). If written,
    /// DOEFF146 reports files that read this vocabulary outside :except (#1371).
    pub single_point_vocabulary: Vec<VocabularyScope>,
    /// Spellings for which the files allowed to write them are decided (`:confined-spellings [(confined-spelling …) …]` — empty = not declared). If written, DOEFF148 reports
    /// files that write this spelling outside :except (#1436).
    pub confined_spellings: Vec<ConfinedSpelling>,
    /// Spellings whose count is decided (`:counted-spellings [(counted-spelling …) …]` — empty = not declared). If written, DOEFF161 reports places where the number of hits in :files (or in the
    /// definitions of :within) is not the decided number (#1437).
    pub counted_spellings: Vec<CountedSpelling>,
    /// The whole declaration of effects (`:effect-census [(effect-census …) …]` — empty = not declared). If written, DOEFF162 reports declarations of effects not in the list,
    /// double declarations, and effects in the list with no declaration (#1438).
    pub effect_census: Vec<EffectCensus>,
    /// The roster of classes that have a typed field (`:field-holders [(field-holders …) …]` — empty = not declared). If written, DOEFF149 reports holders not in the list
    /// and classes in the list that do not have the field.
    pub field_holders: Vec<FieldHolders>,
    /// Rules for handler arguments (if written, DOEFF142 reports clients and mutable stores in defhandler arguments).
    pub handler_arguments: Option<HandlerArguments>,
    /// Files that read type annotations on the public surface (`:typed-values {:files [..] :except [..]}`). If written, DOEFF144 reports bare mappings and
    /// bare tuples in the types of fields, return values, and :post, and answers that are tuple literals.
    pub typed_values: Option<FileSelection>,
    /// Files that read type declarations (.pyi) (`:record-stubs {:files [..] :except [..]}`). If written, DOEFF145 reports a .pyi that declares a kw-only
    /// record of the same-named .hy in the same dir with a @dataclass without kw_only=True (#1191).
    pub record_stubs: Option<FileSelection>,
    /// Rules for fake handlers (if written, DOEFF143 reports fake handlers that answer business effects).
    pub business_fakes: Option<BusinessFakes>,
    /// Rules for the shape of assembly (if written, DOEFF155 and 156 report violations of the assembly shape).
    pub assembly_shape: Option<AssemblyShape>,
    #[serde(skip)]
    pub role_descriptions: BTreeMap<String, String>,
    #[serde(skip)]
    pub exclude: Vec<String>,
    #[serde(skip)]
    pub extensions: Option<Vec<String>>,
    /// The file that was read.
    #[serde(skip)]
    pub path: PathBuf,
    /// Keys this binary does not know (the rest was read without them — reported by DOEFF100).
    #[serde(skip)]
    pub notices: Vec<super::notice::ConfigNotice>,
    /// The catalog of real I/O handlers with the repo's catalog files laid on top (`[tool.doeff-linter] world_catalogs` — set by the run after reading the
    /// config). None = only the bundled catalog. The declaration does not read it; it only carries it to the rules that match the declaration against the catalog.
    #[serde(skip)]
    pub repo_catalog: Option<std::sync::Arc<super::world_catalog::WorldCatalog>>,
    /// Starting dirs outside the root for resolving where an effect name is defined, added after those the resolver derives from the root's own files (`--import-root` —
    /// set by the run; the commit hook passes the checkout's `origins::dependency_roots` to the run on the HEAD tree placed elsewhere). The declaration does not read it.
    #[serde(skip)]
    pub import_roots: Vec<PathBuf>,
}

impl Architecture {
    /// The catalog of real I/O handlers this run matches the declaration against (the bundled catalog plus the repo's catalog files).
    pub fn world_catalog(&self) -> &super::world_catalog::WorldCatalog {
        match self.repo_catalog.as_deref() {
            Some(catalog) => catalog,
            None => super::world_catalog::WorldCatalog::bundled(),
        }
    }

    /// The layers of a dependency-target service that a module of layer reader may read (the layer's :dependency-layers, or :open-layers if absent).
    pub fn dependency_layers_for(&self, reader: &str) -> &[String] {
        self.layers
            .iter()
            .find(|l| l.name == reader)
            .and_then(|l| l.dependency_layers.as_deref())
            .unwrap_or(&self.open_layers)
    }

    /// Among the layers a module (a dot-separated spelling) falls under, the layers that have a rule exemption (the same as a :modules name, or a module below it). The single point that decides the Python rules' population
    /// per file (crate::population) reads this.
    pub fn exempt_layer_of(&self, module: &str) -> Option<&ArchLayer> {
        self.layers.iter().find(|layer| {
            !layer.exempt.is_empty()
                && layer.modules.iter().any(|named| {
                    module == named || module.strip_prefix(named.as_str()).is_some_and(|rest| rest.starts_with('.'))
                })
        })
    }

    /// Among the layers that name a file (a path relative to the dir where the declaration is, `/`-separated) in :files, the layers that have a rule exemption. Only a path
    /// exactly identical to the named one is included.
    pub fn exempt_layer_of_file(&self, relative: &str) -> Option<&ArchLayer> {
        self.layers
            .iter()
            .find(|layer| !layer.exempt.is_empty() && layer.files.iter().any(|named| named == relative))
    }

    /// Reads a file into a declaration (a list of reasons with positions when it cannot be read or the shape is wrong). root is the repo root (the base of :contract-files paths).
    pub fn load(path: &Path, root: &Path) -> Result<Architecture, Vec<String>> {
        Architecture::load_all(&[path.to_path_buf()], root)
    }

    /// Reads several declaration files into one declaration (`[tool.doeff-linter] architecture` as a list — config::declaration_files). The files are read as one:
    /// exactly one defarchitecture across them, defservice in any of them, and the list keys of defarchitecture-lists in any of them (each key in one place only).
    pub fn load_all(paths: &[PathBuf], root: &Path) -> Result<Architecture, Vec<String>> {
        let mut sources = Vec::new();
        let mut problems = Vec::new();
        for path in paths {
            match std::fs::read_to_string(path) {
                Ok(source) => sources.push((path.as_path(), source)),
                Err(e) => problems.push(format!("cannot read {}: {}", path.display(), e)),
            }
        }
        if !problems.is_empty() {
            return Err(problems);
        }
        let sources: Vec<(&Path, &str)> = sources.iter().map(|(path, source)| (*path, source.as_str())).collect();
        Architecture::parse_sources(&sources, root)
    }

    /// Turns source into a declaration (reads the dir where architecture.hy is as the repo root — the default location).
    pub fn parse(source: &str, path: &Path) -> Result<Architecture, Vec<String>> {
        let root = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
        Architecture::parse_at(source, path, root)
    }

    /// Turns source into a declaration (root is the repo root — the contract files of :contract-files are read from here).
    pub fn parse_at(source: &str, path: &Path, root: &Path) -> Result<Architecture, Vec<String>> {
        Architecture::parse_sources(&[(path, source)], root)
    }

    /// Turns the sources of one or more declaration files (in the configured order) into one declaration. Each reason carries the path and line of the file it was written in.
    pub fn parse_sources(sources: &[(&Path, &str)], root: &Path) -> Result<Architecture, Vec<String>> {
        let mut problems: Vec<String> = Vec::new();
        let mut notices: Vec<super::notice::ConfigNotice> = Vec::new();
        // The defarchitecture read, with where it was written (`file:line:column`) and where it wrote each list key.
        let mut architecture: Option<(Architecture, String, Vec<(String, String)>)> = None;
        let mut services: Vec<(ArchService, String)> = Vec::new();
        let mut lists: Vec<ArchitectureLists> = Vec::new();
        for (path, source) in sources {
            let mut reader = Reader::new(source, 0, source.len());
            let forms = reader.read_all();
            let lines = LineIndex::new(source);
            let mut parser = Parser { src: source, lines: &lines, path, root, problems: Vec::new(), unknown: Vec::new(), list_keys: Vec::new() };
            for issue in &reader.issues {
                let offset = match issue {
                    ReadIssue::Unclosed { open, .. } => *open,
                    ReadIssue::Mismatched { at, .. } | ReadIssue::StrayCloser { at } => *at,
                    ReadIssue::UnterminatedString { start } => *start,
                };
                let at = lines.position(offset);
                parser.problems.push(format!("{}:{}:{}: a parenthesis or string is not closed or is mismatched", path.display(), at.line + 1, at.character + 1));
            }
            for form in &forms {
                let Some(items) = parser.paren(form) else { continue };
                match items.first().and_then(|h| parser.symbol(h)) {
                    Some("defarchitecture") => {
                        let at = parser.at(form);
                        if let Some((_, first, _)) = &architecture {
                            parser.problem(form, &format!("there are two defarchitecture forms (the other at {})", first));
                        }
                        parser.list_keys.clear();
                        if let Some(read) = parser.architecture(form, &items, path) {
                            architecture = Some((read, at, std::mem::take(&mut parser.list_keys)));
                        }
                    }
                    Some("defservice") => {
                        if let Some(service) = parser.service(form, &items) {
                            if let Some((_, first)) = services.iter().find(|(s, _)| s.name == service.name) {
                                parser.problem(form, &format!("service {} is declared twice (the other at {})", service.name, first));
                            }
                            services.push((service, parser.at(form)));
                        }
                    }
                    Some("defarchitecture-lists") => lists.extend(parser.architecture_lists(form, &items)),
                    Some("require" | "import") => {}
                    Some(other) => parser.problem(
                        form,
                        &format!("a form that cannot be placed in architecture.hy: {} (only defarchitecture, defarchitecture-lists and defservice)", other),
                    ),
                    None => {}
                }
            }
            notices.extend(parser.unknown.iter().map(|(offset, key)| super::notice::ConfigNotice {
                file: path.to_path_buf(),
                key: key.clone(),
                kind: super::notice::NoticeKind::Key,
                range: crate::position::line_range(source, *offset),
            }));
            problems.append(&mut parser.problems);
        }
        let Some((mut architecture, at, written)) = architecture else {
            let files: Vec<String> = sources.iter().map(|(path, _)| path.display().to_string()).collect();
            problems.push(format!("{}: there is no defarchitecture", files.join(", ")));
            return Err(problems);
        };
        // The list keys written outside defarchitecture: the name is the defarchitecture's, and each key is written in one place only.
        let mut placed: Vec<(String, String)> = written;
        for read in lists {
            if read.name != architecture.name {
                problems.push(format!("{}: defarchitecture-lists {} names a declaration other than defarchitecture {}", read.at, read.name, architecture.name));
            }
            for (key, at, value) in read.keys {
                if let Some((_, first)) = placed.iter().find(|(k, _)| *k == key) {
                    problems.push(format!("{}: {} is written in two places (the other at {}) — write each list key in one place", at, key, first));
                    continue;
                }
                placed.push((key, at));
                match value {
                    ArchitectureList::RetiredWords(words) => architecture.retired_words = words,
                    ArchitectureList::ConfinedSpellings(groups) => architecture.confined_spellings = groups,
                    ArchitectureList::SemanticLines(lines) => architecture.semantic_lines = lines,
                    ArchitectureList::CountedSpellings(counted) => architecture.counted_spellings = counted,
                }
            }
        }
        architecture.services = services.into_iter().map(|(service, _)| service).collect();
        architecture.notices = notices;
        problems.extend(check(&architecture, &at));
        if problems.is_empty() {
            Ok(architecture)
        } else {
            Err(problems)
        }
    }

    /// Modules of the allowlist definitions (mangled dotted spelling) — where raw side effects are allowed (DOEFF106).
    pub fn world_modules(&self) -> BTreeSet<String> {
        self.world_handlers.iter().map(|h| h.definition.mangled_module()).collect()
    }

    /// Modules of boundary components (mangled dotted spelling) -> the kinds of touches allowed (DOEFF106 does not make evidence within the kinds a hit).
    pub fn boundary_touches(&self) -> BTreeMap<String, Vec<WorldTouch>> {
        self.boundary_parts.iter().map(|p| (mangle_dotted(&p.module), p.touches.clone())).collect()
    }

    /// What it touches that counts as an edge (`:edge-touches`; all if omitted).
    pub fn counts_as_edge(&self, touches: &[WorldTouch]) -> bool {
        match &self.edge_touches {
            None => !touches.is_empty(),
            Some(edge) => touches.iter().any(|t| edge.contains(t)),
        }
    }

    /// Fully qualified names of the allowlist definitions -> spelling.
    pub fn world_definition_targets(&self) -> BTreeMap<String, String> {
        self.world_handlers.iter().map(|h| (h.definition.target(), h.definition.spelling())).collect()
    }

    /// Fully qualified names of the doeff real-I/O handlers listed in :wraps -> (spelling, the list of spellings of the allowlist definitions that listed it).
    pub fn wrapped_targets(&self) -> BTreeMap<String, (String, Vec<String>)> {
        let mut out: BTreeMap<String, (String, Vec<String>)> = BTreeMap::new();
        for handler in &self.world_handlers {
            for wrapped in &handler.wraps {
                out.entry(wrapped.target()).or_insert_with(|| (wrapped.spelling(), Vec::new())).1.push(handler.definition.spelling());
            }
        }
        out
    }

    /// The dir of the verification environment (`:verification-environment`) — `<root>/<name>` directly under root (None if not written). The same reading as the location exemption (DOEFF114, 115):
    /// where the name of the first dir under root equals the declared name. DOEFF206 and the layer-3 rules (DOEFF155, 156) use this one reading.
    pub fn verification_environment_dir(&self) -> Option<String> {
        self.verification_environment.as_deref().map(|place| format!("{}/{}", normalize_dir(&self.root), place))
    }

    /// Whether a path from root is a file inside the verification environment's dir (the dir itself is not counted).
    pub fn in_verification_environment(&self, rel: &str) -> bool {
        self.verification_environment_dir().is_some_and(|dir| rel.starts_with(&format!("{}/", dir)))
    }

    /// Looks up a service's declaration by dir name.
    pub fn service_by_dir(&self, dir: &str) -> Option<&ArchService> {
        self.services.iter().find(|s| s.dir == dir)
    }

    /// Maps to the layers' settings section (the same shape as TOML's [tool.doeff-linter.layers]) — the layer rules judge with this as before.
    pub fn layers_section(&self) -> LayersSection {
        let root = normalize_dir(&self.root);
        let order: Vec<String> = self.layers.iter().map(|l| l.name.clone()).collect();
        let paths = self
            .layers
            .iter()
            .map(|layer| {
                let mut places = Vec::new();
                if self.foundation.as_deref() == Some(layer.name.as_str()) {
                    places.push(format!("{}/{}", root, layer.name));
                } else {
                    places.push(format!("{}/*/{}", root, layer.name));
                }
                (layer.name.clone(), PathPatterns::Many(places))
            })
            .collect();
        LayersSection {
            order,
            paths,
            exclude: self.exclude.clone(),
            extensions: self.extensions.clone(),
            allow_imports: self.layers.iter().filter_map(|l| l.imports.clone().map(|i| (l.name.clone(), i))).collect(),
            forbid_modules: self.layers.iter().filter(|l| !l.forbid_modules.is_empty()).map(|l| (l.name.clone(), l.forbid_modules.clone())).collect(),
            types_only: self.layers.iter().filter(|l| l.types_only).map(|l| l.name.clone()).collect(),
            wire_free: self.layers.iter().filter(|l| l.wire_free).map(|l| l.name.clone()).collect(),
            function_definers: None,
            describe: self
                .layers
                .iter()
                .map(|l| {
                    (
                        l.name.clone(),
                        LayerDescription { summary: l.summary.clone(), knows: l.knows.clone(), does_not_know: l.does_not_know.clone(), question: l.question.clone() },
                    )
                })
                .collect(),
        }
    }

    /// Maps to the roles' settings section.
    pub fn roles_section(&self) -> RolesSection {
        let names: BTreeSet<String> = self.layers.iter().flat_map(|l| l.roles.iter().cloned()).collect();
        RolesSection {
            names: names.into_iter().collect(),
            by_layer: self.layers.iter().filter(|l| !l.roles.is_empty()).map(|l| (l.name.clone(), l.roles.clone())).collect(),
            describe: self.role_descriptions.clone(),
        }
    }
}

/// The reading tool (accumulates reasons with positions).
struct Parser<'a> {
    src: &'a str,
    lines: &'a LineIndex<'a>,
    path: &'a Path,
    /// The repo root (the base for reading the contract files of :contract-files).
    root: &'a Path,
    problems: Vec<String>,
    /// Keys this binary does not know (the byte position, and which form the key was in).
    unknown: Vec<(usize, String)>,
    /// The list keys (LIST_KEYS) the defarchitecture being read wrote, with where (`file:line:column`) — a key written there cannot be written again in defarchitecture-lists.
    list_keys: Vec<(String, String)>,
}

/// A list of key and value pairs (`:root "x" :layers [...]`).
type Pairs<'f> = Vec<(&'f Form, &'f Form)>;

/// The keys of defarchitecture that may also be written outside it, in `(defarchitecture-lists NAME :KEY VALUE …)` — the long word lists, so that a
/// declaration split into files can keep them in a file of their own. Each key is written in one place only (in defarchitecture or in one defarchitecture-lists).
pub const LIST_KEYS: [&str; 4] = [":retired-words", ":confined-spellings", ":semantic-lines", ":counted-spellings"];

/// The value of one list key (a closed set of LIST_KEYS).
enum ArchitectureList {
    RetiredWords(Vec<RetiredWords>),
    ConfinedSpellings(Vec<ConfinedSpelling>),
    SemanticLines(Vec<SemanticLine>),
    CountedSpellings(Vec<CountedSpelling>),
}

/// One `(defarchitecture-lists NAME :KEY VALUE …)` read — the name, where the form is, and the keys with where each is written.
struct ArchitectureLists {
    name: String,
    at: String,
    keys: Vec<(String, String, ArchitectureList)>,
}

impl<'a> Parser<'a> {
    /// Where a form is written (`file:line:column`, 1-based) — the prefix of a reason, also named by a reason written in another file.
    fn at(&self, form: &Form) -> String {
        let at = self.lines.position(form.span.start);
        format!("{}:{}:{}", self.path.display(), at.line + 1, at.character + 1)
    }

    /// Reads the value of one list key (the key is one of LIST_KEYS).
    fn list(&mut self, key: &Form, value: &Form) -> Option<ArchitectureList> {
        match self.text(key) {
            ":retired-words" => Some(ArchitectureList::RetiredWords(self.retired_words(value))),
            ":confined-spellings" => Some(ArchitectureList::ConfinedSpellings(self.confined_spellings(value))),
            ":semantic-lines" => Some(ArchitectureList::SemanticLines(self.semantic_lines(value))),
            ":counted-spellings" => Some(ArchitectureList::CountedSpellings(self.counted_spellings(value))),
            other => {
                self.problem(key, &format!("{} cannot be written in defarchitecture-lists (only {})", other, LIST_KEYS.join(" ")));
                None
            }
        }
    }

    /// `(defarchitecture-lists NAME :KEY VALUE …)` — list keys of the defarchitecture NAME written outside it (only LIST_KEYS; each key once in the form).
    fn architecture_lists(&mut self, form: &Form, items: &[&Form]) -> Option<ArchitectureLists> {
        let Some(name) = items.get(1).and_then(|f| self.name(f)) else {
            self.problem(form, "defarchitecture-lists has no name (the name of the defarchitecture whose lists it holds)");
            return None;
        };
        let mut read = ArchitectureLists { name, at: self.at(form), keys: Vec::new() };
        let rest: Vec<&Form> = items.iter().skip(2).copied().collect();
        for (key, value) in self.pairs(&rest) {
            let spelling = self.text(key);
            if let Some((_, first, _)) = read.keys.iter().find(|(k, _, _)| k == spelling) {
                self.problem(key, &format!("{} is written in two places (the other at {}) — write each list key in one place", spelling, first));
                continue;
            }
            let at = self.at(key);
            let Some(value) = self.list(key, value) else { continue };
            read.keys.push((spelling.to_string(), at, value));
        }
        Some(read)
    }

    /// Accumulates a key this binary does not know as a notice (not an error, and that key's value is not read).
    fn unknown_key(&mut self, key: &Form, owner: &str) {
        let text = self.text(key).to_string();
        self.unknown.push((key.span.start, format!("{} {}", owner, text)));
    }

    /// Accumulates a reason with the form's position (file:line:column is 1-based).
    fn problem(&mut self, form: &Form, reason: &str) {
        let at = self.lines.position(form.span.start);
        self.problems.push(format!("{}:{}:{}: {}", self.path.display(), at.line + 1, at.character + 1, reason));
    }

    /// The form's spelling.
    fn text(&self, form: &Form) -> &'a str {
        self.src.get(form.span.start..form.span.end).unwrap_or("")
    }

    /// The contents of `( … )` (excluding discarded ones).
    fn paren<'f>(&self, form: &'f Form) -> Option<Vec<&'f Form>> {
        form.paren_items().map(|items| items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect())
    }

    /// The contents of `[ … ]`.
    fn bracket<'f>(&self, form: &'f Form) -> Option<Vec<&'f Form>> {
        form.bracket_items().map(|items| items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect())
    }

    /// The contents of `{ … }`.
    fn brace<'f>(&self, form: &'f Form) -> Option<Vec<&'f Form>> {
        match &form.node {
            Node::Seq { delim: Delim::Brace, items } => Some(items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect()),
            _ => None,
        }
    }

    /// The spelling of a symbol.
    fn symbol(&self, form: &Form) -> Option<&'a str> {
        matches!(form.node, Node::Symbol).then(|| self.text(form))
    }

    /// The value of a string (an ordinary string or a bracket string).
    fn string(&self, form: &Form) -> Option<String> {
        match &form.node {
            Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } => self.src.get(body.start..body.end).map(|t| t.replace("\\\"", "\"")),
            _ => None,
        }
    }

    /// A name (a symbol or a string).
    fn name(&self, form: &Form) -> Option<String> {
        self.symbol(form).map(str::to_string).or_else(|| self.string(form))
    }

    /// Reads a list of keyword and value pairs (a non-keyword, or a keyword with no value, accumulates a reason).
    fn pairs<'f>(&mut self, items: &[&'f Form]) -> Pairs<'f> {
        let mut out = Vec::new();
        let mut index = 0;
        while index < items.len() {
            let key = items[index];
            if !matches!(key.node, Node::Keyword) {
                self.problem(key, &format!("{} found where a keyword was expected", self.text(key)));
                index += 1;
                continue;
            }
            match items.get(index + 1) {
                Some(value) => out.push((key, *value)),
                None => self.problem(key, &format!("{} has no value", self.text(key))),
            }
            index += 2;
        }
        out
    }

    /// A list of names (`[a b "c"]`).
    fn names(&mut self, form: &Form, what: &str) -> Vec<String> {
        match self.bracket(form) {
            Some(items) => items
                .into_iter()
                .filter_map(|item| {
                    let name = self.name(item);
                    if name.is_none() {
                        self.problem(item, &format!("an element of {} must be a name (a symbol or a string)", what));
                    }
                    name
                })
                .collect(),
            None => {
                self.problem(form, &format!("{} must be a [ … ] list", what));
                Vec::new()
            }
        }
    }

    /// Reads a string value for a key that needs one.
    fn required_string(&mut self, form: &Form, what: &str) -> Option<String> {
        let value = self.string(form);
        if value.is_none() {
            self.problem(form, &format!("{} must be a string", what));
        }
        value
    }

    /// `(defarchitecture NAME :KEY VALUE …)`.
    fn architecture(&mut self, form: &Form, items: &[&Form], path: &Path) -> Option<Architecture> {
        let name = items.get(1).and_then(|f| self.name(f));
        if name.is_none() {
            self.problem(form, "defarchitecture has no name");
        }
        let mut arch = Architecture {
            name: name.unwrap_or_default(),
            root: String::new(),
            layers: Vec::new(),
            shared: None,
            foundation: None,
            verification_environment: None,
            root_modules: Vec::new(),
            outside_writers: None,
            open_layers: Vec::new(),
            placed_dependencies: Vec::new(),
            blind_definitions: Vec::new(),
            allowed_heads: Vec::new(),
            call_sites: Vec::new(),
            broad_catches: Vec::new(),
            services: Vec::new(),
            plain_callable_reasons: Vec::new(),
            rejected_plain_callable_reasons: Vec::new(),
            wire_modules: Vec::new(),
            world_handlers: Vec::new(),
            stand_ins: Vec::new(),
            boundary_parts: Vec::new(),
            raw_io_roots: None,
            edge_mark: None,
            static_readers: Vec::new(),
            systems: None,
            edge_touches: None,
            test_forms: None,
            retired_words: Vec::new(),
            retired_calls: Vec::new(),
            environment_branches: None,
            semantic_lines: Vec::new(),
            single_point_vocabulary: Vec::new(),
            confined_spellings: Vec::new(),
            counted_spellings: Vec::new(),
            effect_census: Vec::new(),
            field_holders: Vec::new(),
            handler_arguments: None,
            typed_values: None,
            record_stubs: None,
            business_fakes: None,
            assembly_shape: None,
            role_descriptions: BTreeMap::new(),
            exclude: vec!["tests".into(), "__pycache__".into(), "conftest.py".into()],
            extensions: None,
            path: path.to_path_buf(),
            notices: Vec::new(),
            repo_catalog: None,
            import_roots: Vec::new(),
        };
        let rest: Vec<&Form> = items.iter().skip(2).copied().collect();
        let mut open_given = false;
        for (key, value) in self.pairs(&rest) {
            match self.text(key) {
                ":root" => arch.root = self.required_string(value, ":root").unwrap_or_default(),
                ":layers" => {
                    let entries = self.bracket(value).unwrap_or_else(|| {
                        self.problem(value, ":layers must be a list of (layer …)");
                        Vec::new()
                    });
                    for entry in entries {
                        if let Some(layer) = self.layer(entry) {
                            if arch.layers.iter().any(|l| l.name == layer.name) {
                                self.problem(entry, &format!("layer {} is declared twice", layer.name));
                            }
                            arch.layers.push(layer);
                        }
                    }
                }
                ":shared" => arch.shared = self.required_string(value, ":shared"),
                ":foundation" => arch.foundation = self.name(value),
                ":verification-environment" => arch.verification_environment = self.required_string(value, ":verification-environment"),
                ":root-modules" => arch.root_modules = self.names(value, ":root-modules"),
                ":outside-writers" => arch.outside_writers = Some(self.outside_writers(value)),
                ":open-layers" => {
                    arch.open_layers = self.names(value, ":open-layers");
                    open_given = true;
                }
                ":placed-dependencies" => arch.placed_dependencies = self.names(value, ":placed-dependencies"),
                ":blind-definitions" => arch.blind_definitions = self.blind_definitions(value),
                ":allowed-heads" => arch.allowed_heads = self.allowed_heads(value),
                ":call-sites" => arch.call_sites = self.call_sites(value),
                ":broad-catches" => arch.broad_catches = self.broad_catches(value),
                ":exclude" => arch.exclude = self.names(value, ":exclude"),
                ":extensions" => arch.extensions = Some(self.names(value, ":extensions")),
                ":plain-callable-reasons" => arch.plain_callable_reasons = self.reasons(value, ":plain-callable-reasons"),
                ":rejected-plain-callable-reasons" => {
                    arch.rejected_plain_callable_reasons = self.reasons(value, ":rejected-plain-callable-reasons")
                }
                ":wire-modules" => arch.wire_modules = self.module_patterns(value, ":wire-modules"),
                ":world-handlers" => arch.world_handlers = self.world_handlers(value),
                ":stand-ins" => arch.stand_ins = self.stand_ins(value),
                ":boundary-parts" => arch.boundary_parts = self.boundary_parts(value),
                ":raw-io-roots" => {
                    let roots = self.names(value, ":raw-io-roots");
                    for root in &roots {
                        if root.is_empty() || root.starts_with('/') || root.split('/').any(|p| p == "..") {
                            self.problem(value, &format!("{} in :raw-io-roots must be a dir spelling from the repo root (not starting with / and not containing ..)", root));
                        }
                    }
                    arch.raw_io_roots = Some(roots);
                }
                ":test-forms" => arch.test_forms = self.test_forms(value),
                ":retired-words" | ":confined-spellings" | ":semantic-lines" | ":counted-spellings" => {
                    let at = self.at(key);
                    if let Some((_, first)) = self.list_keys.iter().find(|(name, _)| name == self.text(key)) {
                        self.problem(key, &format!("{} is written in two places (the other at {}) — write each list key in one place", self.text(key), first));
                        continue;
                    }
                    self.list_keys.push((self.text(key).to_string(), at));
                    let Some(value) = self.list(key, value) else { continue };
                    match value {
                        ArchitectureList::RetiredWords(words) => arch.retired_words = words,
                        ArchitectureList::ConfinedSpellings(groups) => arch.confined_spellings = groups,
                        ArchitectureList::SemanticLines(lines) => arch.semantic_lines = lines,
                        ArchitectureList::CountedSpellings(counted) => arch.counted_spellings = counted,
                    }
                }
                ":retired-calls" => arch.retired_calls = self.retired_calls(value),
                ":environment-branches" => arch.environment_branches = self.environment_branches(value),
                ":single-point-vocabulary" => arch.single_point_vocabulary = self.single_point_vocabulary(value),
                ":effect-census" => arch.effect_census = self.effect_census(value),
                ":field-holders" => arch.field_holders = self.field_holders(value),
                ":handler-arguments" => arch.handler_arguments = self.handler_arguments(value),
                ":typed-values" => arch.typed_values = self.file_selection(value, ":typed-values"),
                ":record-stubs" => arch.record_stubs = self.file_selection(value, ":record-stubs"),
                ":business-fakes" => arch.business_fakes = self.business_fakes(value),
                ":assembly-shape" => arch.assembly_shape = self.assembly_shape(value),
                ":edge-touches" => {
                    let mut touches = Vec::new();
                    for word in self.names(value, ":edge-touches") {
                        match WorldTouch::parse(&word) {
                            Some(touch) if !touches.contains(&touch) => touches.push(touch),
                            Some(_) => self.problem(value, &format!("{} in :edge-touches is written twice", word)),
                            None => self.problem(value, &format!("{} in :edge-touches is not in the vocabulary", word)),
                        }
                    }
                    arch.edge_touches = Some(touches);
                }
                ":edge-mark" => {
                    arch.edge_mark = self.required_string(value, ":edge-mark");
                    let well_formed = arch.edge_mark.as_deref().is_some_and(|m| !m.is_empty() && m.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'));
                    if arch.edge_mark.is_some() && !well_formed {
                        self.problem(value, ":edge-mark must be the name of a pytest marker (alphanumerics and _ — e.g. \"real_world\")");
                    }
                }
                ":static-readers" => arch.static_readers = self.definition_refs(value, ":static-readers"),
                ":systems" => match self.brace(value) {
                    Some(entries) => {
                        let mut systems = Systems::default();
                        for (key, list) in self.pairs(&entries) {
                            match self.text(key) {
                                ":carriers" => systems.carriers = self.definition_refs(list, ":carriers of :systems"),
                                ":runners" => systems.runners = self.definition_refs(list, ":runners of :systems"),
                                other => self.problem(key, &format!("unknown key {} in :systems (only :carriers and :runners)", other)),
                            }
                        }
                        if systems.runners.is_empty() {
                            self.problem(value, ":systems has no :runners (without an entry point that runs systems, every test that reaches out from inside a system would be local)");
                        }
                        arch.systems = Some(systems);
                    }
                    None => self.problem(value, ":systems must be a dictionary {:carriers [..] :runners [..]}"),
                },
                ":roles" => match self.brace(value) {
                    Some(entries) => {
                        for (role, text) in self.pairs(&entries) {
                            match self.string(text) {
                                Some(text) => {
                                    arch.role_descriptions.insert(self.text(role).trim_start_matches(':').to_string(), text);
                                }
                                None => self.problem(text, "the value of :roles must be a string describing the role"),
                            }
                        }
                    }
                    None => self.problem(value, ":roles must be a dictionary {:role \"description\" …}"),
                },
                // :legacy is retired (no escape hatch for old things is provided) — every module
                // outside the declaration is reported by DOEFF114 and 115, and existing ones are accepted by the registry.
                ":legacy" => self.problem(
                    key,
                    ":legacy has been retired — every module in a location outside the declaration is reported by DOEFF114 and 115. Put existing ones in the registry",
                ),
                _ => self.unknown_key(key, "defarchitecture"),
            }
        }
        // If :open-layers is not written, open intent only when the layer intent exists (the default).
        if !open_given && arch.layers.iter().any(|l| l.name == "intent") {
            arch.open_layers = vec!["intent".to_string()];
        }
        if arch.root.is_empty() {
            self.problem(form, "defarchitecture has no :root");
        }
        if arch.layers.is_empty() {
            self.problem(form, "defarchitecture has no :layers");
        }
        Some(arch)
    }

    /// Reads a list of module spelling patterns (`["controllers.foundation.record_client" "controllers.foundation.http.*"]`).
    /// A `.`-separated module spelling in which no segment may be empty. A path (`/`) and a spelling that mixes `**` into a segment accumulate a reason.
    fn module_patterns(&mut self, value: &Form, what: &str) -> Vec<String> {
        let Some(items) = self.bracket(value) else {
            self.problem(value, &format!("{} must be a list of module spellings [\"a.b.c\" …]", what));
            return Vec::new();
        };
        let mut out = Vec::new();
        for item in items {
            let Some(pattern) = self.name(item) else {
                self.problem(item, &format!("an element of {} must be a module spelling (a symbol or a string)", what));
                continue;
            };
            let well_formed = !pattern.contains('/')
                && pattern.split('.').all(|segment| !segment.is_empty() && (segment == "**" || !segment.contains("**")));
            if !well_formed {
                self.problem(
                    item,
                    &format!("{} in {} must be written as a module spelling (`.`-separated; `*` is any spelling within a segment and `**` is zero or more segments) — a path `/` and an empty segment cannot be used", pattern, what),
                );
                continue;
            }
            if out.contains(&pattern) {
                self.problem(item, &format!("{} in {} is written twice", pattern, what));
                continue;
            }
            out.push(pattern);
        }
        out
    }

    /// Reads a list of `["module:NAME" …]` (a name written twice or an unreadable spelling accumulates a reason).
    fn definition_refs(&mut self, value: &Form, what: &str) -> Vec<DefinitionRef> {
        let Some(items) = self.bracket(value) else {
            self.problem(value, &format!("{} must be a list of [\"module:NAME\" …]", what));
            return Vec::new();
        };
        let mut out: Vec<DefinitionRef> = Vec::new();
        for item in items {
            if let Some(found) = self.definition_ref(item, what) {
                if out.contains(&found) {
                    self.problem(item, &format!("{} in {} is written twice", found.spelling(), what));
                } else {
                    out.push(found);
                }
            }
        }
        out
    }

    /// `:clauses` of defservice (a list of clause names — an empty name or a name written twice accumulates a reason).
    /// `:entry-modules ["pkg.module" …]` of defservice (a non-empty list; elements are `.`-separated module spellings; writing the same module twice is an error).
    fn entry_modules(&mut self, value: &Form) -> Vec<String> {
        let Some(items) = self.bracket(value) else {
            self.problem(value, ":entry-modules must be a list [\"pkg.module\" …]");
            return Vec::new();
        };
        if items.is_empty() {
            self.problem(value, ":entry-modules is empty (if written, give at least one entry module)");
        }
        let mut out: Vec<String> = Vec::new();
        for item in items {
            let dotted = self
                .string(item)
                .filter(|m| !m.is_empty() && m.split('.').all(|seg| !seg.is_empty() && seg.chars().all(|c| c.is_alphanumeric() || c == '_')));
            match dotted {
                Some(module) if out.contains(&module) => self.problem(item, &format!("{} in :entry-modules is written twice", module)),
                Some(module) => out.push(module),
                None => self.problem(item, "an element of :entry-modules must be a spelling \"pkg.module\" (a string of a `.`-separated module name)"),
            }
        }
        out
    }

    fn clause_names(&mut self, value: &Form) -> Vec<String> {
        let Some(items) = self.bracket(value) else {
            self.problem(value, ":clauses must be a list [\"clause name\" …]");
            return Vec::new();
        };
        let mut out: Vec<String> = Vec::new();
        for item in items {
            match self.name(item).filter(|n| !n.is_empty() && !n.contains("::")) {
                Some(name) if out.contains(&name) => self.problem(item, &format!("{} in :clauses is written twice", name)),
                Some(name) => out.push(name),
                None => self.problem(item, "an element of :clauses must be a non-empty clause name (a symbol or a string, not containing `::`)"),
            }
        }
        out
    }

    /// `:system` of defservice: one of `"module:NAME"`, a list of them, `{:part-of "module:NAME"}`, or `{:exempt "reason"}` (other shapes accumulate a reason).
    fn system_decl(&mut self, value: &Form) -> Option<SystemDecl> {
        if self.bracket(value).is_some() {
            return Some(SystemDecl::Systems(self.definition_refs(value, ":system")));
        }
        if let Some(entries) = self.brace(value) {
            return match entries.as_slice() {
                [key, reason] if self.text(key) == ":exempt" => match self.string(reason) {
                    Some(text) => Some(SystemDecl::Exempt(text)),
                    None => {
                        self.problem(reason, ":exempt of :system must be a reason string");
                        None
                    }
                },
                [key, target] if self.text(key) == ":part-of" => self.definition_ref(target, ":part-of of :system").map(SystemDecl::PartOf),
                _ => {
                    self.problem(value, "the dictionary of :system must be only {:part-of \"module:NAME\"} or {:exempt \"reason\"}");
                    None
                }
            };
        }
        match self.string(value) {
            Some(_) => self.definition_ref(value, ":system").map(|found| SystemDecl::Systems(vec![found])),
            None => {
                self.problem(value, ":system must be one of \"module:NAME\", a list of them, {:part-of \"module:NAME\"}, or {:exempt \"reason\"}");
                None
            }
        }
    }

    /// `:clause-exemptions {"clause" "reason" …}` of defservice (a reason is a non-empty string).
    fn clause_exemptions(&mut self, value: &Form) -> Vec<ClauseExemption> {
        let Some(entries) = self.brace(value) else {
            self.problem(value, ":clause-exemptions must be a dictionary {\"clause\" \"reason\" …}");
            return Vec::new();
        };
        let mut out: Vec<ClauseExemption> = Vec::new();
        for pair in entries.chunks(2) {
            let [key, reason] = pair else {
                self.problem(pair[0], ":clause-exemptions has a clause with no reason");
                continue;
            };
            match (self.name(key), self.string(reason).filter(|r| !r.trim().is_empty())) {
                (Some(clause), _) if out.iter().any(|e| e.clause == clause) => {
                    self.problem(key, &format!("{} in :clause-exemptions is written twice", clause))
                }
                (Some(clause), Some(reason)) => out.push(ClauseExemption { clause, reason }),
                (None, _) => self.problem(key, "a key of :clause-exemptions must be a clause name (a symbol or a string)"),
                (Some(clause), None) => self.problem(reason, &format!("the reason of {} in :clause-exemptions must be a non-empty string", clause)),
            }
        }
        out
    }

    /// Reads one `"module:NAME"` spelling (an unreadable one accumulates a reason).
    fn definition_ref(&mut self, form: &Form, what: &str) -> Option<DefinitionRef> {
        let Some(text) = self.name(form) else {
            self.problem(form, &format!("{} must be a string \"module:NAME\"", what));
            return None;
        };
        let found = DefinitionRef::parse(&text);
        if found.is_none() {
            self.problem(form, &format!("{} in {} must be a spelling \"module.path:NAME\" (the module is `.`-separated with no empty segment, and the name is non-empty)", text, what));
        }
        found
    }

    /// Reads `{:tests [..] :check-scripts [..] :runners [..]}` (:tests is required).
    fn test_forms(&mut self, value: &Form) -> Option<TestForms> {
        let Some(entries) = self.brace(value) else {
            self.problem(value, ":test-forms must be a dictionary {:tests [..] :check-scripts [..] :runners [..]}");
            return None;
        };
        let mut forms = TestForms::default();
        let mut tests_given = false;
        for (key, list) in self.pairs(&entries) {
            match self.text(key) {
                ":tests" => {
                    tests_given = true;
                    forms.tests = self.names(list, ":test-forms :tests");
                }
                ":check-scripts" => forms.check_scripts = self.names(list, ":test-forms :check-scripts"),
                ":runners" => forms.runners = self.names(list, ":test-forms :runners"),
                _ => self.unknown_key(key, ":test-forms"),
            }
        }
        if !tests_given || forms.tests.is_empty() {
            self.problem(value, ":test-forms has no :tests (patterns of the spellings of test files)");
        }
        Some(forms)
    }

    /// A regular expression string (`r"…"` or a bracket string — an ordinary string would need `\` written twice, in Hy and in the regular expression, so a string containing `\` accumulates a reason).
    fn pattern(&mut self, form: &Form, what: &str) -> Option<String> {
        match &form.node {
            Node::Str { kind: StrKind::Raw | StrKind::Bracket, body } => self.src.get(body.start..body.end).map(str::to_string),
            Node::Str { kind: StrKind::Plain, body } => {
                let text = self.src.get(body.start..body.end).unwrap_or("");
                if text.contains('\\') {
                    self.problem(form, &format!("the regular expression of {} must be written as an r\"…\" string (the \\ in an ordinary string is read twice, by Hy and by the regular expression)", what));
                    return None;
                }
                Some(text.to_string())
            }
            _ => {
                self.problem(form, &format!("an element of {} must be a regular expression string (r\"…\")", what));
                None
            }
        }
    }

    /// Reads a list of path globs from the repo root (a spelling starting with `/` or containing `..` accumulates a reason).
    fn path_globs(&mut self, value: &Form, what: &str) -> Vec<String> {
        let globs = self.names(value, what);
        for glob in &globs {
            if glob.is_empty() || glob.starts_with('/') || glob.split('/').any(|p| p == "..") {
                self.problem(value, &format!("{} in {} must be a path glob from the repo root (not starting with / and not containing ..)", glob, what));
            }
        }
        globs
    }

    /// Reads `{:files [..] :except [..]}` (:files is required — if empty, accumulates a reason and treats it as not declared).
    fn file_selection(&mut self, value: &Form, what: &str) -> Option<FileSelection> {
        let Some(entries) = self.brace(value) else {
            self.problem(value, &format!("{} must be a dictionary {{:files [..] :except [..]}}", what));
            return None;
        };
        let mut selection = FileSelection::default();
        for (key, list) in self.pairs(&entries) {
            match self.text(key) {
                ":files" => selection.files = self.path_globs(list, &format!("{} :files", what)),
                ":except" => selection.except = self.path_globs(list, &format!("{} :except", what)),
                _ => self.unknown_key(key, what),
            }
        }
        if selection.files.is_empty() {
            self.problem(value, &format!("{} has no :files (globs of the files to read)", what));
            return None;
        }
        Some(selection)
    }

    /// Reads `[(retired-words "name" :words [..] :patterns [r"…"] :files [..] :except [..] :rule-lines [..] :contract-files [..] :in lines|names|paths :instead "…") …]`
    /// (:files, :instead, and either :words or :patterns are required).
    fn retired_words(&mut self, value: &Form) -> Vec<RetiredWords> {
        let shape = "(retired-words \"name\" :words [..] :patterns [r\"…\"] :files [..] :except [..]? :rule-lines [..]? :contract-files [..]? :in lines|names|paths? :instead \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":retired-words must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<RetiredWords> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("retired-words"));
            let Some(name) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f)) else {
                self.problem(entry, &format!("an element of :retired-words must be {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let mut group = RetiredWords {
                name,
                words: Vec::new(),
                patterns: Vec::new(),
                files: Vec::new(),
                except: Vec::new(),
                rule_lines: Vec::new(),
                contracts: ContractSpellings::default(),
                place: WordPlace::Lines,
                instead: String::new(),
            };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":words" => group.words = self.names(field, ":words"),
                    ":patterns" => match self.bracket(field) {
                        Some(items) => {
                            for item in items {
                                let Some(pattern) = self.pattern(item, ":patterns") else { continue };
                                match check_regex(&pattern) {
                                    Ok(_) => group.patterns.push(pattern),
                                    Err(error) => self.problem(item, &format!("cannot read the regular expression {} of retired-words {}: {}", pattern, group.name, error)),
                                }
                            }
                        }
                        None => self.problem(field, ":patterns must be a list [r\"…\" …]"),
                    },
                    ":files" => group.files = self.path_globs(field, ":files"),
                    ":except" => group.except = self.path_globs(field, ":except"),
                    ":rule-lines" => group.rule_lines = self.names(field, ":rule-lines"),
                    ":contract-files" => group.contracts = self.contract_files(field, &group.name),
                    ":in" => match self.name(field).as_deref() {
                        Some("lines") => group.place = WordPlace::Lines,
                        Some("names") => group.place = WordPlace::Names,
                        Some("paths") => group.place = WordPlace::Paths,
                        _ => self.problem(field, ":in must be lines, names, or paths"),
                    },
                    ":instead" => group.instead = self.required_string(field, ":instead").unwrap_or_default(),
                    _ => self.unknown_key(key, "retired-words"),
                }
            }
            if group.words.is_empty() && group.patterns.is_empty() {
                self.problem(entry, &format!("retired-words {} has neither :words nor :patterns", group.name));
            }
            if group.words.iter().any(|w| w.trim().is_empty()) {
                self.problem(entry, &format!("retired-words {} has an empty word in :words", group.name));
            }
            if group.files.is_empty() {
                self.problem(entry, &format!("retired-words {} has no :files (a group with no files to search is not placed)", group.name));
            }
            if group.instead.trim().is_empty() {
                self.problem(entry, &format!("retired-words {} has no :instead (the word to use instead, and how to fix it)", group.name));
            }
            if group.place != WordPlace::Lines && !group.rule_lines.is_empty() {
                self.problem(entry, &format!(":rule-lines of retired-words {} is effective only with :in lines", group.name));
            }
            if group.place != WordPlace::Lines && !group.contracts.files.is_empty() {
                self.problem(entry, &format!(":contract-files of retired-words {} is effective only with :in lines", group.name));
            }
            if out.iter().any(|g| g.name == group.name) {
                self.problem(entry, &format!("retired-words {} is declared twice", group.name));
                continue;
            }
            out.push(group);
        }
        out
    }

    /// Reads the group's `:contract-files [<path from the repo root> …]` of retired-words group `group`, and collects spellings from the JSON of the contract files
    /// (collect_contract_spellings). An empty list, a path outside the root, an unreadable file, a file that is not JSON, and a file with no spelling at all are not
    /// silently made empty but are made declaration errors with positions.
    fn contract_files(&mut self, value: &Form, group: &str) -> ContractSpellings {
        let mut contracts = ContractSpellings::default();
        let Some(items) = self.bracket(value) else {
            self.problem(value, &format!(":contract-files of retired-words {} must be a list [\"<path from the repo root>\" …]", group));
            return contracts;
        };
        if items.is_empty() {
            self.problem(value, &format!(":contract-files of retired-words {} is empty (omit it, or list contract files)", group));
        }
        for item in items {
            let Some(rel) = self.name(item) else {
                self.problem(item, &format!("an element of :contract-files of retired-words {} must be a path string", group));
                continue;
            };
            if rel.is_empty() || rel.starts_with('/') || rel.split('/').any(|p| p == "..") {
                self.problem(item, &format!("{} in :contract-files of retired-words {} must be a path from the repo root (not starting with / and not containing ..)", rel, group));
                continue;
            }
            let path = self.root.join(&rel);
            let parsed = std::fs::read_to_string(&path)
                .map_err(|e| format!("cannot be read: {}", e))
                .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).map_err(|e| format!("cannot be read as JSON: {}", e)));
            match parsed {
                Ok(json) => {
                    let mut spellings = BTreeSet::new();
                    collect_contract_spellings(&json, &mut spellings);
                    if spellings.is_empty() {
                        self.problem(item, &format!("{} in :contract-files of retired-words {} has no contract spelling at all (key names, enum / const values)", rel, group));
                    }
                    contracts.spellings.extend(spellings);
                }
                Err(reason) => self.problem(item, &format!("{} ({}) in :contract-files of retired-words {} {}", rel, path.display(), group, reason)),
            }
            contracts.files.push(rel);
        }
        contracts
    }

    /// Reads `[(confined-spelling "name" :patterns [r"…"] :files [..] :except [..] :names-bound-in [..] :why "…") …]` (:patterns, :files, and
    /// :why are required — :except may be omitted = a spelling written nowhere in :files; :names-bound-in may be omitted = no names derived).
    fn confined_spellings(&mut self, value: &Form) -> Vec<ConfinedSpelling> {
        let shape = "(confined-spelling \"name\" :patterns [r\"…\"] :files [..] :except [..] :names-bound-in [..]? :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":confined-spellings must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<ConfinedSpelling> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("confined-spelling"));
            let Some((name, head)) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f).map(|n| (n, *f))) else {
                self.problem(entry, &format!("an element of :confined-spellings must be {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let range = self.lines.range(head.span.start, head.span.end);
            let mut group = ConfinedSpelling {
                name,
                patterns: Vec::new(),
                files: Vec::new(),
                except: Vec::new(),
                names_bound_in: Vec::new(),
                bound_names: Vec::new(),
                why: String::new(),
                range,
                file: self.path.to_path_buf(),
            };
            let mut bound_in: Option<&Form> = None;
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":patterns" => match self.bracket(field) {
                        Some(items) => {
                            for item in items {
                                let Some(pattern) = self.pattern(item, ":patterns") else { continue };
                                match check_regex(&pattern) {
                                    Ok(_) => group.patterns.push(pattern),
                                    Err(error) => self.problem(item, &format!("cannot read the regular expression {} of confined-spelling {}: {}", pattern, group.name, error)),
                                }
                            }
                        }
                        None => self.problem(field, ":patterns must be a list [r\"…\" …]"),
                    },
                    ":files" => group.files = self.path_globs(field, ":files"),
                    ":except" => group.except = self.path_globs(field, ":except"),
                    ":names-bound-in" => bound_in = Some(field),
                    ":why" => group.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "confined-spelling"),
                }
            }
            if group.patterns.is_empty() {
                self.problem(entry, &format!("confined-spelling {} has no :patterns", group.name));
            } else if let Some(field) = bound_in {
                self.names_bound_in(field, &mut group);
            }
            if group.files.is_empty() {
                self.problem(entry, &format!("confined-spelling {} has no :files (a group with no files to search is not placed)", group.name));
            }
            if group.why.trim().is_empty() {
                self.problem(entry, &format!("confined-spelling {} has no :why (why only these files)", group.name));
            }
            if out.iter().any(|g| g.name == group.name) {
                self.problem(entry, &format!("confined-spelling {} is declared twice", group.name));
                continue;
            }
            out.push(group);
        }
        out
    }

    /// Reads the Hy files of group `group`'s `:names-bound-in [<path from the repo root> …]` and adds, to the group's spellings, the names bound to a value matching the group's spelling
    /// (bound_names::names_bound_to — names bound to a value matching an added name are also added, until nothing more is added). A file that binds names must be a file
    /// allowed to write the group's spelling (matching :except) — a binding in an outside file is itself a hit. A path outside the root, a file not in :except, an unreadable file,
    /// a file with unclosed parentheses, and a file from which no name can be derived are not silently made a group with no copy but are made declaration errors with positions (the same as
    /// "no spelling at all" for each file of :contract-files — because moving a constant into a function or changing the binding shape would silently drop that file's names from the group.
    /// Read when architecture.hy is read).
    fn names_bound_in(&mut self, value: &Form, group: &mut ConfinedSpelling) {
        let Some(items) = self.bracket(value) else {
            self.problem(value, &format!(":names-bound-in of confined-spelling {} must be a list [\"<path from the repo root>\" …]", group.name));
            return;
        };
        if items.is_empty() {
            self.problem(value, &format!(":names-bound-in of confined-spelling {} is empty (omit it, or list files that bind names)", group.name));
        }
        let mut bindings = Vec::new();
        let mut read: Vec<(&Form, String)> = Vec::new();
        for item in items {
            let Some(rel) = self.name(item) else {
                self.problem(item, &format!("an element of :names-bound-in of confined-spelling {} must be a path string", group.name));
                continue;
            };
            if rel.is_empty() || rel.starts_with('/') || rel.split('/').any(|p| p == "..") || !rel.ends_with(".hy") {
                self.problem(item, &format!("{} in :names-bound-in of confined-spelling {} must be the path of a Hy file from the repo root (not starting with / and not containing ..; .hy)", rel, group.name));
                continue;
            }
            if !group.except.iter().any(|glob| super::paths::glob_matches(glob, &rel)) {
                self.problem(item, &format!("{} in :names-bound-in of confined-spelling {} does not match :except (a file that binds names must be a file allowed to write the spelling)", rel, group.name));
                continue;
            }
            let path = self.root.join(&rel);
            match std::fs::read_to_string(&path) {
                Ok(source) => match super::bound_names::bindings_of(&rel, &source) {
                    Some(found) => {
                        bindings.extend(found);
                        read.push((item, rel.clone()));
                    }
                    None => self.problem(item, &format!("{} in :names-bound-in of confined-spelling {} has a parenthesis or string that is not closed", rel, group.name)),
                },
                Err(error) => self.problem(item, &format!("cannot read {} ({}) in :names-bound-in of confined-spelling {}: {}", rel, path.display(), group.name, error)),
            }
            group.names_bound_in.push(rel);
        }
        let derived = super::bound_names::names_bound_to(&group.patterns, &bindings);
        for (item, rel) in read {
            if !derived.iter().any(|name| name.origin == rel) {
                self.problem(item, &format!("{} in :names-bound-in of confined-spelling {} has no name bound to a value matching the group's spelling", rel, group.name));
            }
        }
        for name in derived {
            group.patterns.extend(name.patterns.iter().cloned());
            group.bound_names.push(name.name);
        }
    }

    /// Reads `[(effect-census "name" :files [..] :effects [..] :base "EffectBase" :why "…") …]` (:files, :effects, and :why are required —
    /// :base defaults to EffectBase if omitted).
    fn effect_census(&mut self, value: &Form) -> Vec<EffectCensus> {
        let shape = "(effect-census \"name\" :files [..] :effects [..] :base \"EffectBase\" :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":effect-census must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<EffectCensus> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("effect-census"));
            let Some((name, head)) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f).map(|n| (n, *f))) else {
                self.problem(entry, &format!("an element of :effect-census must be {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let range = self.lines.range(head.span.start, head.span.end);
            let mut census =
                EffectCensus { name, files: Vec::new(), effects: Vec::new(), base: "EffectBase".to_string(), why: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":files" => census.files = self.path_globs(field, ":files"),
                    ":effects" => census.effects = self.names(field, ":effects"),
                    ":base" => census.base = self.required_string(field, ":base").unwrap_or_default(),
                    ":why" => census.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "effect-census"),
                }
            }
            if census.files.is_empty() {
                self.problem(entry, &format!("effect-census {} has no :files (list the files that declare effects)", census.name));
            }
            if census.effects.is_empty() {
                self.problem(entry, &format!("effect-census {} has no :effects (list the effects)", census.name));
            }
            if census.base.trim().is_empty() {
                self.problem(entry, &format!(":base of effect-census {} is empty", census.name));
            }
            if census.why.trim().is_empty() {
                self.problem(entry, &format!("effect-census {} has no :why (why the list is closed)", census.name));
            }
            if out.iter().any(|c| c.name == census.name) {
                self.problem(entry, &format!("effect-census {} is declared twice", census.name));
                continue;
            }
            out.push(census);
        }
        out
    }

    /// Reads `[(field-holders "name" :type "T" :files [..] :classes [..] :holders [..] :why "…") …]` (:type, :files, :holders, and :why are required —
    /// :holders may be an empty list = no class has one. :classes may be omitted = everything directly under the module. If :classes is written, :holders are
    /// names within it).
    fn field_holders(&mut self, value: &Form) -> Vec<FieldHolders> {
        let shape = "(field-holders \"name\" :type \"T\" :files [..] :classes [..] :holders [..] :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":field-holders must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<FieldHolders> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("field-holders"));
            let Some((name, head)) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f).map(|n| (n, *f))) else {
                self.problem(entry, &format!("an element of :field-holders must be {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let range = self.lines.range(head.span.start, head.span.end);
            let mut type_name = String::new();
            let mut files: Vec<String> = Vec::new();
            let mut classes: Vec<String> = Vec::new();
            let mut holders: Option<Vec<String>> = None;
            let mut why = String::new();
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":type" => type_name = self.required_string(field, ":type").unwrap_or_default(),
                    ":files" => files = self.path_globs(field, ":files"),
                    ":classes" => classes = self.names(field, ":classes"),
                    ":holders" => holders = Some(self.names(field, ":holders")),
                    ":why" => why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "field-holders"),
                }
            }
            let mut complete = true;
            if type_name.trim().is_empty() {
                self.problem(entry, &format!("field-holders {} has no :type (the type spelling to count)", name));
                complete = false;
            }
            if files.is_empty() {
                self.problem(entry, &format!("field-holders {} has no :files (a declaration with no files to count is not placed)", name));
                complete = false;
            }
            if holders.is_none() {
                self.problem(entry, &format!("field-holders {} has no :holders (if no class may have one, write [])", name));
                complete = false;
            }
            if why.trim().is_empty() {
                self.problem(entry, &format!("field-holders {} has no :why (why this roster)", name));
            }
            let holders = holders.unwrap_or_default();
            if !classes.is_empty() {
                for holder in holders.iter().filter(|h| !classes.contains(*h)) {
                    self.problem(entry, &format!("{} in :holders of field-holders {} is not in :classes", holder, name));
                    complete = false;
                }
            }
            if out.iter().any(|d| d.name == name) {
                self.problem(entry, &format!("field-holders {} is declared twice", name));
                continue;
            }
            if complete {
                out.push(FieldHolders { name, type_name, files, classes, holders, why, range });
            }
        }
        out
    }

    /// Reads `[(counted-spelling "name" :pattern r"…" :files [..] :within [..] :count N :why "…") …]` (:pattern, :files, :why, and
    /// exactly one of :count or :at-least are required — :within may be omitted = count across all of :files).
    fn counted_spellings(&mut self, value: &Form) -> Vec<CountedSpelling> {
        let shape = "(counted-spelling \"name\" :pattern r\"…\" :files [..] :within [..] :count N | :at-least N :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":counted-spellings must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<CountedSpelling> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("counted-spelling"));
            let Some((name, head)) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f).map(|n| (n, *f))) else {
                self.problem(entry, &format!("an element of :counted-spellings must be {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let range = self.lines.range(head.span.start, head.span.end);
            let mut pattern: Option<String> = None;
            let mut files: Vec<String> = Vec::new();
            let mut within: Vec<String> = Vec::new();
            let mut wanted: Vec<WantedCount> = Vec::new();
            let mut why = String::new();
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":pattern" => {
                        if let Some(text) = self.pattern(field, ":pattern") {
                            match check_regex(&text) {
                                Ok(_) => pattern = Some(text),
                                Err(error) => self.problem(field, &format!("cannot read the regular expression {} of counted-spelling {}: {}", text, name, error)),
                            }
                        }
                    }
                    ":files" => files = self.path_globs(field, ":files"),
                    ":within" => within = self.names(field, ":within"),
                    key @ (":count" | ":at-least") => match self.text(field).parse::<usize>() {
                        Ok(n) => wanted.push(if key == ":count" { WantedCount::Exactly(n) } else { WantedCount::AtLeast(n) }),
                        Err(_) => self.problem(field, &format!("{} of counted-spelling {} must be an integer of 0 or more", key, name)),
                    },
                    ":why" => why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "counted-spelling"),
                }
            }
            let mut complete = true;
            if pattern.is_none() {
                self.problem(entry, &format!("counted-spelling {} has no :pattern", name));
                complete = false;
            }
            if files.is_empty() {
                self.problem(entry, &format!("counted-spelling {} has no :files (a declaration with no files to count is not placed)", name));
                complete = false;
            }
            if wanted.len() != 1 {
                self.problem(entry, &format!("counted-spelling {} must have exactly one of :count or :at-least", name));
                complete = false;
            }
            if why.trim().is_empty() {
                self.problem(entry, &format!("counted-spelling {} has no :why (why this count)", name));
            }
            if out.iter().any(|d| d.name == name) {
                self.problem(entry, &format!("counted-spelling {} is declared twice", name));
                continue;
            }
            if let (true, Some(pattern), Some(&wanted)) = (complete, pattern, wanted.first()) {
                out.push(CountedSpelling { name, pattern, files, within, wanted, why, range, file: self.path.to_path_buf() });
            }
        }
        out
    }

    /// Reads `[(vocabulary-scope "name" :patterns [r"…"] :files [..] :except [..] :instead "…") …]`
    /// (:patterns, :files, :except, and :instead are required — without :except it would be a declaration with no "single point of judgment").
    fn single_point_vocabulary(&mut self, value: &Form) -> Vec<VocabularyScope> {
        let shape = "(vocabulary-scope \"name\" :patterns [r\"…\"] :files [..] :except [..] :instead \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":single-point-vocabulary must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<VocabularyScope> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("vocabulary-scope"));
            let Some(name) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f)) else {
                self.problem(entry, &format!("an element of :single-point-vocabulary must be {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let mut group = VocabularyScope { name, patterns: Vec::new(), files: Vec::new(), except: Vec::new(), instead: String::new() };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":patterns" => match self.bracket(field) {
                        Some(items) => {
                            for item in items {
                                let Some(pattern) = self.pattern(item, ":patterns") else { continue };
                                match check_regex(&pattern) {
                                    Ok(_) => group.patterns.push(pattern),
                                    Err(error) => self.problem(item, &format!("cannot read the regular expression {} of vocabulary-scope {}: {}", pattern, group.name, error)),
                                }
                            }
                        }
                        None => self.problem(field, ":patterns must be a list [r\"…\" …]"),
                    },
                    ":files" => group.files = self.path_globs(field, ":files"),
                    ":except" => group.except = self.path_globs(field, ":except"),
                    ":instead" => group.instead = self.required_string(field, ":instead").unwrap_or_default(),
                    _ => self.unknown_key(key, "vocabulary-scope"),
                }
            }
            if group.patterns.is_empty() {
                self.problem(entry, &format!("vocabulary-scope {} has no :patterns", group.name));
            }
            if group.files.is_empty() {
                self.problem(entry, &format!("vocabulary-scope {} has no :files (a group with no files to search is not placed)", group.name));
            }
            if group.except.is_empty() {
                self.problem(entry, &format!("vocabulary-scope {} has no :except (the single point of judgment)", group.name));
            }
            if group.instead.trim().is_empty() {
                self.problem(entry, &format!("vocabulary-scope {} has no :instead (how to fix it)", group.name));
            }
            if out.iter().any(|g| g.name == group.name) {
                self.problem(entry, &format!("vocabulary-scope {} is declared twice", group.name));
                continue;
            }
            out.push(group);
        }
        out
    }

    /// Reads `[(retired-calls "name" :calls [..] :files [..] :except [..] :instead "…") …]` (:calls, :files, and :instead are required).
    fn retired_calls(&mut self, value: &Form) -> Vec<RetiredCalls> {
        let shape = "(retired-calls \"name\" :calls [..] :files [..] :except [..]? :instead \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":retired-calls must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<RetiredCalls> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("retired-calls"));
            let Some(name) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f)) else {
                self.problem(entry, &format!("an element of :retired-calls must be {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let mut group = RetiredCalls { name, calls: Vec::new(), files: Vec::new(), except: Vec::new(), instead: String::new() };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":calls" => group.calls = self.names(field, ":calls"),
                    ":files" => group.files = self.path_globs(field, ":files"),
                    ":except" => group.except = self.path_globs(field, ":except"),
                    ":instead" => group.instead = self.required_string(field, ":instead").unwrap_or_default(),
                    _ => self.unknown_key(key, "retired-calls"),
                }
            }
            if group.calls.is_empty() || group.calls.iter().any(|c| c.is_empty() || c.contains(char::is_whitespace)) {
                self.problem(entry, &format!(":calls of retired-calls {} must be a non-empty list of call-head spellings (containing no whitespace)", group.name));
            }
            if group.files.is_empty() {
                self.problem(entry, &format!("retired-calls {} has no :files", group.name));
            }
            if group.instead.trim().is_empty() {
                self.problem(entry, &format!("retired-calls {} has no :instead (what to use instead)", group.name));
            }
            if out.iter().any(|g| g.name == group.name) {
                self.problem(entry, &format!("retired-calls {} is declared twice", group.name));
                continue;
            }
            out.push(group);
        }
        out
    }

    /// Reads `:business-fakes {…}` (:simulation and :business-modules are required).
    fn business_fakes(&mut self, value: &Form) -> Option<BusinessFakes> {
        let Some(entries) = self.brace(value) else {
            self.problem(value, ":business-fakes must be a dictionary {:simulation [..] :assembly [..] :tests [..] :skip [..] :sets [..] … :business-modules [..]}");
            return None;
        };
        let mut decl = BusinessFakes::default();
        for (key, field) in self.pairs(&entries) {
            match self.text(key) {
                ":simulation" => decl.simulation = self.path_globs(field, ":business-fakes :simulation"),
                ":assembly" => decl.assembly = self.path_globs(field, ":business-fakes :assembly"),
                ":tests" => decl.tests = self.path_globs(field, ":business-fakes :tests"),
                ":skip" => decl.skip = self.path_globs(field, ":business-fakes :skip"),
                ":production" => decl.production = self.path_globs(field, ":business-fakes :production"),
                ":sets" => decl.sets = self.path_globs(field, ":business-fakes :sets"),
                ":simulation-prefix" => decl.simulation_prefix = self.required_string(field, ":business-fakes :simulation-prefix"),
                ":production-prefix" => decl.production_prefix = self.required_string(field, ":business-fakes :production-prefix"),
                ":entry-string-modules" => decl.entry_string_modules = self.names(field, ":business-fakes :entry-string-modules"),
                ":entry-string-files" => decl.entry_string_files = self.path_globs(field, ":business-fakes :entry-string-files"),
                ":business-modules" => decl.business_modules = self.names(field, ":business-fakes :business-modules"),
                ":lower-layer-modules" => decl.lower_layer_modules = self.names(field, ":business-fakes :lower-layer-modules"),
                ":external-effects" => decl.external_effects = self.required_string(field, ":business-fakes :external-effects"),
                ":counterexamples" => decl.counterexamples = self.required_string(field, ":business-fakes :counterexamples"),
                ":unserved" => decl.unserved = self.required_string(field, ":business-fakes :unserved"),
                ":lower-layer-passages" => decl.lower_layer_passages = self.required_string(field, ":business-fakes :lower-layer-passages"),
                ":test-only-fakes" => decl.test_only_fakes = self.required_string(field, ":business-fakes :test-only-fakes"),
                _ => self.unknown_key(key, ":business-fakes"),
            }
        }
        if decl.simulation.is_empty() {
            self.problem(value, ":business-fakes has no :simulation (patterns of the spellings of emulated-environment files)");
        }
        if decl.business_modules.is_empty() {
            self.problem(value, ":business-fakes has no :business-modules (modules that define business effects)");
        }
        Some(decl)
    }

    /// Reads `:environment-branches {:values [..] :flags [..] :layers [..]}` (:layers and either :values or :flags are required).
    fn environment_branches(&mut self, value: &Form) -> Option<EnvironmentBranches> {
        let Some(entries) = self.brace(value) else {
            self.problem(value, ":environment-branches must be a dictionary {:values [\"…\"] :flags [\"…\"] :layers [LAYER …]}");
            return None;
        };
        let mut decl = EnvironmentBranches::default();
        for (key, field) in self.pairs(&entries) {
            match self.text(key) {
                ":values" => decl.values = self.names(field, ":environment-branches :values"),
                ":flags" => decl.flags = self.names(field, ":environment-branches :flags"),
                ":layers" => decl.layers = self.names(field, ":environment-branches :layers"),
                _ => self.unknown_key(key, ":environment-branches"),
            }
        }
        if decl.values.is_empty() && decl.flags.is_empty() {
            self.problem(value, ":environment-branches has neither :values (environment-name values) nor :flags (dry-run flags) — there is no spelling to apply");
        }
        if decl.layers.is_empty() {
            self.problem(value, ":environment-branches has no :layers (the business layers to apply to)");
        }
        Some(decl)
    }

    /// Reads `:semantic-lines [(line "name" :rules [DOEFF201 …] :text "…" :fires [example …]? :silent [example …]?) …]`.
    /// An example is `"<code>"` or `{:code "<code>" :why "…"}` (#1995).
    /// A name, :rules, and a non-empty :text are required. :rules may contain only rules whose Jev question takes a demarcation (SemanticQuestion::LINED); an unknown rule, a rule that
    /// is not a Jev question, and a Jev-question rule that takes no demarcation are settings errors. Writing the same name or the same rule twice, and an example with an empty code or an empty why, are also errors.
    fn semantic_lines(&mut self, value: &Form) -> Vec<SemanticLine> {
        let shape = "(line \"name\" :rules [DOEFF201 …] :text \"…\" :fires [\"<code>\" | {:code \"<code>\" :why \"…\"} …]? :silent [ … ]?)";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":semantic-lines must be a list of {}", shape));
            return Vec::new();
        };
        let lines: Vec<(&Form, SemanticLine)> = entries.into_iter().filter_map(|entry| self.semantic_line(entry, shape).map(|line| (entry, line))).collect();
        for (index, (entry, line)) in lines.iter().enumerate() {
            if lines[..index].iter().any(|(_, earlier)| earlier.name == line.name) {
                self.problem(entry, &format!("demarcation {} is declared twice", line.name));
            }
        }
        lines.into_iter().map(|(_, line)| line).collect()
    }

    /// Reads one demarcation `(line "name" :rules [..] :text "…" :fires [..]? :silent [..]?)` (if unreadable, accumulates a reason and returns None).
    fn semantic_line(&mut self, entry: &Form, shape: &str) -> Option<SemanticLine> {
        let Some(parts) = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("line")) else {
            self.problem(entry, &format!("an element of :semantic-lines must be {}", shape));
            return None;
        };
        let Some(name) = parts.get(1).and_then(|f| self.name(f)).filter(|n| !n.trim().is_empty()) else {
            self.problem(entry, &format!("an element of :semantic-lines has no name — {}", shape));
            return None;
        };
        let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
        let mut rules: Option<Vec<SemanticQuestion>> = None;
        let mut text: Option<String> = None;
        let mut fires = Vec::new();
        let mut silent = Vec::new();
        for (key, field) in self.pairs(&rest) {
            match self.text(key) {
                ":rules" => rules = Some(self.line_rules(field, &name)),
                ":text" => text = self.required_string(field, &format!(":text of demarcation {}", name)),
                ":fires" => fires = self.line_examples(field, &name, ":fires"),
                ":silent" => silent = self.line_examples(field, &name, ":silent"),
                _ => self.unknown_key(key, "line"),
            }
        }
        let rules = match rules {
            Some(rules) if !rules.is_empty() => Some(rules),
            Some(_) => {
                self.problem(entry, &format!("demarcation {} has no rule in :rules that takes a demarcation question (one of DOEFF201, 202, 205)", name));
                None
            }
            None => {
                self.problem(entry, &format!("demarcation {} has no :rules (one of DOEFF201, 202, 205)", name));
                None
            }
        };
        let text = match text {
            Some(text) if !text.trim().is_empty() => Some(text),
            Some(_) => {
                self.problem(entry, &format!(":text of demarcation {} is empty", name));
                None
            }
            None => {
                self.problem(entry, &format!("demarcation {} has no :text (the demarcation text)", name));
                None
            }
        };
        Some(SemanticLine { name, rules: rules?, text: text?, fires, silent })
    }

    /// Reads the :rules of a demarcation — looks up rule IDs among the Jev questions that take a demarcation (an ID that cannot be looked up and a duplicate accumulate a reason).
    fn line_rules(&mut self, field: &Form, line: &str) -> Vec<SemanticQuestion> {
        let ids = self.names(field, &format!(":rules of demarcation {}", line));
        let rules: Vec<SemanticQuestion> = ids
            .iter()
            .filter_map(|id| {
                let Some(rule) = ProjectRule::parse(id) else {
                    self.problem(field, &format!("{} in :rules of demarcation {} is an unknown rule", id, line));
                    return None;
                };
                let question = SemanticQuestion::LINED.iter().copied().find(|question| question.id() == rule.id());
                match question {
                    Some(_) => {}
                    None if rule.is_semantic() => {
                        self.problem(field, &format!("{} in :rules of demarcation {} takes no demarcation (only the questions of DOEFF201, 202, 205 do)", id, line))
                    }
                    None => self.problem(field, &format!("{} in :rules of demarcation {} is not a Jev-question rule (one of DOEFF201, 202, 205)", id, line)),
                }
                question
            })
            .collect();
        for (index, rule) in rules.iter().enumerate() {
            if rules[..index].contains(rule) {
                self.problem(field, &format!("{} in :rules of demarcation {} is written twice", rule.id(), line));
            }
        }
        rules
    }

    /// A list of demarcation examples (an element = a code string or a pair `{:code "<code>" :why "…"}` — an unreadable element, an empty code, or an empty why accumulates a reason and is dropped).
    fn line_examples(&mut self, field: &Form, line: &str, key: &str) -> Vec<LineExample> {
        let what = format!("{} of demarcation {}", key, line);
        let Some(items) = self.bracket(field) else {
            self.problem(field, &format!("{} must be a list of examples [\"<code>\" | {{:code \"<code>\" :why \"…\"}} …]", what));
            return Vec::new();
        };
        items.into_iter().filter_map(|item| self.line_example(item, &what)).collect()
    }

    /// One demarcation example (`"<code>"` or `{:code "<code>" :why "…"}` — :code is required and :why may be omitted).
    fn line_example(&mut self, item: &Form, what: &str) -> Option<LineExample> {
        let (code, why) = match (self.string(item), self.brace(item)) {
            (Some(code), _) => (Some(code), None),
            (None, Some(entries)) => {
                let fields: Vec<(&Form, Option<String>)> = self
                    .pairs(&entries)
                    .into_iter()
                    .filter_map(|(key, value)| match self.text(key) {
                        ":code" | ":why" => Some((key, self.required_string(value, &format!("{} of {}", self.text(key), what)))),
                        _ => {
                            self.unknown_key(key, &format!("an example of {}", what));
                            None
                        }
                    })
                    .collect();
                let field = |name: &str| fields.iter().find(|(key, _)| self.text(key) == name).and_then(|(_, value)| value.clone());
                let (code, why) = (field(":code"), field(":why"));
                if code.is_none() && !fields.iter().any(|(key, _)| self.text(key) == ":code") {
                    self.problem(item, &format!("an example of {} has no :code", what));
                }
                if why.as_deref().is_some_and(|why| why.trim().is_empty()) {
                    self.problem(item, &format!(":why of an example of {} is empty", what));
                    return None;
                }
                (code, why)
            }
            (None, None) => {
                self.problem(item, &format!("an element of {} must be a code string or a pair {{:code \"<code>\" :why \"…\"}}", what));
                return None;
            }
        };
        match code {
            Some(code) if !code.trim().is_empty() => Some(LineExample { code, why }),
            Some(_) => {
                self.problem(item, &format!("{} has an empty code", what));
                None
            }
            None => None,
        }
    }

    /// Reads `:assembly-shape {…}` (:translation-point, :translations, :translation-layer, and :intent-layer are required).
    fn assembly_shape(&mut self, value: &Form) -> Option<AssemblyShape> {
        let shape = ":assembly-shape must be a dictionary {:translation-point \"…-*-…\" :retired-function \"…\" :translations \"…\" :translation-layer \"…\" :intent-layer \"…\"}";
        let Some(entries) = self.brace(value) else {
            self.problem(value, shape);
            return None;
        };
        let mut decl = AssemblyShape::default();
        for (key, field) in self.pairs(&entries) {
            match self.text(key) {
                ":translation-point" => decl.translation_point = self.required_string(field, ":assembly-shape :translation-point").unwrap_or_default(),
                ":retired-function" => decl.retired_function = self.required_string(field, ":assembly-shape :retired-function").filter(|f| !f.is_empty()),
                ":translations" => decl.translations = self.required_string(field, ":assembly-shape :translations").unwrap_or_default(),
                ":translation-layer" => decl.translation_layer = self.required_string(field, ":assembly-shape :translation-layer").unwrap_or_default(),
                ":intent-layer" => decl.intent_layer = self.required_string(field, ":assembly-shape :intent-layer").unwrap_or_default(),
                _ => self.unknown_key(key, ":assembly-shape"),
            }
        }
        if !decl.translation_point.is_empty() && decl.translation_point.matches('*').count() != 1 {
            self.problem(value, ":assembly-shape :translation-point's name pattern must have exactly one `*` (e.g. \"with-*-translation\")");
        }
        for (name, spelled) in [
            (":translation-point", &decl.translation_point),
            (":translations", &decl.translations),
            (":translation-layer", &decl.translation_layer),
            (":intent-layer", &decl.intent_layer),
        ] {
            if spelled.is_empty() {
                self.problem(value, &format!(":assembly-shape has no {}", name));
            }
        }
        Some(decl)
    }

    /// Reads `{:files [..] :exclude [..] :store-names [..] :store-suffixes [..] :keep-mark "…" :value-types [..]}` (:files is required).
    fn handler_arguments(&mut self, value: &Form) -> Option<HandlerArguments> {
        let Some(entries) = self.brace(value) else {
            self.problem(value, ":handler-arguments must be a dictionary {:files [..] :exclude [..] :store-names [..] :store-suffixes [..] :keep-mark \"…\" :value-types [..]}");
            return None;
        };
        let mut decl = HandlerArguments::default();
        for (key, field) in self.pairs(&entries) {
            match self.text(key) {
                ":files" => decl.files = self.names(field, ":handler-arguments :files"),
                ":exclude" => decl.exclude = self.names(field, ":handler-arguments :exclude"),
                ":store-names" => decl.store_names = self.names(field, ":handler-arguments :store-names"),
                ":store-suffixes" => decl.store_suffixes = self.names(field, ":handler-arguments :store-suffixes"),
                ":keep-mark" => decl.keep_mark = self.required_string(field, ":handler-arguments :keep-mark").filter(|m| !m.is_empty()),
                ":value-types" => decl.value_types = self.names(field, ":handler-arguments :value-types"),
                _ => self.unknown_key(key, ":handler-arguments"),
            }
        }
        if decl.files.is_empty() {
            self.problem(value, ":handler-arguments has no :files (patterns of the spellings of files to judge)");
        }
        Some(decl)
    }

    /// Reads `[(blind "module:NAME" :forbid-words [..] :no-imports True :allow-requires [..] :why "…") …]`
    /// (:why, and either :forbid-words or :no-imports True, are required).
    fn blind_definitions(&mut self, value: &Form) -> Vec<BlindDefinition> {
        let shape = "(blind \"module:NAME\" :forbid-words [..]? :no-imports True? :allow-requires [..]? :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":blind-definitions must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<BlindDefinition> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("blind"));
            let Some(parts) = parts else {
                self.problem(entry, &format!("an element of :blind-definitions must be {}", shape));
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "blind has no \"module:NAME\"");
                continue;
            };
            let Some(definition) = self.definition_ref(head, "blind") else { continue };
            let range = self.lines.range(head.span.start, head.span.end);
            let mut blind = BlindDefinition { definition, forbid_words: Vec::new(), no_imports: false, allow_requires: Vec::new(), why: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":forbid-words" => blind.forbid_words = self.names(field, ":forbid-words"),
                    ":no-imports" => match self.symbol(field) {
                        Some("True") => blind.no_imports = true,
                        Some("False") => blind.no_imports = false,
                        _ => self.problem(field, ":no-imports must be True or False"),
                    },
                    ":allow-requires" => blind.allow_requires = self.names(field, ":allow-requires"),
                    ":why" => blind.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "blind"),
                }
            }
            let spelling = blind.definition.spelling();
            if blind.forbid_words.iter().any(|w| w.is_empty()) {
                self.problem(entry, &format!("blind {} has an empty spelling in :forbid-words", spelling));
            }
            if blind.forbid_words.is_empty() && !blind.no_imports {
                self.problem(entry, &format!("blind {} has neither :forbid-words nor :no-imports True (a declaration that requires nothing is not placed)", spelling));
            }
            if !blind.allow_requires.is_empty() && !blind.no_imports {
                self.problem(entry, &format!(":allow-requires of blind {} is effective only with :no-imports True", spelling));
            }
            if blind.why.trim().is_empty() {
                self.problem(entry, &format!("blind {} has no :why (why those materials are not read)", spelling));
            }
            if out.iter().any(|b| b.definition == blind.definition) {
                self.problem(entry, &format!("blind {} is declared twice", spelling));
                continue;
            }
            out.push(blind);
        }
        out
    }

    /// Reads `[(call-site "head" :files [..] :except [..]? :sites [(site "module:NAME" :count N? :parent "head"? :branch "name"?) …] :why "…") …]`
    /// (:files, :sites, and :why are required).
    fn call_sites(&mut self, value: &Form) -> Vec<CallSite> {
        let shape = "(call-site \"head\" :files [..] :except [..]? :sites [(site \"module:NAME\" :count N? :parent \"head\"? :branch \"name\"?) …] :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":call-sites must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<CallSite> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("call-site"));
            let Some(parts) = parts else {
                self.problem(entry, &format!("an element of :call-sites must be {}", shape));
                continue;
            };
            let Some(head) = parts.get(1).and_then(|h| self.name(h)).filter(|h| !h.is_empty()) else {
                self.problem(entry, "call-site has no head spelling");
                continue;
            };
            let range = self.lines.range(entry.span.start, entry.span.end);
            let mut declared = CallSite { head, files: Vec::new(), except: Vec::new(), sites: Vec::new(), why: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":files" => declared.files = self.names(field, ":files"),
                    ":except" => declared.except = self.names(field, ":except"),
                    ":sites" => declared.sites = self.call_site_sites(field),
                    ":why" => declared.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "call-site"),
                }
            }
            let head = declared.head.clone();
            if declared.files.is_empty() {
                self.problem(entry, &format!("call-site {} has no :files (a declaration with no files to search is not placed)", head));
            }
            if declared.sites.is_empty() {
                self.problem(entry, &format!("call-site {} has no :sites (a declaration with no places where calls are allowed is not placed)", head));
            }
            if declared.why.trim().is_empty() {
                self.problem(entry, &format!("call-site {} has no :why (why only here)", head));
            }
            if out.iter().any(|d| d.head == declared.head) {
                self.problem(entry, &format!("call-site {} is declared twice", head));
                continue;
            }
            out.push(declared);
        }
        out
    }

    /// Reads `[(broad-catch "name" :files [..] :except [..]? :carriers [(carrier "module:NAME" :event "event") …]? :why "…") …]`
    /// (:files and :why are required — a group that omits :carriers allows no broad catch at all).
    fn broad_catches(&mut self, value: &Form) -> Vec<BroadCatch> {
        let shape = "(broad-catch \"name\" :files [..] :except [..]? :carriers [(carrier \"module:NAME\" :event \"event\") …]? :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":broad-catches must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<BroadCatch> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("broad-catch"));
            let Some((name, head)) = parts.as_ref().and_then(|p| p.get(1)).and_then(|f| self.name(f).map(|n| (n, *f))) else {
                self.problem(entry, &format!("an element of :broad-catches must be {}", shape));
                continue;
            };
            let parts = parts.unwrap_or_default();
            let range = self.lines.range(head.span.start, head.span.end);
            let mut declared =
                BroadCatch { name, files: Vec::new(), except: Vec::new(), carriers: Vec::new(), why: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":files" => declared.files = self.names(field, ":files"),
                    ":except" => declared.except = self.names(field, ":except"),
                    ":carriers" => declared.carriers = self.broad_catch_carriers(field),
                    ":why" => declared.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "broad-catch"),
                }
            }
            if declared.files.is_empty() {
                self.problem(entry, &format!("broad-catch {} has no :files (a declaration with no files to search is not placed)", declared.name));
            }
            if declared.why.trim().is_empty() {
                self.problem(entry, &format!("broad-catch {} has no :why (why broad catches are not placed)", declared.name));
            }
            if out.iter().any(|d| d.name == declared.name) {
                self.problem(entry, &format!("broad-catch {} is declared twice", declared.name));
                continue;
            }
            out.push(declared);
        }
        out
    }

    /// Reads `[(carrier "module:NAME" :event "event") …]` (:event is required).
    fn broad_catch_carriers(&mut self, value: &Form) -> Vec<BroadCatchCarrier> {
        let shape = "(carrier \"module:NAME\" :event \"event\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":carriers must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<BroadCatchCarrier> = Vec::new();
        for entry in entries {
            let Some(parts) = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("carrier")) else {
                self.problem(entry, &format!("an element of :carriers must be {}", shape));
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "carrier has no \"module:NAME\"");
                continue;
            };
            let Some(definition) = self.definition_ref(head, "carrier") else { continue };
            let range = self.lines.range(head.span.start, head.span.end);
            let mut event: Option<String> = None;
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":event" => event = self.required_string(field, ":event"),
                    _ => self.unknown_key(key, "carrier"),
                }
            }
            let Some(event) = event.filter(|e| !e.trim().is_empty()) else {
                self.problem(entry, &format!("carrier {} has no :event (the event that carries the exception)", definition.spelling()));
                continue;
            };
            if out.iter().any(|c| c.definition == definition) {
                self.problem(entry, &format!("carrier {} is written twice", definition.spelling()));
                continue;
            }
            out.push(BroadCatchCarrier { definition, event, range });
        }
        out
    }

    /// Reads `[(site "module:NAME" :count N :parent "head" :branch "name") …]`.
    fn call_site_sites(&mut self, value: &Form) -> Vec<CallSiteSite> {
        let shape = "(site \"module:NAME\" :count N? :parent \"head\"? :branch \"name\"?)";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":sites must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<CallSiteSite> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("site"));
            let Some(parts) = parts else {
                self.problem(entry, &format!("an element of :sites must be {}", shape));
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "site has no \"module:NAME\"");
                continue;
            };
            let Some(definition) = self.definition_ref(head, "site") else { continue };
            let range = self.lines.range(head.span.start, head.span.end);
            let mut site = CallSiteSite { definition, count: None, parent: None, branch: None, range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":count" => match self.text(field).parse::<usize>() {
                        Ok(count) => site.count = Some(count),
                        Err(_) => self.problem(field, ":count must be an integer of 0 or more"),
                    },
                    ":parent" => site.parent = self.required_string(field, ":parent"),
                    ":branch" => site.branch = self.required_string(field, ":branch"),
                    _ => self.unknown_key(key, "site"),
                }
            }
            if out.iter().any(|s| s.definition == site.definition) {
                self.problem(entry, &format!("site {} is written twice", site.definition.spelling()));
                continue;
            }
            out.push(site);
        }
        out
    }

    /// Reads `[(allowed-heads "module:NAME" :heads [..] :why "…") …]` (:heads and :why are required).
    fn allowed_heads(&mut self, value: &Form) -> Vec<AllowedHeads> {
        let shape = "(allowed-heads \"module:NAME\" :heads [..] :why \"…\")";
        let Some(entries) = self.bracket(value) else {
            self.problem(value, &format!(":allowed-heads must be a list of {}", shape));
            return Vec::new();
        };
        let mut out: Vec<AllowedHeads> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("allowed-heads"));
            let Some(parts) = parts else {
                self.problem(entry, &format!("an element of :allowed-heads must be {}", shape));
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "allowed-heads has no \"module:NAME\"");
                continue;
            };
            let Some(definition) = self.definition_ref(head, "allowed-heads") else { continue };
            let range = self.lines.range(head.span.start, head.span.end);
            let mut declared = AllowedHeads { definition, heads: Vec::new(), why: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":heads" => declared.heads = self.names(field, ":heads"),
                    ":why" => declared.why = self.required_string(field, ":why").unwrap_or_default(),
                    _ => self.unknown_key(key, "allowed-heads"),
                }
            }
            let spelling = declared.definition.spelling();
            if declared.heads.is_empty() {
                self.problem(entry, &format!("allowed-heads {} has no :heads (no definition calls nothing — list also the definition's own head, such as defk)", spelling));
            }
            if declared.heads.iter().any(|h| h.is_empty()) {
                self.problem(entry, &format!("allowed-heads {} has an empty spelling in :heads", spelling));
            }
            if declared.why.trim().is_empty() {
                self.problem(entry, &format!("allowed-heads {} has no :why (why only these heads)", spelling));
            }
            if out.iter().any(|d| d.definition == declared.definition) {
                self.problem(entry, &format!("allowed-heads {} is declared twice", spelling));
                continue;
            }
            out.push(declared);
        }
        out
    }

    /// The allowlist `[(world-handler "module:NAME" :touches [..] :answers [..] :wraps [..]) …]` is read.
    fn world_handlers(&mut self, value: &Form) -> Vec<WorldHandler> {
        let entries = self.bracket(value).unwrap_or_else(|| {
            self.problem(value, ":world-handlers must be a list of (world-handler \"module:NAME\" :touches [..])");
            Vec::new()
        });
        let mut out: Vec<WorldHandler> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("world-handler"));
            let Some(parts) = parts else {
                self.problem(entry, "an element of :world-handlers must be (world-handler \"module:NAME\" :touches [..] :answers [..]? :wraps [..]?)");
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "world-handler has no \"module:NAME\"");
                continue;
            };
            let Some(definition) = self.definition_ref(head, "world-handler") else { continue };
            let range = self.lines.range(head.span.start, head.span.end);
            let mut handler =
                WorldHandler { definition, touches: Vec::new(), answers: Vec::new(), wraps: Vec::new(), contract_test: ContractTest::Required, range };
            let mut touches_given = false;
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":touches" => {
                        touches_given = true;
                        for word in self.names(field, ":touches") {
                            match WorldTouch::parse(&word) {
                                Some(touch) if handler.touches.contains(&touch) => {
                                    self.problem(field, &format!("{} in :touches of world-handler {} is written twice", word, handler.definition.spelling()))
                                }
                                Some(touch) => handler.touches.push(touch),
                                None => {
                                    let words: Vec<&str> = WorldTouch::ALL.iter().map(|t| t.name()).collect();
                                    self.problem(field, &format!("{} in :touches of world-handler {} is not in the vocabulary (one of {})", word, handler.definition.spelling(), words.join(", ")))
                                }
                            }
                        }
                    }
                    ":answers" => handler.answers = self.names(field, ":answers"),
                    ":wraps" => match self.bracket(field) {
                        Some(items) => {
                            for item in items {
                                if let Some(wrapped) = self.definition_ref(item, ":wraps") {
                                    if handler.wraps.contains(&wrapped) {
                                        self.problem(item, &format!("{} in :wraps is written twice", wrapped.spelling()));
                                    } else {
                                        handler.wraps.push(wrapped);
                                    }
                                }
                            }
                        }
                        None => self.problem(field, ":wraps must be a list [\"module:NAME\" …]"),
                    },
                    // A handler that does not require an edge test (DOEFF137) — the value is only (none :doeff-test|:repo-test "file::NAME") or the symbol none (no reason — fires
                    // when judged) (other values are not silently let through as misreadings; #1796).
                    ":contract-test" => {
                        if let Some(declared) = self.contract_test(field, &format!("world-handler {}", handler.definition.spelling())) {
                            handler.contract_test = declared;
                        }
                    }
                    _ => self.unknown_key(key, "world-handler"),
                }
            }
            if !touches_given || handler.touches.is_empty() {
                self.problem(entry, &format!("world-handler {} has no :touches (a definition that touches nothing is not placed in the allowlist)", handler.definition.spelling()));
            }
            if out.iter().any(|h| h.definition == handler.definition) {
                self.problem(entry, &format!("world-handler {} is declared twice", handler.definition.spelling()));
                continue;
            }
            out.push(handler);
        }
        out
    }

    /// The roster of counterparts `[(stand-in "module:NAME" :real "module:NAME" :contract-test …?) …]` is read (DOEFF137).
    fn stand_ins(&mut self, value: &Form) -> Vec<StandIn> {
        let entries = self.bracket(value).unwrap_or_else(|| {
            self.problem(value, ":stand-ins must be a list of (stand-in \"module:NAME\" :real \"module:NAME\")");
            Vec::new()
        });
        let mut out: Vec<StandIn> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("stand-in"));
            let Some(parts) = parts else {
                self.problem(entry, "an element of :stand-ins must be (stand-in \"module:NAME\" :real \"module:NAME\" :contract-test …?)");
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "stand-in has no \"module:NAME\"");
                continue;
            };
            let Some(definition) = self.definition_ref(head, "stand-in") else { continue };
            let range = self.lines.range(head.span.start, head.span.end);
            let subject = format!("stand-in {}", definition.spelling());
            let mut real: Option<DefinitionRef> = None;
            let mut contract_test = ContractTest::Required;
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":real" => real = self.definition_ref(field, ":real"),
                    ":contract-test" => {
                        if let Some(declared) = self.contract_test(field, &subject) {
                            contract_test = declared;
                        }
                    }
                    _ => self.unknown_key(key, "stand-in"),
                }
            }
            let Some(real) = real else {
                self.problem(entry, &format!("{} has no :real \"module:NAME\" (the real handler the counterpart stands in for)", subject));
                continue;
            };
            if real == definition {
                self.problem(entry, &format!("{} has a :real that is the counterpart itself (write the real handler)", subject));
                continue;
            }
            if out.iter().any(|s| s.definition == definition && s.real == real) {
                self.problem(entry, &format!("the pair of {} and real {} is declared twice", subject, real.spelling()));
                continue;
            }
            out.push(StandIn { definition, real, contract_test, range });
        }
        out
    }

    /// Reads the value of `:contract-test` — the symbol none is without a reason, and `(none :doeff-test "file::NAME")` / `(none :repo-test "file::NAME")` are with a reason.
    /// Other shapes, a wrong number of keys, and a wrong node id shape accumulate a reason and return None (DOEFF137). subject = the spelling of the roster element
    /// (`world-handler NAME` / `stand-in NAME`).
    fn contract_test(&mut self, field: &Form, subject: &str) -> Option<ContractTest> {
        let shape = format!(
            "{} :contract-test must be (none {} \"file::TEST-NAME\") or the symbol none (a none with no reason fires DOEFF137)",
            subject,
            ContractTestWaiver::KEYS.join("|")
        );
        if self.symbol(field) == Some("none") {
            return Some(ContractTest::NoneWithoutReason);
        }
        let Some(parts) = self.paren(field).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("none")) else {
            self.problem(field, &shape);
            return None;
        };
        let rest: Vec<&Form> = parts.iter().skip(1).copied().collect();
        let pairs = self.pairs(&rest);
        let [(key, value)] = pairs.as_slice() else {
            self.problem(field, &format!("{} (exactly one reason)", shape));
            return None;
        };
        let Some(node) = self.string(value).as_deref().and_then(TestNodeId::parse) else {
            self.problem(field, &format!("{} (the test name is a .hy / .py path from the repo root, then ::, then the name — e.g. \"packages/x/tests/test_y.hy::test-z\")", shape));
            return None;
        };
        match self.text(key) {
            ":doeff-test" => Some(ContractTest::Waived(ContractTestWaiver::DoeffContractTest(node))),
            ":repo-test" => Some(ContractTest::Waived(ContractTestWaiver::CoveredByRepoTest(node))),
            _ => {
                self.problem(key, &shape);
                None
            }
        }
    }

    /// A list of boundary components `[(boundary-part "module" :touches [..] :reason "…") …]` is read.
    fn boundary_parts(&mut self, value: &Form) -> Vec<BoundaryPart> {
        let entries = self.bracket(value).unwrap_or_else(|| {
            self.problem(value, ":boundary-parts must be a list of (boundary-part \"module\" :touches [..] :reason \"…\")");
            Vec::new()
        });
        let mut out: Vec<BoundaryPart> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("boundary-part"));
            let Some(parts) = parts else {
                self.problem(entry, "an element of :boundary-parts must be (boundary-part \"module\" :touches [..] :reason \"…\")");
                continue;
            };
            let Some(head) = parts.get(1) else {
                self.problem(entry, "boundary-part has no \"module\"");
                continue;
            };
            let Some(module) = self.required_string(head, "module of boundary-part") else { continue };
            if module.is_empty() || module.contains(':') || module.contains('/') {
                self.problem(head, &format!("{} of boundary-part must be a dotted module spelling (not containing : or /)", module));
                continue;
            }
            let range = self.lines.range(head.span.start, head.span.end);
            let mut part = BoundaryPart { module, touches: Vec::new(), reason: String::new(), range };
            let rest: Vec<&Form> = parts.iter().skip(2).copied().collect();
            for (key, field) in self.pairs(&rest) {
                match self.text(key) {
                    ":touches" => {
                        for word in self.names(field, ":touches") {
                            match WorldTouch::parse(&word) {
                                Some(touch) if part.touches.contains(&touch) => {
                                    self.problem(field, &format!("{} in :touches of boundary-part {} is written twice", word, part.module))
                                }
                                Some(touch) => part.touches.push(touch),
                                None => {
                                    let words: Vec<&str> = WorldTouch::ALL.iter().map(|t| t.name()).collect();
                                    self.problem(field, &format!("{} in :touches of boundary-part {} is not in the vocabulary (one of {})", word, part.module, words.join(", ")))
                                }
                            }
                        }
                    }
                    ":reason" => part.reason = self.required_string(field, ":reason of boundary-part").unwrap_or_default(),
                    _ => self.unknown_key(key, "boundary-part"),
                }
            }
            if part.touches.is_empty() {
                self.problem(entry, &format!("boundary-part {} has no :touches (name the kinds allowed)", part.module));
            }
            if part.reason.trim().is_empty() {
                self.problem(entry, &format!("boundary-part {} has no :reason (why real I/O itself is its job)", part.module));
            }
            if out.iter().any(|p| p.module == part.module) {
                self.problem(entry, &format!("boundary-part {} is declared twice", part.module));
                continue;
            }
            out.push(part);
        }
        out
    }

    /// Reads the table of outside writers `{"<store>:<name>" "writer" …}` (the declaration that decides which repos DOEFF207 applies to). The spelling of a pair is a shape with exactly one `:` and
    /// non-empty on both sides; a writer's description is a non-empty string. Two of the same pair accumulates a reason.
    fn outside_writers(&mut self, value: &Form) -> Vec<OutsideWriter> {
        let Some(items) = self.brace(value) else {
            self.problem(value, ":outside-writers must be a dict {\"<store>:<name>\" \"writer\" …}");
            return Vec::new();
        };
        if items.len() % 2 != 0 {
            self.problem(value, ":outside-writers pairs and writers do not line up");
        }
        let mut out: Vec<OutsideWriter> = Vec::new();
        for pair in items.chunks(2) {
            let [key, writer] = pair else { continue };
            let access = self.string(key).filter(|a| a.split(':').count() == 2 && a.split(':').all(|part| !part.is_empty()));
            let Some(access) = access else {
            self.problem(key, ":a key of :outside-writers must be a string \"<store>:<name>\" (exactly one `:`, non-empty on both sides)");
                continue;
            };
            let Some(writer) = self.string(writer).filter(|w| !w.trim().is_empty()) else {
            self.problem(writer, &format!("the writer of {} in :outside-writers must be a non-empty string", access));
                continue;
            };
            if out.iter().any(|w| w.access == access) {
            self.problem(key, &format!("{} in :outside-writers appears twice", access));
                continue;
            }
            out.push(OutsideWriter { access, writer });
        }
        out
    }

    /// Reads a list of reasons `[(reason NAME "description" :fix "how to fix"?) …]` (the same name twice accumulates a reason).
    fn reasons(&mut self, value: &Form, what: &str) -> Vec<ReasonKind> {
        let entries = self.bracket(value).unwrap_or_else(|| {
            self.problem(value, &format!("{} must be a list of (reason NAME \"description\")", what));
            Vec::new()
        });
        let mut out: Vec<ReasonKind> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("reason"));
            let Some(parts) = parts else {
            self.problem(entry, &format!("an element of {} must be (reason NAME \"description\" :fix \"how to fix\"?)", what));
                continue;
            };
            let (Some(name), Some(description)) = (parts.get(1).and_then(|f| self.name(f)), parts.get(2).and_then(|f| self.string(f))) else {
            self.problem(entry, &format!("an element of {} must be (reason NAME \"description\" :fix \"how to fix\"?)", what));
                continue;
            };
            let rest: Vec<&Form> = parts.iter().skip(3).copied().collect();
            let mut fix = None;
            for (key, text) in self.pairs(&rest) {
                match self.text(key) {
                    ":fix" => fix = self.string(text),
                    _ => self.unknown_key(key, "reason"),
                }
            }
            if out.iter().any(|r| r.name == name) {
            self.problem(entry, &format!("reason {} is declared twice", name));
                continue;
            }
            out.push(ReasonKind { name, description, fix });
        }
        out
    }

    /// `(layer NAME :summary "…" :knows "…" :does-not-know "…" :question "…" :roles [..] :imports [..] :forbid-modules [..] :types-only true :wire-free true)`.
    fn layer(&mut self, form: &Form) -> Option<ArchLayer> {
        let Some(items) = self.paren(form).filter(|items| items.first().and_then(|h| self.symbol(h)) == Some("layer")) else {
            self.problem(form, "an element of :layers must be (layer NAME …)");
            return None;
        };
        let Some(name) = items.get(1).and_then(|f| self.name(f)) else {
            self.problem(form, "layer has no name");
            return None;
        };
        let mut layer = ArchLayer {
            name,
            summary: None,
            knows: None,
            does_not_know: None,
            question: None,
            roles: Vec::new(),
            imports: None,
            forbid_modules: Vec::new(),
            types_only: false,
            wire_free: false,
            dependency_layers: None,
            modules: Vec::new(),
            files: Vec::new(),
            exempt: Vec::new(),
        };
        let rest: Vec<&Form> = items.iter().skip(2).copied().collect();
        for (key, value) in self.pairs(&rest) {
            match self.text(key) {
                ":modules" => layer.modules = self.names(value, ":modules"),
                ":files" => layer.files = self.layer_files(value, &layer.name),
                ":exempt" => layer.exempt = self.rule_exemptions(value, &layer.name),
                ":summary" => layer.summary = self.required_string(value, ":summary"),
                ":knows" => layer.knows = self.required_string(value, ":knows"),
                ":does-not-know" => layer.does_not_know = self.required_string(value, ":does-not-know"),
                ":question" => layer.question = self.required_string(value, ":question"),
                ":roles" => layer.roles = self.names(value, ":roles"),
                ":imports" => layer.imports = Some(self.names(value, ":imports")),
                ":forbid-modules" => layer.forbid_modules = self.names(value, ":forbid-modules"),
                ":dependency-layers" => layer.dependency_layers = Some(self.names(value, ":dependency-layers")),
                ":types-only" => match self.symbol(value) {
                    Some("True" | "true") => layer.types_only = true,
                    Some("False" | "false") => layer.types_only = false,
                    _ => self.problem(value, ":types-only must be True or False"),
                },
                ":wire-free" => match self.symbol(value) {
                    Some("True" | "true") => layer.wire_free = true,
                    Some("False" | "false") => layer.wire_free = false,
                    _ => self.problem(value, ":wire-free must be True or False"),
                },
                _ => self.unknown_key(key, "layer"),
            }
        }
        if !layer.exempt.is_empty() && layer.modules.is_empty() && layer.files.is_empty() {
            // An exemption applies only to named modules and files — if it were made to exempt whole location dirs, other files later added to the layer would silently drop out of the population.
            self.problem(
                form,
                &format!("the :exempt of layer {} applies only to named :modules or :files — neither exists", layer.name),
            );
        }
        Some(layer)
    }

    /// Reads `:files ["relative path" …]` — names files one by one by path relative to the dir where the declaration is (`/`-separated).
    /// Absolute paths, `..`, a `.` segment, a path that is not `.py`, a named file that does not exist, and the same path twice are reading errors (so that no naming of a deleted or moved
    /// file remains). A dir cannot be named (a file added to a dir would silently not enter the layer).
    fn layer_files(&mut self, value: &Form, layer: &str) -> Vec<String> {
        let Some(items) = self.bracket(value) else {
            self.problem(value, &format!(":files of layer {} must be a list [\"relative path\" …]", layer));
            return Vec::new();
        };
        let base = self.path.parent().unwrap_or(Path::new(".")).to_path_buf();
        let mut out: Vec<String> = Vec::new();
        for item in items {
            let Some(file) = self.string(item) else {
                self.problem(item, &format!("an element of :files of layer {} must be a string of a path relative to the dir where the declaration is", layer));
                continue;
            };
            let relative = Path::new(&file);
            let plain = !relative.is_absolute()
                && relative.components().all(|part| matches!(part, std::path::Component::Normal(_)));
            if !plain {
                self.problem(item, &format!("{} in :files of layer {} must be written as a path relative to the dir where the declaration is (absolute paths, `..`, and `.` cannot be written)", file, layer));
            } else if !file.ends_with(".py") {
                self.problem(item, &format!("{} in :files of layer {} is not a Python file (.py) — a dir cannot be named", file, layer));
            } else if out.contains(&file) {
                self.problem(item, &format!("{} in :files of layer {} is written twice", file, layer));
            } else if !base.join(relative).is_file() {
                self.problem(item, &format!("{} in :files of layer {} does not exist (the naming of a deleted or moved file remains)", file, layer));
            } else {
                out.push(file);
            }
        }
        out
    }

    /// Reads `:exempt [(rule RULE-ID "reason") …]`. An exemption with no reason, an unknown rule ID, and declaring the same rule twice are
    /// reading errors (accumulates a reason with a position). What can be exempted is only the Python per-statement rules (crate::rules::get_all_rules — the single point crate::population
    /// that removes the population affects the hits of per-file Python rules), and the definition-style rules judged per definition file
    /// (ProjectRule::exemptible_by_layer — removed at the same single point right after the definition judgment; #2913). Writing any other ID would silently have no effect,
    /// so it is made a reading error.
    fn rule_exemptions(&mut self, value: &Form, layer: &str) -> Vec<RuleExemption> {
        let entries = self.bracket(value).unwrap_or_else(|| {
            self.problem(value, &format!(":exempt of layer {} must be a list of (rule RULE-ID \"reason\")", layer));
            Vec::new()
        });
        use crate::rules::base::LintRule as _;
        let known: Vec<String> = crate::rules::get_all_rules()
            .iter()
            .map(|rule| rule.rule_id().to_string())
            .chain(
                crate::project::rule::ProjectRule::ALL
                    .iter()
                    .filter(|rule| rule.exemptible_by_layer())
                    .map(|rule| rule.id().to_string()),
            )
            .collect();
        let mut out: Vec<RuleExemption> = Vec::new();
        for entry in entries {
            let parts = self.paren(entry).filter(|p| p.first().and_then(|h| self.symbol(h)) == Some("rule"));
            let Some(parts) = parts else {
                self.problem(entry, &format!("an element of :exempt of layer {} must be (rule RULE-ID \"reason\")", layer));
                continue;
            };
            let Some(rule) = parts.get(1).and_then(|f| self.name(f)) else {
                self.problem(entry, &format!("an element of :exempt of layer {} has no rule ID", layer));
                continue;
            };
            let reason = parts.get(2).and_then(|f| self.string(f)).unwrap_or_default();
            if parts.len() > 3 {
                self.problem(entry, &format!("{} in :exempt of layer {} has an extra value — only the 2 of (rule RULE-ID \"reason\")", rule, layer));
            }
            if !known.iter().any(|id| *id == rule) {
                self.problem(entry, &format!("{} in :exempt of layer {} is not the ID of an exemptible rule (Python per-statement rules, and definition-style rules DOEFF110, 111, 112, 118, 119)", rule, layer));
                continue;
            }
            if reason.trim().is_empty() {
                self.problem(entry, &format!("{} in :exempt of layer {} has no reason (why that rule does not apply to this layer)", rule, layer));
                continue;
            }
            if out.iter().any(|e| e.rule == rule) {
                self.problem(entry, &format!("{} is declared twice in :exempt of layer {}", rule, layer));
                continue;
            }
            out.push(RuleExemption { rule, reason });
        }
        out
    }

    /// `(defservice NAME "description"? {:depends-on [..] :layers [..]})`.
    fn service(&mut self, form: &Form, items: &[&Form]) -> Option<ArchService> {
        let Some(name) = items.get(1).and_then(|f| self.name(f)) else {
            self.problem(form, "defservice has no name");
            return None;
        };
        let range = self.lines.range(items[1].span.start, items[1].span.end);
        let mut service = ArchService {
            dir: hy_mangle(&name),
            name,
            description: None,
            depends_on: Vec::new(),
            layers: Vec::new(),
            public_contract: PublicContract::InProcess,
            invariants: None,
            clauses: None,
            clause_exemptions: Vec::new(),
            entry_modules: None,
            system: None,
            range,
            file: self.path.to_path_buf(),
        };
        let mut exemptions_form: Option<&Form> = None;
        for part in items.iter().skip(2) {
            if let Some(text) = self.string(part) {
                service.description = Some(text);
                continue;
            }
            match self.brace(part) {
                Some(entries) => {
                    for (key, value) in self.pairs(&entries) {
                        match self.text(key) {
                            ":depends-on" => service.depends_on = self.names(value, ":depends-on"),
                            ":layers" => service.layers = self.names(value, ":layers"),
                            ":dir" => service.dir = self.required_string(value, ":dir").unwrap_or_default(),
                            ":invariants" => service.invariants = Some(self.definition_refs(value, ":invariants")),
                            ":clauses" => service.clauses = Some(self.clause_names(value)),
                            ":entry-modules" => service.entry_modules = Some(self.entry_modules(value)),
                            ":system" => service.system = self.system_decl(value),
                            ":clause-exemptions" => {
                                service.clause_exemptions = self.clause_exemptions(value);
                                exemptions_form = Some(value);
                            }
                            ":public-contract" => match self.symbol(value) {
                                Some("http") => service.public_contract = PublicContract::Http,
                                _ => self.problem(value, ":public-contract must be http only (omitted = in-process)"),
                            },
                            _ => self.unknown_key(key, "defservice"),
                        }
                    }
                }
                None => self.problem(part, "an element of defservice must be a description string or a dictionary {:depends-on … :layers …}"),
            }
        }
        if let Some(form) = exemptions_form {
            let declared: &[String] = service.clauses.as_deref().unwrap_or(&[]);
            let strays: Vec<String> =
                service.clause_exemptions.iter().filter(|e| !declared.contains(&e.clause)).map(|e| e.clause.clone()).collect();
            for clause in strays {
                self.problem(form, &format!("{} in :clause-exemptions is a clause not in :clauses", clause));
            }
        }
        Some(service)
    }

}

/// Checks for name mismatches (nonexistent layers, services, and the foundation and shared layers). A reason about a defservice names the file and line it is written in,
/// the others the file of defarchitecture.
fn check(arch: &Architecture, at: &str) -> Vec<String> {
    let mut problems = Vec::new();
    let layers: BTreeSet<&str> = arch.layers.iter().map(|l| l.name.as_str()).collect();
    let services: BTreeSet<&str> = arch.services.iter().map(|s| s.name.as_str()).collect();
    let http_only: BTreeSet<&str> = arch
        .services
        .iter()
        .filter(|s| s.public_contract == PublicContract::Http)
        .map(|s| s.name.as_str())
        .collect();
    let file = at;
    let push = |problems: &mut Vec<String>, text: String| problems.push(format!("{}: {}", file, text));
    let at_service = |problems: &mut Vec<String>, service: &ArchService, text: String| {
        problems.push(format!("{}:{}:{}: {}", service.file.display(), service.range.start.line + 1, service.range.start.character + 1, text))
    };
    for layer in &arch.layers {
        for target in layer.imports.iter().flatten() {
            if !layers.contains(target.as_str()) {
                push(&mut problems, format!("{} in :imports of layer {} is not in :layers", target, layer.name));
            }
        }
        for target in layer.dependency_layers.iter().flatten() {
            if !layers.contains(target.as_str()) {
                push(&mut problems, format!("{} in :dependency-layers of layer {} is not in :layers", target, layer.name));
            }
        }
    }
    if let Some(foundation) = &arch.foundation {
        if !layers.contains(foundation.as_str()) {
            push(&mut problems, format!("there is no layer in :layers with the same name as :foundation {}", foundation));
        }
    }
    if let Some(decl) = &arch.environment_branches {
        for layer in decl.layers.iter().filter(|layer| !layers.contains(layer.as_str())) {
            push(&mut problems, format!("{} in :layers of :environment-branches is not in :layers", layer));
        }
    }
    if let Some(place) = &arch.verification_environment {
        if place.is_empty() || place.contains('/') {
            push(&mut problems, format!(":verification-environment {:?} must be a single dir name directly under root (not containing `/`)", place));
        }
        if arch.shared.as_deref() == Some(place.as_str()) || arch.foundation.as_deref() == Some(place.as_str()) {
            push(&mut problems, format!(":verification-environment {} cannot have the same name as shared or foundation", place));
        }
        if arch.services.iter().any(|s| s.dir == *place) {
            push(&mut problems, format!(":verification-environment {} cannot have the same name as a declared service's dir", place));
        }
    }
    for module in &arch.root_modules {
        if module.is_empty() || module.contains('/') || module.contains('.') {
            push(&mut problems, format!(":root-modules {:?} must be a single module name directly under root (a file stem — not containing `/` or `.`)", module));
        }
    }
    for open in &arch.open_layers {
        if !layers.contains(open.as_str()) {
            push(&mut problems, format!("{} in :open-layers is not in :layers", open));
        }
    }
    for (i, placed) in arch.placed_dependencies.iter().enumerate() {
        if !layers.contains(placed.as_str()) {
            push(&mut problems, format!("{} in :placed-dependencies is not in :layers", placed));
        } else if arch.foundation.as_deref() == Some(placed.as_str()) {
            push(&mut problems, format!("{} in :placed-dependencies is a :foundation layer — it is not applied to a layer outside services", placed));
        }
        if arch.placed_dependencies[..i].contains(placed) {
            push(&mut problems, format!("{} in :placed-dependencies is written twice", placed));
        }
    }
    // Sending and receiving modules are allowed only in the foundation layer (DOEFF120) — writing :wire-modules in a declaration with no foundation allows nothing, so it is an error.
    if !arch.wire_modules.is_empty() && arch.foundation.is_none() {
        push(&mut problems, ":wire-modules requires :foundation (the sending and receiving modules that are allowed JsonValue are placed only in the foundation layer)".to_string());
    }
    // Allowlist definitions are placed only in the foundation layer (R2) — writing an allowlist in a declaration with no foundation leaves nowhere to place it, so it is an error.
    if !arch.world_handlers.is_empty() && arch.foundation.is_none() {
        push(&mut problems, ":world-handlers requires :foundation (definitions allowed to touch the outside world are placed only in the foundation layer)".to_string());
    }
    if arch.edge_mark.is_some() && arch.world_handlers.is_empty() {
        push(&mut problems, ":edge-mark requires :world-handlers (edge tests are tests that reach the allowlist definitions)".to_string());
    }
    // The counterparts' contract tests are counted by DOEFF137 with the same index and graph as the edge tests of :world-handlers (with the same enabling condition) — a declaration that is not judged is an error.
    if !arch.stand_ins.is_empty() && arch.edge_mark.is_none() {
        push(&mut problems, ":stand-ins requires :edge-mark (DOEFF137 counts the counterparts' contract tests the same way as the edge tests of :world-handlers)".to_string());
    }
    for handler in &arch.world_handlers {
        for wrapped in &handler.wraps {
            if arch.world_handlers.iter().any(|h| h.definition == *wrapped) {
                push(
                    &mut problems,
                    format!("{} in :wraps of world-handler {} is an allowlist definition — write only doeff real-I/O handlers in :wraps", wrapped.spelling(), handler.definition.spelling()),
                );
            }
        }
    }
    for service in &arch.services {
        for layer in &service.layers {
            if !layers.contains(layer.as_str()) {
                at_service(&mut problems, service, format!("{} in :layers of service {} is not in :layers of defarchitecture", layer, service.name));
            }
            if arch.foundation.as_deref() == Some(layer.as_str()) {
                at_service(&mut problems, service, format!("foundation layer {} cannot be placed in :layers of service {} (it is a layer outside services)", layer, service.name));
            }
        }
        for dependency in &service.depends_on {
            if !services.contains(dependency.as_str()) {
                at_service(&mut problems, service, format!("{} in :depends-on of service {} is not a declared service", dependency, service.name));
            }
            if http_only.contains(dependency.as_str()) {
                at_service(
                    &mut problems,
                    service,
                    format!(
                        "service {} lists {} in :depends-on, but it is :public-contract http (its public contract is the HTTP surface only) — do not put it in an in-process dependency; read it over HTTP",
                        service.name, dependency
                    ),
                );
            }
        }
    }
    problems
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOOD: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "judgment" :roles [judgment] :imports [core intent])
           (layer intent :roles [intent] :types-only True)
           (layer foundation)]
  :shared "shared"
  :foundation foundation
  :roles {:judgment "business judgment"})
(defservice billing "billing" {:depends-on [custody] :layers [core intent]})
(defservice custody {:layers [core intent]})
"#;

    #[test]
    fn reads_the_declaration_and_maps_it_to_layer_settings() {
        let arch = Architecture::parse(GOOD, Path::new("architecture.hy")).unwrap();
        assert_eq!(arch.root, "app");
        assert_eq!(arch.services.len(), 2);
        assert_eq!(arch.services[0].depends_on, vec!["custody"]);
        assert_eq!(arch.services[0].description.as_deref(), Some("billing"));
        let section = arch.layers_section();
        assert_eq!(section.paths["core"], PathPatterns::Many(vec!["app/*/core".into()]));
        assert_eq!(section.paths["foundation"], PathPatterns::Many(vec!["app/foundation".into()]));
        assert_eq!(section.types_only, vec!["intent"]);
        assert_eq!(arch.roles_section().describe["judgment"], "business judgment");
    }

    #[test]
    fn misreadings_are_errors_with_positions() {
        let bad = r#"(defarchitecture s :root "app" :layers [(layer core :colour "x")] :nonsense 1 :legacy ["app/old"])
(defservice a {:layers [ghost] :uses [b]})
(defservice a {})
"#;
        let problems = Architecture::parse(bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        for needle in ["architecture.hy:1:", ":legacy has been retired", "ghost", "service a is declared twice"] {
            assert!(problems.contains(needle), "{} is missing:\n{}", needle, problems);
        }
        assert!(!problems.contains(":colour") && !problems.contains(":nonsense"), "an unknown key was made an error:\n{}", problems);
    }

    #[test]
    fn unknown_keys_are_notices_and_the_rest_is_read() {
        // A declaration newer than this binary (an unknown key) is not made an error; only that key is left unread and reported as a notice.
        let newer = GOOD
            .replace("(layer foundation)]", "(layer foundation :future-knob [x])]")
            .replace(":foundation foundation", ":foundation foundation\n  :brand-new-key 1")
            .replace("{:depends-on [custody] :layers [core intent]}", "{:depends-on [custody] :layers [core intent] :owners [me]}");
        let arch = Architecture::parse(&newer, Path::new("architecture.hy")).unwrap();
        assert_eq!(arch.services.len(), 2, "the rest of the declaration was not read");
        assert_eq!(arch.services[0].depends_on, vec!["custody"]);
        let keys: Vec<&str> = arch.notices.iter().map(|n| n.key.as_str()).collect();
        assert_eq!(keys, vec!["layer :future-knob", "defarchitecture :brand-new-key", "defservice :owners"]);
        let lines: Vec<u32> = arch.notices.iter().map(|n| n.range.start.line).collect();
        let expect = |needle: &str| newer.lines().position(|l| l.contains(needle)).unwrap() as u32;
        assert_eq!(lines, vec![expect(":future-knob"), expect(":brand-new-key"), expect(":owners")]);
    }

    #[test]
    fn public_contract_http_is_read_and_cannot_be_depended_on() {
        // A service whose public contract is HTTP only (:public-contract http) cannot be listed in another service's :depends-on.
        let declared = GOOD.replace("{:depends-on [custody] :layers [core intent]}", "{:layers [core intent]}");
        let with_http = declared.replace("(defservice custody", "(defservice custody {:public-contract http})\n(defservice custody-old");
        let arch = Architecture::parse(&with_http, Path::new("architecture.hy")).unwrap();
        let custody = arch.services.iter().find(|s| s.name == "custody").unwrap();
        assert_eq!(custody.public_contract, PublicContract::Http);
        assert!(arch.services.iter().filter(|s| s.name != "custody").all(|s| s.public_contract == PublicContract::InProcess));
        assert!(arch.notices.is_empty(), "reported as an unknown key: {:?}", arch.notices);

        let depended = GOOD.replace("(defservice custody", "(defservice custody {:public-contract http})\n(defservice custody-old");
        let problems = Architecture::parse(&depended, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains("lists custody in :depends-on, but it is :public-contract http"), "the dependency was not stopped:\n{}", problems);

        let bad = GOOD.replace("(defservice custody", "(defservice custody {:public-contract grpc})\n(defservice custody-old");
        let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains(":public-contract must be http only"), "a value outside the vocabulary was let through:\n{}", problems);
    }

    #[test]
    fn clauses_and_their_exemptions_are_read() {
        // :clauses is a list of clause names, :clause-exemptions is a dictionary of {"clause" "reason"}, and the exempted clauses are within :clauses.
        let declared = GOOD.replace(
            "{:depends-on [custody] :layers [core intent]}",
            "{:depends-on [custody] :layers [core intent] :clauses [\"C1\" C2] :clause-exemptions {\"C2\" \"structural guarantee\"}}",
        );
        let arch = Architecture::parse(&declared, Path::new("architecture.hy")).unwrap();
        let billing = arch.services.iter().find(|s| s.name == "billing").unwrap();
        assert_eq!(billing.clauses.as_deref(), Some(&["C1".to_string(), "C2".to_string()][..]));
        assert_eq!(billing.clause_exemptions, vec![ClauseExemption { clause: "C2".into(), reason: "structural guarantee".into() }]);
        assert!(arch.services.iter().filter(|s| s.name != "billing").all(|s| s.clauses.is_none() && s.clause_exemptions.is_empty()));
        assert!(arch.notices.is_empty(), "reported as an unknown key: {:?}", arch.notices);

        let bad = GOOD.replace(
            "{:depends-on [custody] :layers [core intent]}",
            "{:depends-on [custody] :layers [core intent] :clauses [\"C1\" \"C1\" \"a::b\"] :clause-exemptions {\"C9\" \"reason\" \"C1\" \"\"}}",
        );
        let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains("C1 in :clauses is written twice"), "an overlap was let through:\n{}", problems);
        assert!(problems.contains("not containing `::`"), "a name containing the separator was let through:\n{}", problems);
        assert!(problems.contains("the reason of C1 in :clause-exemptions must be a non-empty string"), "an empty reason was let through:\n{}", problems);
        assert!(problems.contains("C9 in :clause-exemptions is a clause not in :clauses"), "a clause not in the declaration was exempted:\n{}", problems);
    }

    #[test]
    fn layer_rule_exemptions_name_their_modules_and_reasons() {
        // A layer names modules with :modules, and with :exempt [(rule RULE-ID "reason")] exempts only the named modules from the Python
        // rules' population. An empty reason, a layer rule's ID, an unknown ID, an exemption with no :modules, and the same rule twice are reading errors.
        let declared = GOOD.replace(
            "(layer foundation)",
            "(layer foundation) (layer startup :modules [guard \"guard.hooks\"] :exempt [(rule DOEFF004 \"entered at startup\")] :forbid-modules [doeff])",
        );
        let arch = Architecture::parse(&declared, Path::new("architecture.hy")).unwrap();
        let startup = arch.layers.iter().find(|l| l.name == "startup").unwrap();
        assert_eq!(startup.modules, vec!["guard".to_string(), "guard.hooks".to_string()]);
        assert_eq!(startup.exempt, vec![RuleExemption { rule: "DOEFF004".into(), reason: "entered at startup".into() }]);
        assert!(arch.notices.is_empty(), "reported as an unknown key: {:?}", arch.notices);
        assert_eq!(arch.exempt_layer_of("guard").map(|l| l.name.as_str()), Some("startup"));
        assert_eq!(arch.exempt_layer_of("guard.hooks.inner").map(|l| l.name.as_str()), Some("startup"));
        assert_eq!(arch.exempt_layer_of("guardian").map(|l| l.name.as_str()), None, "a different module that merely has the same name prefix was included");
        assert_eq!(arch.exempt_layer_of("billing.core").map(|l| l.name.as_str()), None);

        let bad = GOOD.replace(
            "(layer foundation)",
            "(layer foundation) (layer startup :modules [guard] :exempt [(rule DOEFF004 \"\") (rule DOEFF106 \"layer rule\") (rule DOEFF999 \"x\") \
             (rule DOEFF001 \"one\") (rule DOEFF001 \"two\")]) (layer loose :exempt [(rule DOEFF004 \"no named module\")])",
        );
        let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains("DOEFF004 in :exempt of layer startup has no reason"), "an empty reason was let through:\n{}", problems);
        assert!(problems.contains("DOEFF106 in :exempt of layer startup is not the ID of an exemptible rule (Python per-statement rules, and definition-style rules DOEFF110, 111, 112, 118, 119)"), "a layer rule's ID was let through:\n{}", problems);
        assert!(problems.contains("DOEFF999 in :exempt of layer startup is not the ID of an exemptible rule"), "an unknown ID was let through:\n{}", problems);
        assert!(problems.contains("DOEFF001 is declared twice in :exempt of layer startup"), "the same rule twice was let through:\n{}", problems);
        assert!(problems.contains("the :exempt of layer loose applies only to named :modules or :files"), "an exemption with no naming was let through:\n{}", problems);

        // Definition-style rules (DOEFF110 and others) can be exempted. The critical DOEFF126 (a bare defk call) cannot be exempted (#2913).
        let definition = GOOD.replace(
            "(layer foundation)",
            "(layer foundation) (layer macro :modules [expand] :exempt [(rule DOEFF110 \"called at macro expansion time\")])",
        );
        let arch = Architecture::parse(&definition, Path::new("architecture.hy")).unwrap();
        assert_eq!(
            arch.exempt_layer_of("expand").map(|l| l.exempt.clone()),
            Some(vec![RuleExemption { rule: "DOEFF110".into(), reason: "called at macro expansion time".into() }])
        );
        let critical = GOOD.replace(
            "(layer foundation)",
            "(layer foundation) (layer macro :modules [expand] :exempt [(rule DOEFF126 \"bare call\")])",
        );
        let problems = Architecture::parse(&critical, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains("DOEFF126 in :exempt of layer macro is not the ID of an exemptible rule"), "an exemption of a critical rule was let through:\n{}", problems);
    }

    #[test]
    fn layer_files_name_existing_files_by_relative_path() {
        // :files names files one by one by path relative to the dir where the declaration is (for files that have no module name).
        // Only a path exactly identical to the named one is included. A nonexistent file, `..`, an absolute path, a dir (not .py), and a duplicate are reading errors.
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("sealed")).unwrap();
        std::fs::write(dir.path().join("sealed").join("a.py"), "x = 1\n").unwrap();
        std::fs::write(dir.path().join("b.py"), "x = 1\n").unwrap();
        let declaration = dir.path().join("architecture.hy");
        let declared = GOOD.replace(
            "(layer foundation)",
            "(layer foundation) (layer sealed :files [\"sealed/a.py\" \"b.py\"] :exempt [(rule DOEFF007 \"the sealed table keeps a record\")])",
        );
        let arch = Architecture::parse_at(&declared, &declaration, dir.path()).unwrap();
        let sealed = arch.layers.iter().find(|l| l.name == "sealed").unwrap();
        assert_eq!(sealed.files, vec!["sealed/a.py".to_string(), "b.py".to_string()]);
        assert!(arch.notices.is_empty(), "reported as an unknown key: {:?}", arch.notices);
        assert_eq!(arch.exempt_layer_of_file("sealed/a.py").map(|l| l.name.as_str()), Some("sealed"));
        assert_eq!(arch.exempt_layer_of_file("b.py").map(|l| l.name.as_str()), Some("sealed"));
        assert_eq!(arch.exempt_layer_of_file("sealed/c.py").map(|l| l.name.as_str()), None, "a file in the same dir that was not named was included");
        assert_eq!(arch.exempt_layer_of_file("a.py").map(|l| l.name.as_str()), None, "a different path that merely has the same name ending was included");

        let bad = GOOD.replace(
            "(layer foundation)",
            "(layer foundation) (layer sealed :files [\"missing.py\" \"../b.py\" \"/b.py\" \"sealed\" \"b.py\" \"b.py\"] \
             :exempt [(rule DOEFF007 \"the sealed table keeps a record\")])",
        );
        let problems = Architecture::parse_at(&bad, &declaration, dir.path()).unwrap_err().join("\n");
        assert!(problems.contains("missing.py in :files of layer sealed does not exist"), "a naming of a nonexistent file was let through:\n{}", problems);
        assert!(problems.contains("../b.py in :files of layer sealed must be written as a path relative to the dir where the declaration is"), "`..` was let through:\n{}", problems);
        assert!(problems.contains("/b.py in :files of layer sealed must be written as a path relative to the dir where the declaration is"), "an absolute path was let through:\n{}", problems);
        assert!(problems.contains("sealed in :files of layer sealed is not a Python file (.py)"), "naming a dir was let through:\n{}", problems);
        assert!(problems.contains("b.py in :files of layer sealed is written twice"), "the same path twice was let through:\n{}", problems);
    }

    #[test]
    fn invariants_are_module_colon_function_strings() {
        // :invariants is a list of "module:FUNCTION" strings. A service that omits it is None (a missing declaration is judged by DOEFF163).
        let declared = GOOD.replace(
            "{:depends-on [custody] :layers [core intent]}",
            "{:depends-on [custody] :layers [core intent] :invariants [\"app.sim.billing_invariants:charges-hold\"]}",
        );
        let arch = Architecture::parse(&declared, Path::new("architecture.hy")).unwrap();
        let billing = arch.services.iter().find(|s| s.name == "billing").unwrap();
        let spelled: Vec<String> = billing.invariants.iter().flatten().map(|d| d.spelling()).collect();
        assert_eq!(spelled, vec!["app.sim.billing_invariants:charges-hold"]);
        assert!(arch.services.iter().filter(|s| s.name != "billing").all(|s| s.invariants.is_none()));
        assert!(arch.notices.is_empty(), "reported as an unknown key: {:?}", arch.notices);

        let bad = GOOD.replace("{:depends-on [custody] :layers [core intent]}", "{:depends-on [custody] :layers [core intent] :invariants [\"nocolon\" \"a:b\" \"a:b\"]}");
        let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains("nocolon in :invariants must be a spelling \"module.path:NAME\""), "a spelling error was let through:\n{}", problems);
        assert!(problems.contains("a:b in :invariants is written twice"), "an overlap was let through:\n{}", problems);
        let scalar = GOOD.replace("{:depends-on [custody] :layers [core intent]}", "{:depends-on [custody] :layers [core intent] :invariants \"a:b\"}");
        let problems = Architecture::parse(&scalar, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains(":invariants must be a list of [\"module:NAME\" …]"), "a non-list value was let through:\n{}", problems);
    }

    #[test]
    fn entry_modules_are_dotted_module_strings() {
        // :entry-modules is a non-empty list of "pkg.module" strings. A service that omits it is None (judged by the entry layer's dir).
        let declared = GOOD.replace(
            "{:depends-on [custody] :layers [core intent]}",
            "{:depends-on [custody] :layers [core intent] :entry-modules [\"merge_queue.controller.main\"]}",
        );
        let arch = Architecture::parse(&declared, Path::new("architecture.hy")).unwrap();
        let billing = arch.services.iter().find(|s| s.name == "billing").unwrap();
        assert_eq!(billing.entry_modules, Some(vec!["merge_queue.controller.main".to_string()]));
        assert!(arch.services.iter().filter(|s| s.name != "billing").all(|s| s.entry_modules.is_none()));

        let bad = GOOD.replace(
            "{:depends-on [custody] :layers [core intent]}",
            "{:depends-on [custody] :layers [core intent] :entry-modules [\"a/b.hy\" \"a.b\" \"a.b\" \"a..b\"]}",
        );
        let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains("an element of :entry-modules must be a spelling \"pkg.module\""), "a spelling error was let through:\n{}", problems);
        assert!(problems.contains("a.b in :entry-modules is written twice"), "an overlap was let through:\n{}", problems);
        let empty = GOOD.replace("{:depends-on [custody] :layers [core intent]}", "{:depends-on [custody] :layers [core intent] :entry-modules []}");
        let problems = Architecture::parse(&empty, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains(":entry-modules is empty"), "an empty list was let through:\n{}", problems);
    }

    #[test]
    fn wire_modules_are_dotted_module_patterns_and_need_a_foundation() {
        let good = GOOD.replace(":foundation foundation", ":foundation foundation :wire-modules [\"app.foundation.records_client\" app.foundation.http.*]");
        let arch = Architecture::parse(&good, Path::new("architecture.hy")).unwrap();
        assert_eq!(arch.wire_modules, vec!["app.foundation.records_client", "app.foundation.http.*"]);
        let bad = r#"(defarchitecture s :root "app" :layers [(layer core)] :wire-modules ["app/foundation/x.hy" "a..b" "a.x**" "ok.one" "ok.one"])"#;
        let problems = Architecture::parse(bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        for needle in ["app/foundation/x.hy in :wire-modules must be written as a module spelling", "a..b in :wire-modules must be written as a module spelling", "a.x** in :wire-modules must be written as a module spelling", "ok.one in :wire-modules is written twice", ":wire-modules requires :foundation"] {
            assert!(problems.contains(needle), "{} is missing:\n{}", needle, problems);
        }
    }

    #[test]
    fn world_handlers_are_read_with_a_closed_vocabulary_of_touches() {
        let good = GOOD.replace(
            ":foundation foundation",
            r#":foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http file clock]
                     :answers [HttpRequest ReadText]
                     :wraps ["doeff_core_effects._os_file:os-file-handler" "doeff_core_effects._http_handlers:http-production-handler"])
                   (world-handler "app.foundation.agent:claude-runtime" :touches [process])]"#,
        );
        let arch = Architecture::parse(&good, Path::new("architecture.hy")).unwrap();
        assert!(arch.notices.is_empty(), "reported as an unknown key: {:?}", arch.notices);
        assert_eq!(arch.world_handlers.len(), 2);
        let host = &arch.world_handlers[0];
        assert_eq!(host.definition, DefinitionRef { module: "app.foundation.host".into(), name: "with-host".into() });
        assert_eq!(host.touches, vec![WorldTouch::Http, WorldTouch::File, WorldTouch::Clock]);
        assert_eq!(host.answers, vec!["HttpRequest", "ReadText"]);
        assert_eq!(host.wraps[0].spelling(), "doeff_core_effects._os_file:os-file-handler");
        assert!(arch.world_handlers[1].wraps.is_empty() && arch.world_handlers[1].answers.is_empty());
        let line = good.lines().position(|l| l.contains("app.foundation.host:with-host")).unwrap() as u32;
        assert_eq!(host.range.start.line, line);
        assert_eq!(host.contract_test, ContractTest::Required, "a handler that omits :contract-test requires an edge test");
    }

    #[test]
    fn world_handler_contract_test_reads_reasoned_and_bare_none() {
        // DOEFF137 (#1796): a none with a reason does not require an edge test. A bare symbol none with no reason is readable but fires when judged.
        let good = GOOD.replace(
            ":foundation foundation",
            r#":foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http]
                     :contract-test (none :doeff-test "packages/doeff-core-effects/tests/test_http.hy::test-http-contract"))
                   (world-handler "app.foundation.host:main" :touches [process] :contract-test (none :repo-test "tests/test_main.hy::test-main"))
                   (world-handler "app.foundation.host:bare" :touches [process] :contract-test none)
                   (world-handler "app.foundation.agent:claude-runtime" :touches [process])]"#,
        );
        let arch = Architecture::parse(&good, Path::new("architecture.hy")).unwrap();
        assert!(arch.notices.is_empty(), "reported as an unknown key: {:?}", arch.notices);
        assert_eq!(
            arch.world_handlers[0].contract_test,
            ContractTest::Waived(ContractTestWaiver::DoeffContractTest(TestNodeId {
                file: "packages/doeff-core-effects/tests/test_http.hy".into(),
                name: "test-http-contract".into()
            }))
        );
        assert_eq!(
            arch.world_handlers[1].contract_test,
            ContractTest::Waived(ContractTestWaiver::CoveredByRepoTest(TestNodeId { file: "tests/test_main.hy".into(), name: "test-main".into() }))
        );
        assert_eq!(arch.world_handlers[2].contract_test, ContractTest::NoneWithoutReason);
        assert_eq!(arch.world_handlers[3].contract_test, ContractTest::Required);
    }

    #[test]
    fn world_handler_contract_test_misshapen_reasons_are_errors() {
        // A value of the wrong shape, an empty reason, a wrong key, or a wrong number of reasons is a declaration error (not silently let through; #1796).
        let good = GOOD.replace(
            ":foundation foundation",
            r#":foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http] :contract-test VALUE)]"#,
        );
        for value in [
            "always",
            "\"none\"",
            "[none]",
            "(none)",
            "(always :doeff-test \"tests/test_x.hy::test-y\")",
            "(none :doeff-test \"\")",
            "(none :doeff-test \"tests/test_x.hy\")",
            "(none :doeff-test \"tests/test_x.hy::\")",
            "(none :doeff-test \"/abs/test_x.hy::test-y\")",
            "(none :doeff-test \"tests/test_x.txt::test-y\")",
            "(none :doeff-test \"tests/test x.hy::test-y\")",
            "(none :doeff-test test-y)",
            "(none :because \"tests/test_x.hy::test-y\")",
            "(none :doeff-test \"tests/test_x.hy::a\" :repo-test \"tests/test_x.hy::b\")",
        ] {
            let bad = good.replace("VALUE", value);
            let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
            assert!(problems.contains("app.foundation.host:with-host :contract-test must be (none"), "{} was let through:\n{}", value, problems);
        }
    }

    #[test]
    fn assembly_shape_is_read_and_misreadings_are_errors() {
        let good = GOOD.replace(
            ":foundation foundation",
            r#":foundation foundation
  :assembly-shape {:translation-point "with-*-translation" :retired-function "handlers-of" :translations "TRANSLATION-HANDLERS"
                   :translation-layer "protocol" :intent-layer "intent"}"#,
        );
        let arch = Architecture::parse(&good, Path::new("architecture.hy")).unwrap();
        let shape = arch.assembly_shape.unwrap();
        assert_eq!(
            (shape.translation_point.as_str(), shape.retired_function.as_deref(), shape.translations.as_str(), shape.intent_layer.as_str()),
            ("with-*-translation", Some("handlers-of"), "TRANSLATION-HANDLERS", "intent")
        );
        let bad = GOOD.replace(":foundation foundation", r#":foundation foundation :assembly-shape {:translation-point "with-translation"}"#);
        let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        for needle in ["exactly one `*`", ":assembly-shape has no :translations", ":assembly-shape has no :intent-layer"] {
            assert!(problems.contains(needle), "{} is missing:\n{}", needle, problems);
        }
    }

    #[test]
    fn world_handler_misreadings_are_errors() {
        let bad = GOOD.replace(
            ":foundation foundation",
            r#":foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http smoke http])
                   (world-handler "app.foundation.host:with-host" :touches [file])
                   (world-handler "app..x:y" :touches [file])
                   (world-handler "app.foundation.pure:answer")
                   (world-handler "app.foundation.outer:outer" :touches [file] :wraps ["app.foundation.host:with-host" "nocolon"])
                   (defk nope)]"#,
        );
        let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
        for needle in [
            "smoke in :touches of world-handler app.foundation.host:with-host is not in the vocabulary",
            "http in :touches of world-handler app.foundation.host:with-host is written twice",
            "world-handler app.foundation.host:with-host is declared twice",
            "app..x:y in world-handler must be a spelling \"module.path:NAME\"",
            "world-handler app.foundation.pure:answer has no :touches",
            "app.foundation.host:with-host in :wraps of world-handler app.foundation.outer:outer is an allowlist definition",
            "nocolon in :wraps must be a spelling",
            "an element of :world-handlers must be (world-handler",
        ] {
            assert!(problems.contains(needle), "{} is missing:\n{}", needle, problems);
        }
        let no_foundation = r#"(defarchitecture s :root "app" :layers [(layer core)] :world-handlers [(world-handler "app.x:y" :touches [file])])"#;
        let problems = Architecture::parse(no_foundation, Path::new("architecture.hy")).unwrap_err().join("\n");
        assert!(problems.contains(":world-handlers requires :foundation"), "{}", problems);
    }

    /// `:semantic-lines` reads the demarcation's name, the questions it goes into (DOEFF201, 202, 205), the text, the examples that fire, and the examples that do not (may be omitted).
    /// An example can also be written as a bracket string (`#[code[…]code]`), and a pair `{:code "<code>" :why "…"}` also reads one sentence on why it fires / does not fire (#1995).
    /// A declaration that is not written is empty (the questions stay as they are).
    #[test]
    fn semantic_lines_are_read_for_the_jev_questions() {
        let good = GOOD.replace(
            ":foundation foundation",
            r##":foundation foundation
  :semantic-lines [(line "demarcation 1" :rules [DOEFF201] :text "protocol does not judge."
                     :fires [{:code #[code[(defk f [x] (when (in x "allowed") x))]code] :why "this definition decides whether to allow."}]
                     :silent ["(defk g [x] (h x))"])
                   (line demarcation-5 :rules [DOEFF205 doeff201] :text "projection is also judgment.")]"##,
        );
        let arch = Architecture::parse(&good, Path::new("architecture.hy")).unwrap();
        assert!(arch.notices.is_empty(), "reported as an unknown key: {:?}", arch.notices);
        assert_eq!(
            arch.semantic_lines,
            vec![
                SemanticLine {
                    name: "demarcation 1".into(),
                    rules: vec![SemanticQuestion::BusinessDecision],
                    text: "protocol does not judge.".into(),
                    fires: vec![LineExample { code: "(defk f [x] (when (in x \"allowed\") x))".into(), why: Some("this definition decides whether to allow.".into()) }],
                    silent: vec![LineExample { code: "(defk g [x] (h x))".into(), why: None }],
                },
                SemanticLine {
                    name: "demarcation-5".into(),
                    rules: vec![SemanticQuestion::MixedConcerns, SemanticQuestion::BusinessDecision],
                    text: "projection is also judgment.".into(),
                    fires: Vec::new(),
                    silent: Vec::new(),
                },
            ]
        );
        assert!(Architecture::parse(GOOD, Path::new("architecture.hy")).unwrap().semantic_lines.is_empty());
    }

    /// Unknown rules, rules that are not Jev questions, questions that take no demarcation, empty text, a missing :rules or :text, writing twice, and empty examples are settings errors (not silently dropped from the question).
    #[test]
    fn semantic_line_misreadings_are_errors() {
        for (line, needle) in [
            (r#"(line "a" :rules [DOEFF999] :text "x")"#, "DOEFF999 in :rules of demarcation a is an unknown rule"),
            (r#"(line "a" :rules [DOEFF101] :text "x")"#, "DOEFF101 in :rules of demarcation a is not a Jev-question rule"),
            (r#"(line "a" :rules [DOEFF203] :text "x")"#, "DOEFF203 in :rules of demarcation a takes no demarcation"),
            (r#"(line "a" :rules [DOEFF201] :text "")"#, ":text of demarcation a is empty"),
            (r#"(line "a" :rules [DOEFF201] :text "   ")"#, ":text of demarcation a is empty"),
            (r#"(line "a" :rules [DOEFF201])"#, "demarcation a has no :text (the demarcation text)"),
            (r#"(line "a" :text "x")"#, "demarcation a has no :rules"),
            (r#"(line "a" :rules [] :text "x")"#, "demarcation a has no rule in :rules that takes a demarcation question"),
            (r#"(line "a" :rules [DOEFF201 DOEFF201] :text "x")"#, "DOEFF201 in :rules of demarcation a is written twice"),
            (r#"(line "a" :rules [DOEFF201] :text "x" :fires [""])"#, ":fires of demarcation a has an empty code"),
            (r#"(line "a" :rules [DOEFF201] :text "x" :silent [code])"#, "an element of :silent of demarcation a must be a code string or"),
            (r#"(line "a" :rules [DOEFF201] :text "x" :silent [{:why "w"}])"#, "an example of :silent of demarcation a has no :code"),
            (r#"(line "a" :rules [DOEFF201] :text "x" :fires [{:code "" :why "w"}])"#, ":fires of demarcation a has an empty code"),
            (r#"(line "a" :rules [DOEFF201] :text "x" :fires [{:code "(f)" :why " "}])"#, ":why of an example of :fires of demarcation a is empty"),
            (r#"(line "a" :rules [DOEFF201] :text "x" :fires [{:code "(f)" :why why}])"#, ":why of :fires of demarcation a must be a string"),
            (r#"(line "a" :rules [DOEFF201] :text "x") (line "a" :rules [DOEFF202] :text "y")"#, "demarcation a is declared twice"),
            (r#"(line :rules [DOEFF201] :text "x")"#, "an element of :semantic-lines has no name"),
            (r#"(rule "a" :rules [DOEFF201] :text "x")"#, "an element of :semantic-lines must be (line"),
        ] {
            let bad = GOOD.replace(":foundation foundation", &format!(":foundation foundation :semantic-lines [{}]", line));
            let problems = Architecture::parse(&bad, Path::new("architecture.hy")).unwrap_err().join("\n");
            assert!(problems.contains(needle), "{} was let through:\n{}", line, problems);
        }
    }

    /// The contract spellings are only the key names of objects at any depth, the string elements of `enum`, and the string values of `const` —
    /// the contents of comments, example values, and strings in other arrays (the paths of required and deprecated) are not collected.
    #[test]
    fn contract_spellings_are_keys_and_enum_const_values() {
        let json: serde_json::Value = serde_json::from_str(
            r#"{"comment": "description of mail", "streamKinds": ["letter"], "deprecated": ["a.b"],
                "$defs": {"Carrier": {"properties": {"mail": {"type": "string"}}, "required": ["job"]},
                          "Ref": {"properties": {"kind": {"enum": ["mail", 3, "chat"]}, "source": {"const": "ask"}}}},
                "examples": [{"note": "letter"}]}"#,
        )
        .unwrap();
        let mut spellings = BTreeSet::new();
        collect_contract_spellings(&json, &mut spellings);
        let expected = ["$defs", "Carrier", "Ref", "ask", "chat", "comment", "const", "deprecated", "enum", "examples", "kind", "mail", "note", "properties", "required", "source", "streamKinds", "type"];
        assert_eq!(spellings.iter().map(String::as_str).collect::<Vec<_>>(), expected);
        assert!(!spellings.contains("letter"), "string values that are not enum / const are not collected");
    }

    /// Whether a declared regular expression is readable is decided by syntax alone (the NFA is not built with `Regex::new`). Readable spellings pass as before,
    /// and unreadable spellings are refused with the same notice text as `Regex::new`.
    #[test]
    fn a_declared_pattern_is_checked_by_syntax_and_refused_with_the_compilers_words() {
        for good in [r"foo\.bar", r"\bsleep\b", r"^\(time\.sleep\b", r"(?i)abc|def", r"[a-z_]+\d{2,4}", r"\p{Han}+", "plain text", ""] {
            assert!(check_regex(good).is_ok(), "{good} is a readable spelling");
            assert!(regex::Regex::new(good).is_ok(), "{good} is a readable spelling (premise of the test)");
        }
        for bad in ["(unclosed", "[a-", "a{2,1}", r"\", "*start", "(?P<n>a)(?P<n>b)", r"\p{NoSuchClass}"] {
            let ours = check_regex(bad).expect_err(bad).to_string();
            let theirs = regex::Regex::new(bad).expect_err(bad).to_string();
            assert_eq!(ours, theirs, "{bad} refusal text differs from before");
        }
    }
}
