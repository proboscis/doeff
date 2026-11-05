//! DOEFF155 and 156: the shape of assembly (#1367 / #1189 — originally a temporary check in agora-controllers,
//! the 5 laws of assembly-breaches in controllers/agora_sim/tests/business_fakes_rules.hy, ADR R8, R9, #780, #827, #832, #893, #1340).
//!
//! The single point of the translation list is a defk in the assembly layer (name pattern `:translation-point` — e.g. `with-*-translation`), and its body's
//! `(with-handlers [#* list …] body)` lists the constants of translation lists (`:translations`) of the translation layer (`:translation-layer`). Base handlers are
//! placed in the text where another function wraps the body, and are not mixed into the translation list.
//!
//! DOEFF155 (shape):
//! * a set file (a production code file that matches `:sets` of `:business-fakes`) remains
//! * a retired assembly function (`:retired-function`) remains in the assembly layer
//! * the single point of the translation list lists, with `#*`, an imported name that is not a translation list
//! * the single point of the translation list lists its own argument with `#*` (if callers can pass a list, a fake can pass a list different from production — lists are only constants of the translation layer)
//! * the verification environment (the dir of architecture.hy's `:verification-environment`) and test files open and list a translation list (a list the single point may list) with `#*` —
//!   a fake calls the same single point of the translation list as production and does not assemble the list itself (other lists the point lists together, and their order, would not be reflected — #3408)
//! * list order: there is no list outside (before) it that answers the intent effects (`:intent-layer`) of another service that the list re-emits
//! * a constant of a translation list lists a definition of another service's translation layer
//!
//! DOEFF156 (where answers are placed):
//! * a translation handler re-emits a business effect — for intent-layer effects always; for others, those that no handler in the same list answers (excluding effects it answers itself) or
//!   those where every handler of the same list that answers the re-emitted effect is inner (after it in the list)
//! * a base handler (outside the translation list) listed by a set function of a set file answers a business effect
//!
//! Business effects, the table of the outside world, the assembly layer, and set files are read from the declaration of `:business-fakes` (DOEFF143), and names are read from `:assembly-shape`,
//! so no repo names are placed here. Reachability follows the definition-edge graph, the same as DOEFF143's, folded into top-level definitions. Whole runs only.
//! Limit of reading: the target of a translation is judged by the names of business effects that a definition body names (references that are not calls also count as re-emission).

use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Prefix, Reader};
use doeff_indexer::hy_index::{HyFileIndex, Range};

use super::architecture::{Architecture, AssemblyShape, BusinessFakes};
use super::business_fakes::{self, FileRole};
use super::names::{absolute_module, hy_mangle, module_of};
use super::paths::glob_matches;
use super::spans::innermost_definition;

/// Of the material for judgment, the part built from the whole definition graph (the caller builds it — judge_assembly_shape in project/mod.rs). It is received so that the graph's type and construction
/// stay in mod.rs while this module does not read mod.rs back (reading it back would make a dependency cycle).
pub struct GraphInputs<'h> {
    /// The order of the graph's files (the order of definition sections).
    pub rels: Vec<&'h String>,
    /// file → the number of that file's first definition section.
    pub base: HashMap<&'h str, usize>,
    /// The number of definition sections.
    pub nodes: usize,
    /// callees[n] = the sections reachable from section n (forward direction of edges).
    pub callees: Vec<Vec<usize>>,
    /// The effect clauses (built from the business fakes declaration).
    pub clauses: Vec<business_fakes::Clause>,
    /// file → (layer name, service name). Files outside layer locations are not included.
    pub layers: HashMap<String, (String, Option<String>)>,
}

/// One top-level definition (material for judgment).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Top {
    pub rel: String,
    pub qualified: String,
    /// The definition's name (the last segment of the full name — the mangled spelling).
    pub name: String,
    /// The full names of the names the body names.
    pub refs: BTreeSet<String>,
    /// The sections of business effects answered other than by tap (handler, effect) — excluding effects of the outside-world table.
    pub answers: Vec<(String, String)>,
    /// Whether it is production code.
    pub production: bool,
    /// The layer name the file belongs to, and the service dir of that location (None for a location where the layer comes first).
    pub layer: Option<(String, Option<String>)>,
    /// If the segment after root is a declared service dir, that dir (including old locations outside the layer locations).
    pub service_dir: Option<String>,
    /// Whether it is a set function of a set file (starts with the head word for production or fake).
    pub set_function: bool,
}

/// The whole material for judgment.
#[derive(Debug, Clone, Default)]
pub struct Model {
    pub tops: Vec<Top>,
    pub by_name: HashMap<String, usize>,
    /// edges[t] = the top-level definitions reachable from t's body.
    pub edges: Vec<Vec<usize>>,
    /// Set files (production code files matching :sets).
    pub set_files: BTreeSet<String>,
    /// The dir of the shared location (intents under it are not counted as another service's).
    pub shared: Option<String>,
}

/// The reading of one single point of a translation list.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranslationPoint {
    pub rel: String,
    /// The full name of the single point's defk.
    pub function: String,
    /// The full names of the translation lists listed (in listing order; the head is outermost).
    pub sequence: Vec<String>,
    /// Imported names listed with `#*` that are not translation lists (the spelling as written).
    pub strays: Vec<String>,
    /// The defk arguments of the single point listed with `#*` (the spelling as written).
    pub parameters: Vec<String>,
}

/// The reading of a single point of a translation list (one answer of read_points) — the point's name, the translation lists listed, imported names that are not translation lists, and arguments listed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PointRead {
    pub name: String,
    pub sequence: Vec<String>,
    pub strays: Vec<String>,
    pub parameters: Vec<String>,
}

/// One place outside the single point of a translation list where a translation list is opened with `#*` (the verification environment or a test file).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LooseList {
    pub rel: String,
    /// The full name of the top-level definition containing the opening place (None if outside a definition).
    pub at: Option<String>,
    /// The full name of the translation list that was opened.
    pub list: String,
}

/// Kinds of breach (a closed type — the rule, the key detail, and the text are decided from this).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Law {
    /// A set file remains.
    SetFile,
    /// A retired assembly function remains in the assembly layer.
    Retired,
    /// The single point of the translation list lists a name that is not a translation list.
    Stray { name: String },
    /// The single point of the translation list lists its own argument name.
    Parameter { name: String },
    /// The verification environment or a test file opens and lists the translation list list outside the single point.
    Loose { list: String },
    /// There is no list outside that answers the intent effect effect of another service that the list list re-emits.
    Order { list: String, effect: String },
    /// A translation list lists the translation reference of another service.
    OwnTranslations { reference: String, owner: String },
    /// A translation handler re-emits a business effect.
    GenericTarget { handler: String, effect: String },
    /// Every handler of the same list that answers the re-emitted effect is inner.
    InnerAnswerer { handler: String, effect: String, answerers: Vec<String> },
    /// A base handler listed by a set function answers a business effect.
    FoundationAnswer { function: String, handler: String, effect: String },
}

/// One breach (rel = the file of the finding, at = the full name of the definition from which the position is taken).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Breach {
    pub rel: String,
    pub at: Option<String>,
    pub law: Law,
}

impl Breach {
    /// DOEFF155 (shape) or DOEFF156 (where answers are placed).
    pub fn is_shape(&self) -> bool {
        !matches!(self.law, Law::GenericTarget { .. } | Law::InnerAnswerer { .. } | Law::FoundationAnswer { .. })
    }

    /// Detail of the registry key.
    pub fn detail(&self) -> String {
        match &self.law {
            Law::SetFile => "set-file".to_string(),
            Law::Retired => "retired".to_string(),
            Law::Stray { name } => format!("stray:{}", name),
            Law::Parameter { name } => format!("parameter:{}", name),
            Law::Loose { list } => format!("loose:{}", list),
            Law::Order { list, effect } => format!("order:{}:{}", list, effect),
            Law::OwnTranslations { reference, .. } => format!("own:{}", reference),
            Law::GenericTarget { handler, effect } => format!("target:{}:{}", handler, effect),
            Law::InnerAnswerer { handler, effect, .. } => format!("inner:{}:{}", handler, effect),
            Law::FoundationAnswer { function, handler, effect } => format!("foundation:{}:{}:{}", function, handler, effect),
        }
    }

    /// The subject of the notice (what this is) and the reason (why it is a violation).
    pub fn explain(&self, shape: &AssemblyShape) -> (String, String) {
        match &self.law {
            Law::SetFile => (
                format!("set file {}", self.rel),
                format!(
                    "assembly has not moved to the assembly layer's single point of the translation list {} (which lists {} of the translation layer) and to the base that wraps the body — the only difference between production and fakes is the base that wraps the body",
                    shape.translation_point, shape.translations
                ),
            ),
            Law::Retired => (
                format!("{} in {}", self.rel, shape.retired_function.as_deref().unwrap_or("")),
                format!("a retired assembly function remains — put the translation list in defk {} [… body], and put the base in the text that wraps the body", shape.translation_point),
            ),
            Law::Stray { name } => (
                format!("{} lists {} at the single point of the translation list", self.rel, name),
                format!("not a translation list — what `#*` may list is only {} imported from the translation layer (or a list of the owner of a service that has no location). Base handlers go in the text that wraps the body", shape.translations),
            ),
            Law::Parameter { name } => (
                format!("{} lists the argument {} at the single point of the translation list", self.rel, name),
                format!(
                    "it receives the translation list from the caller — callers (including fakes) can pass a list different from production. What `#*` may list is only {} imported from the translation layer. Split the point for lists that differ by caller",
                    shape.translations
                ),
            ),
            Law::Loose { list } => (
                format!("{} opens and lists the translation list {}", self.rel, list),
                format!(
                    "it lists a translation list outside the single point of the translation list ({}) — fakes and tests call the same single point as production (if the list is assembled by itself, the other lists, the order, and the settings reading that the point lists together are not reflected, and the assembly differs from production)",
                    shape.translation_point
                ),
            ),
            Law::Order { list, effect } => (
                format!("{} lists the list {} at the single point of the translation list", self.rel, list),
                format!("it re-emits the intent effect {} of another service, but there is no list outside (before) it that answers it — the effect passes through to the base and has no answerer", effect),
            ),
            Law::OwnTranslations { reference, owner } => (
                format!("{} in {}", self.rel, shape.translations),
                format!("it lists the translation {} of another service {} — a translation list holds only its own service's translations. Lists of other services are listed by the assembly layer", owner, reference),
            ),
            Law::GenericTarget { handler, effect } => (
                format!("{} translation handler {}", self.rel, handler),
                format!(
                    "it re-emits the business effect {} — the target of a translation is only generic effects that know nothing of the business. Business flows that emit intent effects go in core programs (effects from old non-intent locations may be re-emitted if a handler in the same list answers them)",
                    effect
                ),
            ),
            Law::InnerAnswerer { handler, effect, answerers } => (
                format!("{} translation handler {}", self.rel, handler),
                format!("every handler {} in the same list that answers the re-emitted {} is inner (after it in the list) — only an outer handler can answer a re-emitted effect", answerers.join(", "), effect),
            ),
            Law::FoundationAnswer { function, handler, effect } => (
                format!("{} base handler {} of {}", self.rel, handler, function),
                format!("it answers the business effect {} — a base handler answers only effects of the outside world. Answers to business effects go in the translation handlers of {}", effect, shape.translations),
            ),
        }
    }
}

/// The top-level definitions reachable from roots (including roots).
fn reach(model: &Model, roots: &[usize]) -> BTreeSet<usize> {
    let mut seen: BTreeSet<usize> = roots.iter().copied().collect();
    let mut queue: VecDeque<usize> = roots.iter().copied().collect();
    while let Some(top) = queue.pop_front() {
        for &next in &model.edges[top] {
            if seen.insert(next) {
                queue.push_back(next);
            }
        }
    }
    seen
}

/// The service at the translation layer's location (None for layer-first locations and other layers).
fn translation_owner<'m>(top: &'m Top, shape: &AssemblyShape) -> Option<&'m str> {
    match &top.layer {
        Some((layer, Some(service))) if *layer == shape.translation_layer => Some(service.as_str()),
        _ => None,
    }
}

/// Whether the top-level definition is production code outside layer locations (under a declared service dir but matching no layer — an old location).
fn unplaced(top: &Top) -> bool {
    top.production && top.layer.is_none() && top.service_dir.is_some()
}

/// Whether the effect effect has been placed in the intent layer (regardless of service).
fn intent_effect(model: &Model, shape: &AssemblyShape, effect: &str) -> bool {
    model.by_name.get(effect).is_some_and(|&at| matches!(&model.tops[at].layer, Some((layer, _)) if *layer == shape.intent_layer))
}

/// Whether the effect effect is an intent effect of a service other than owner (excluding the shared location and layer-first locations).
fn other_service_intent(model: &Model, shape: &AssemblyShape, effect: &str, owner: &str) -> bool {
    let Some(&at) = model.by_name.get(effect) else { return false };
    match &model.tops[at].layer {
        Some((layer, Some(service))) => *layer == shape.intent_layer && service != owner && model.shared.as_deref() != Some(service.as_str()),
        _ => false,
    }
}

/// Whether it is a translation list the single point of the translation list may list: a constant of a translation list in a translation-layer module, or a list in a module of the owner of a service with no location, where
/// all elements are handlers outside the location that answers business effects (a transitional clause — it disappears once the move is done; #827).
pub fn translation_list(model: &Model, shape: &AssemblyShape, target: &str) -> bool {
    let Some(&at) = model.by_name.get(target) else { return false };
    let top = &model.tops[at];
    if top.name == hy_mangle(&shape.translations) && matches!(&top.layer, Some((layer, _)) if *layer == shape.translation_layer) {
        return true;
    }
    let elements: Vec<&String> = top.refs.iter().filter(|r| *r != target).collect();
    unplaced(top)
        && !elements.is_empty()
        && elements.iter().all(|r| model.by_name.get(r.as_str()).is_some_and(|&e| !model.tops[e].answers.is_empty() && unplaced(&model.tops[e])))
}

/// Judge. retired = (file, full name) of retired functions remaining in the assembly layer; points = readings of single points of translation lists;
/// loose = translation lists opened outside the single point by the verification environment and test files; orders = the translation list constant's full name → element name → position.
pub fn judge(
    model: &Model,
    shape: &AssemblyShape,
    retired: &[(String, String)],
    points: &[TranslationPoint],
    loose: &[LooseList],
    orders: &HashMap<String, HashMap<String, usize>>,
) -> Vec<Breach> {
    let mut out = Vec::new();
    let business_answered: BTreeSet<&str> = model.tops.iter().flat_map(|t| t.answers.iter().map(|(_, e)| e.as_str())).collect();
    let answers_of = |tops: &BTreeSet<usize>| -> BTreeSet<&str> { tops.iter().flat_map(|&t| model.tops[t].answers.iter().map(|(_, e)| e.as_str())).collect() };
    // A set file is a breach by itself (it disappears with the file once the move is done).
    for rel in &model.set_files {
        out.push(Breach { rel: rel.clone(), at: None, law: Law::SetFile });
    }
    for (rel, function) in retired {
        out.push(Breach { rel: rel.clone(), at: Some(function.clone()), law: Law::Retired });
    }
    // The single point of the translation list: names that are not translation lists, and the ordering among lists (the same key is not emitted twice).
    let mut emitted: BTreeSet<(String, String)> = BTreeSet::new();
    for point in points {
        let named = point.strays.iter().map(|name| Law::Stray { name: name.clone() });
        let given = point.parameters.iter().map(|name| Law::Parameter { name: name.clone() });
        for law in named.chain(given) {
            let breach = Breach { rel: point.rel.clone(), at: Some(point.function.clone()), law };
            if emitted.insert((breach.rel.clone(), breach.detail())) {
                out.push(breach);
            }
        }
        let mut outside: BTreeSet<&str> = BTreeSet::new();
        for list in &point.sequence {
            let Some(&at) = model.by_name.get(list) else { continue };
            let reached = reach(model, &[at]);
            let own = answers_of(&reached);
            if let Some(owner) = model.tops[at].service_dir.as_deref() {
                let reyields: BTreeSet<&str> = reached
                    .iter()
                    .filter(|&&t| !model.tops[t].answers.is_empty())
                    .flat_map(|&t| model.tops[t].refs.iter().map(String::as_str))
                    .filter(|r| business_answered.contains(r) && !own.contains(r) && other_service_intent(model, shape, r, owner))
                    .collect();
                for effect in reyields.difference(&outside) {
                    let breach = Breach {
                        rel: point.rel.clone(),
                        at: Some(point.function.clone()),
                        law: Law::Order { list: list.clone(), effect: effect.to_string() },
                    };
                    if emitted.insert((breach.rel.clone(), breach.detail())) {
                        out.push(breach);
                    }
                }
            }
            outside.extend(own);
        }
    }
    // Translation lists opened outside the single point (the same list in the same file only once).
    for found in loose {
        let breach = Breach { rel: found.rel.clone(), at: found.at.clone(), law: Law::Loose { list: found.list.clone() } };
        if emitted.insert((breach.rel.clone(), breach.detail())) {
            out.push(breach);
        }
    }
    let constant = hy_mangle(&shape.translations);
    let mut constants: Vec<usize> = (0..model.tops.len()).filter(|&t| model.tops[t].production && model.tops[t].name == constant).collect();
    constants.sort_by(|a, b| model.tops[*a].qualified.cmp(&model.tops[*b].qualified));
    // A translation list holds only its own service's translations.
    for &q in &constants {
        let Some(owner) = translation_owner(&model.tops[q], shape) else { continue };
        for reference in &model.tops[q].refs {
            let Some(&other) = model.by_name.get(reference) else { continue };
            if let Some(other_owner) = translation_owner(&model.tops[other], shape).filter(|o| *o != owner) {
                out.push(Breach {
                    rel: model.tops[q].rel.clone(),
                    at: Some(model.tops[q].qualified.clone()),
                    law: Law::OwnTranslations { reference: reference.clone(), owner: other_owner.to_string() },
                });
            }
        }
    }
    // The re-emission targets and order of translation handlers.
    for &q in &constants {
        let list = &model.tops[q];
        let handlers: Vec<usize> = reach(model, &[q]).into_iter().filter(|&t| !model.tops[t].answers.is_empty()).collect();
        let translated: BTreeSet<&str> = handlers.iter().flat_map(|&t| model.tops[t].answers.iter().map(|(_, e)| e.as_str())).collect();
        let order = orders.get(&list.qualified);
        for &t in &handlers {
            let top = &model.tops[t];
            let own: BTreeSet<&str> = top.answers.iter().map(|(_, e)| e.as_str()).collect();
            let position = order.and_then(|o| o.get(&top.name)).copied();
            for reference in top.refs.iter().map(String::as_str).filter(|r| business_answered.contains(r) && !own.contains(r)) {
                if intent_effect(model, shape, reference) || !translated.contains(reference) {
                    out.push(Breach {
                        rel: list.rel.clone(),
                        at: Some(top.qualified.clone()),
                        law: Law::GenericTarget { handler: top.name.clone(), effect: reference.to_string() },
                    });
                }
                let (Some(position), Some(order)) = (position, order) else { continue };
                if !translated.contains(reference) {
                    continue;
                }
                let answerers: Vec<&Top> =
                    handlers.iter().map(|&h| &model.tops[h]).filter(|h| h.qualified != top.qualified && h.answers.iter().any(|(_, e)| e == reference)).collect();
                let positions: Vec<usize> = answerers.iter().filter_map(|h| order.get(&h.name).copied()).collect();
                if !positions.is_empty() && positions.len() == answerers.len() && positions.iter().all(|p| *p >= position) {
                    out.push(Breach {
                        rel: list.rel.clone(),
                        at: Some(top.qualified.clone()),
                        law: Law::InnerAnswerer {
                            handler: top.name.clone(),
                            effect: reference.to_string(),
                            answerers: answerers.iter().map(|h| h.name.clone()).collect(),
                        },
                    });
                }
            }
        }
    }
    // A base handler (outside the translation list) listed by a set function of a set file does not answer business effects.
    for (t, function) in model.tops.iter().enumerate().filter(|(_, t)| t.production && t.set_function) {
        let module = function.qualified.rsplit_once('.').map(|(m, _)| m).unwrap_or("");
        let translation = model.by_name.get(&format!("{}.{}", module, constant)).map(|&c| reach(model, &[c])).unwrap_or_default();
        let foundation = reach(model, &model.edges[t]);
        let mut found: Vec<(&str, &str)> = foundation
            .difference(&translation)
            .flat_map(|&f| model.tops[f].answers.iter().map(|(h, e)| (h.as_str(), e.as_str())))
            .collect();
        found.sort();
        found.dedup();
        for (handler, effect) in found {
            out.push(Breach {
                rel: function.rel.clone(),
                at: Some(function.qualified.clone()),
                law: Law::FoundationAnswer { function: function.name.clone(), handler: handler.to_string(), effect: effect.to_string() },
            });
        }
    }
    out
}

fn text<'s>(source: &'s str, form: &Form) -> &'s str {
    &source[form.span.start..form.span.end]
}

/// A form with the type annotation stripped (the name in `#^ T name`).
fn bare(form: &Form) -> &Form {
    match &form.node {
        Node::Annotated { target: Some(target), .. } => bare(target),
        _ => form,
    }
}

fn symbol<'s>(source: &'s str, form: &Form) -> Option<&'s str> {
    matches!(bare(form).node, Node::Symbol).then(|| text(source, bare(form)))
}

/// Mangle a dotted spelling per segment (`p.TRANSLATION-HANDLERS` → `p.TRANSLATION_HANDLERS`).
fn mangle_dotted(spelled: &str) -> String {
    spelled.split('.').map(hy_mangle).collect::<Vec<_>>().join(".")
}

/// Whether a name matches a name pattern (`*` is a single one) (compared between mangled spellings).
fn name_matches(pattern: &str, name: &str) -> bool {
    let Some((head, tail)) = pattern.split_once('*') else { return hy_mangle(pattern) == name };
    // A fragment is in the middle of a name, so a leading or trailing `-` is mapped to `_` rather than treated as a special mangle spelling.
    let (head, tail) = (head.replace('-', "_"), tail.replace('-', "_"));
    name.len() > head.len() + tail.len() && name.starts_with(&head) && name.ends_with(&tail)
}

/// The literal of the handler list of the first `(with-handlers [..] ..)` appearing in a form.
fn handler_list<'f>(source: &str, form: &'f Form) -> Option<&'f [Form]> {
    let Node::Seq { items, .. } = &form.node else { return None };
    if let (Some(head), Some(list)) = (form.paren_items().and_then(|i| i.first()), items.get(1)) {
        if matches!(symbol(source, head), Some("with-handlers" | "with_handlers")) {
            if let Some(listed) = list.bracket_items() {
                return Some(listed);
            }
        }
    }
    items.iter().find_map(|item| match &item.node {
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => handler_list(source, inner),
        _ => handler_list(source, item),
    })
}

/// The names (mangled spellings) of the defk's argument list (`[a #^ T b * c]`).
fn parameter_names(source: &str, params: &Form) -> BTreeSet<String> {
    params.bracket_items().unwrap_or(&[]).iter().filter_map(|p| symbol(source, p)).filter(|n| *n != "*" && *n != "/").map(hy_mangle).collect()
}

/// Read the single point of the translation list of a file. resolve = imported name (mangled dotted spelling) → the full name it points to,
/// accepts = whether to accept it as a translation list. The answer = a reading per point (translation lists listed, imported names that are not translation lists, and arguments listed).
pub fn read_points(
    source: &str,
    shape: &AssemblyShape,
    resolve: &dyn Fn(&str) -> Option<String>,
    accepts: &dyn Fn(&str) -> bool,
) -> Vec<PointRead> {
    let forms = Reader::new(source, 0, source.len()).read_all();
    // A val / setv that lists a list directly under the same module (opened with `#*`).
    let locals: HashMap<String, &[Form]> = forms
        .iter()
        .filter_map(|form| {
            let [head, name, value] = form.paren_items()? else { return None };
            matches!(symbol(source, head)?, "val" | "setv").then_some(())?;
            Some((hy_mangle(symbol(source, name)?), value.bracket_items()?))
        })
        .collect();
    let mut points = Vec::new();
    for form in &forms {
        let Some(items) = form.paren_items() else { continue };
        let (Some("defk"), Some(name)) = (items.first().and_then(|h| symbol(source, h)), items.get(1).and_then(|n| symbol(source, n))) else { continue };
        let name = hy_mangle(name);
        if !name_matches(&shape.translation_point, &name) {
            continue;
        }
        let Some(listed) = items[2..].iter().find_map(|item| handler_list(source, item)) else { continue };
        let parameters_of = items.get(2).map(|p| parameter_names(source, p)).unwrap_or_default();
        let (mut sequence, mut strays, mut parameters) = (Vec::new(), Vec::new(), Vec::new());
        let mut pending: VecDeque<&Form> = listed.iter().collect();
        let mut opened: BTreeSet<String> = BTreeSet::new();
        while let Some(item) = pending.pop_front() {
            let Node::Prefixed { prefix: Prefix::Unpack, inner: Some(inner) } = &item.node else { continue };
            let Some(spelled) = symbol(source, inner).map(mangle_dotted) else { continue };
            // Arguments take precedence over imports and vals of the same name (a name in the body points to the argument).
            if parameters_of.contains(&spelled) {
                parameters.push(spelled);
                continue;
            }
            match (resolve(&spelled), locals.get(&spelled)) {
                (Some(target), _) if accepts(&target) => sequence.push(target),
                (_, Some(local)) => {
                    if opened.insert(spelled.clone()) {
                        for element in local.iter().rev() {
                            pending.push_front(element);
                        }
                    }
                }
                (Some(_), None) => strays.push(spelled),
                (None, None) => {}
            }
        }
        points.push(PointRead { name, sequence, strays, parameters });
    }
    points
}

/// Of the `#* name` in a form, collect in order of discovery those that resolve resolves to a full name and accepts accepts as a translation list.
fn unpacked_lists(source: &str, form: &Form, resolve: &dyn Fn(&str) -> Option<String>, accepts: &dyn Fn(&str) -> bool, out: &mut Vec<String>) {
    match &form.node {
        Node::Prefixed { prefix: Prefix::Unpack, inner: Some(inner) } => {
            if let Some(target) = symbol(source, inner).map(mangle_dotted).and_then(|spelled| resolve(&spelled)).filter(|t| accepts(t)) {
                out.push(target);
            }
            unpacked_lists(source, inner, resolve, accepts, out);
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } | Node::Annotated { target: Some(inner), .. } => {
            unpacked_lists(source, inner, resolve, accepts, out)
        }
        Node::Seq { items, .. } => items.iter().for_each(|item| unpacked_lists(source, item, resolve, accepts, out)),
        _ => {}
    }
}

/// In the verification environment or a test file, read the places where a translation list is opened and listed with `#*` (outside the single point of the translation list — the point exists only in the assembly layer).
/// The answer = a list of (the name [mangled spelling; None if outside a definition] of the top-level definition containing the opening place, the full name of the translation list that was opened).
pub fn loose_lists(source: &str, resolve: &dyn Fn(&str) -> Option<String>, accepts: &dyn Fn(&str) -> bool) -> Vec<(Option<String>, String)> {
    let mut found = Vec::new();
    for form in Reader::new(source, 0, source.len()).read_all() {
        let mut lists = Vec::new();
        unpacked_lists(source, &form, resolve, accepts, &mut lists);
        let defined = form.paren_items().and_then(|items| {
            let head = symbol(source, items.first()?)?;
            (head.starts_with("def") || matches!(head, "val" | "var" | "setv")).then(|| symbol(source, items.get(1)?)).flatten().map(hy_mangle)
        });
        found.extend(lists.into_iter().map(|list| (defined.clone(), list)));
    }
    found
}

/// The order of elements of a translation list constant (element name (a symbol, or the head of a call that makes a handler) → first position). None if not a literal.
pub fn list_order(source: &str, constant: &str) -> Option<HashMap<String, usize>> {
    let wanted = hy_mangle(constant);
    Reader::new(source, 0, source.len()).read_all().iter().find_map(|form| {
        let [head, name, value] = form.paren_items()? else { return None };
        if !matches!(symbol(source, head)?, "val" | "setv") || hy_mangle(symbol(source, name)?) != wanted {
            return None;
        }
        let Node::Seq { delim: Delim::Bracket | Delim::Tuple, items } = &value.node else { return None };
        let mut order = HashMap::new();
        for (i, item) in items.iter().enumerate() {
            let name = symbol(source, item).or_else(|| item.paren_items().and_then(|p| p.first()).and_then(|h| symbol(source, h)));
            if let Some(name) = name {
                order.entry(hy_mangle(name)).or_insert(i);
            }
        }
        Some(order)
    })
}

/// The names (mangled spellings) bound by a file's imports → the full names they point to (b of `(import m [a :as b])` → m.a, p of `(import m :as p)` → m).
fn import_names(file: &HyFileIndex) -> HashMap<String, String> {
    let mut names = HashMap::new();
    for import in file.imports.iter().filter(|i| !i.is_require) {
        let module = absolute_module(&file.module, &import.module);
        match (&import.name, &import.alias) {
            (Some(name), alias) => {
                names.insert(hy_mangle(alias.as_deref().unwrap_or(name)), format!("{}.{}", module, hy_mangle(name)));
            }
            (None, Some(alias)) => {
                names.insert(hy_mangle(alias), module.clone());
            }
            (None, None) => {
                names.insert(module.clone(), module.clone());
            }
        }
    }
    names
}

/// Judge from the index and the material graph that the caller built from the definition graph (whole runs only). The reason for a table that could not be read is returned as the second value.
pub fn find(
    root: &Path,
    architecture: &Architecture,
    decl: &BusinessFakes,
    shape: &AssemblyShape,
    hy: &HashMap<String, HyFileIndex>,
    graph: GraphInputs,
) -> (Vec<Breach>, Vec<String>) {
    let mut problems = Vec::new();
    let external: BTreeMap<String, String> = match &decl.external_effects {
        Some(dir) => {
            let judged = super::registry::JudgedKeys::load(root, std::slice::from_ref(dir), super::registry::Absent::Unreadable);
            problems.extend(judged.problems);
            judged.reasons
        }
        None => BTreeMap::new(),
    };
    let GraphInputs { rels, base, nodes, callees, clauses, layers } = graph;
    let arch_root = super::layers::normalize_dir(&architecture.root);
    // Fold into top-level definitions per section (definitions in the same file whose range is not contained in another definition).
    let mut top_of: Vec<usize> = vec![0; nodes];
    let mut model = Model { shared: architecture.shared.clone(), ..Model::default() };
    for rel in &rels {
        let file = &hy[rel.as_str()];
        let first = base[rel.as_str()];
        let mut order: Vec<usize> = (0..file.definitions.len()).collect();
        order.sort_by(|a, b| {
            let (a, b) = (&file.definitions[*a].full_range, &file.definitions[*b].full_range);
            a.start.cmp(&b.start).then(b.end.cmp(&a.end))
        });
        let production = business_fakes::role_of(rel, decl) != FileRole::Skipped && business_fakes::production_code(rel, decl);
        let layer = layers.get(rel.as_str()).cloned();
        let service_dir = rel
            .strip_prefix(&format!("{}/", arch_root))
            .and_then(|rest| rest.split_once('/'))
            .map(|(dir, _)| dir)
            .filter(|dir| architecture.service_by_dir(dir).is_some())
            .map(str::to_string);
        // They are sorted by start, so a definition that falls within the current top-level definition's range is a definition inside it.
        let mut current: Option<(usize, Range)> = None;
        for index in order {
            let d = &file.definitions[index];
            let enclosing = current.filter(|(_, range)| range.start <= d.full_range.start && d.full_range.end <= range.end).map(|(c, _)| c);
            let top = match enclosing {
                Some(c) => c,
                None => {
                    model.by_name.entry(d.qualified_name.clone()).or_insert(model.tops.len());
                    model.tops.push(Top {
                        rel: rel.to_string(),
                        qualified: d.qualified_name.clone(),
                        name: d.qualified_name.rsplit('.').next().unwrap_or("").to_string(),
                        production,
                        layer: layer.clone(),
                        service_dir: service_dir.clone(),
                        set_function: business_fakes::set_member(rel, &d.name, decl.production_prefix.as_deref(), decl)
                            || business_fakes::set_member(rel, &d.name, decl.simulation_prefix.as_deref(), decl),
                        ..Top::default()
                    });
                    current = Some((model.tops.len() - 1, d.full_range));
                    model.tops.len() - 1
                }
            };
            top_of[first + index] = top;
        }
        // Names the body names (references and calls) go to the top-level definitions.
        for reference in &file.references {
            let Some(target) = reference.target.as_ref() else { continue };
            if let Some(owner) = innermost_definition(&file.definitions, &reference.range) {
                model.tops[top_of[first + owner]].refs.insert(target.clone());
            }
        }
        for call in &file.calls {
            let (Some(target), Some(owner)) = (call.target.as_ref(), call.caller) else { continue };
            model.tops[top_of[first + owner]].refs.insert(target.clone());
        }
        if production && decl.sets.iter().any(|s| glob_matches(s, rel)) {
            model.set_files.insert(rel.to_string());
        }
    }
    model.edges = vec![Vec::new(); model.tops.len()];
    for (node, next) in callees.iter().enumerate() {
        for &n in next {
            let (from, to) = (top_of[node], top_of[n]);
            if from != to && !model.edges[from].contains(&to) {
                model.edges[from].push(to);
            }
        }
    }
    for clause in clauses.iter().filter(|c| !c.shape.forwards && !external.contains_key(&c.effect)) {
        if business_fakes::business_module(business_fakes::module_of_effect(&clause.effect), decl) {
            model.tops[top_of[clause.node]].answers.push((clause.handler.clone(), clause.effect.clone()));
        }
    }
    // Retired functions and single points of translation lists in files of the assembly layer (excluding tests).
    let mut retired = Vec::new();
    let mut points = Vec::new();
    let assembly: Vec<&String> = rels.iter().copied().filter(|r| business_fakes::role_of(r, decl) == FileRole::Assembly).collect();
    for rel in &assembly {
        if let Some(function) = &shape.retired_function {
            let qualified = format!("{}.{}", module_of(rel), hy_mangle(function));
            if model.by_name.contains_key(&qualified) {
                retired.push((rel.to_string(), qualified));
            }
        }
    }
    for rel in assembly.iter().filter(|r| r.ends_with(".hy")) {
        let file = &hy[rel.as_str()];
        if !file.definitions.iter().any(|d| d.container.is_none() && name_matches(&shape.translation_point, &d.mangled)) {
            continue;
        }
        let Ok(source) = std::fs::read_to_string(root.join(rel.as_str())) else { continue };
        let names = import_names(file);
        let resolve = |spelled: &str| resolved(&names, spelled);
        let accepts = |target: &str| translation_list(&model, shape, target);
        for read in read_points(&source, shape, &resolve, &accepts) {
            points.push(TranslationPoint {
                rel: rel.to_string(),
                function: format!("{}.{}", module_of(rel), read.name),
                sequence: read.sequence,
                strays: read.strays,
                parameters: read.parameters,
            });
        }
    }
    // Translation lists opened outside the single point by the verification environment and test files (what is read is only files that are translation lists or import a module that has one). Lists counted =
    // lists the single point may list, and lists in which the assembly layer bundles translation-layer handlers or translation lists (e.g. entry's <name>-TRANSLATIONS — if a fake opens it,
    // it assembles part of the production list outside the single point).
    let translation_layer = |name: &str| {
        model.by_name.get(name).is_some_and(|&at| matches!(&model.tops[at].layer, Some((layer, _)) if *layer == shape.translation_layer))
    };
    let bundles = |top: &Top| {
        business_fakes::role_of(&top.rel, decl) == FileRole::Assembly
            && !name_matches(&shape.translation_point, &top.name)
            && top.refs.iter().any(|r| r != &top.qualified && (translation_layer(r) || translation_list(&model, shape, r)))
    };
    let lists: BTreeSet<&str> =
        model.tops.iter().filter(|t| translation_list(&model, shape, &t.qualified) || bundles(t)).map(|t| t.qualified.as_str()).collect();
    let list_modules: BTreeSet<&str> = lists.iter().filter_map(|q| q.rsplit_once('.').map(|(m, _)| m)).collect();
    let mut loose = Vec::new();
    let mut checked: Vec<&String> = hy
        .keys()
        .filter(|r| r.ends_with(".hy") && (architecture.in_verification_environment(r) || business_fakes::role_of(r, decl) == FileRole::Test))
        .collect();
    checked.sort();
    for rel in checked {
        let file = &hy[rel.as_str()];
        let names = import_names(file);
        if !names.values().any(|target| lists.contains(target.as_str()) || list_modules.contains(target.as_str())) {
            continue;
        }
        let Ok(source) = std::fs::read_to_string(root.join(rel.as_str())) else { continue };
        let resolve = |spelled: &str| resolved(&names, spelled);
        let accepts = |target: &str| lists.contains(target);
        for (defined, list) in loose_lists(&source, &resolve, &accepts) {
            loose.push(LooseList { rel: rel.to_string(), at: defined.map(|name| format!("{}.{}", module_of(rel), name)), list });
        }
    }
    // The order of elements of translation list constants.
    let constant = hy_mangle(&shape.translations);
    let mut orders = HashMap::new();
    for top in model.tops.iter().filter(|t| t.production && t.name == constant && t.rel.ends_with(".hy")) {
        let Ok(source) = std::fs::read_to_string(root.join(&top.rel)) else { continue };
        if let Some(order) = list_order(&source, &shape.translations) {
            orders.insert(top.qualified.clone(), order);
        }
    }
    (judge(&model, shape, &retired, &points, &loose, &orders), problems)
}

/// Resolve an imported name (mangled dotted spelling) to the full name it points to (`p.X` is X of the imported module p).
fn resolved(names: &HashMap<String, String>, spelled: &str) -> Option<String> {
    names.get(spelled).cloned().or_else(|| {
        let (head, rest) = spelled.split_once('.')?;
        names.get(head).map(|module| format!("{}.{}", module, rest))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shape() -> AssemblyShape {
        AssemblyShape {
            translation_point: "with-*-translation".into(),
            retired_function: Some("handlers-of".into()),
            translations: "TRANSLATION-HANDLERS".into(),
            translation_layer: "protocol".into(),
            intent_layer: "intent".into(),
        }
    }

    fn top(rel: &str, qualified: &str, layer: Option<(&str, &str)>, answers: &[&str], refs: &[&str]) -> Top {
        let name = qualified.rsplit('.').next().unwrap().to_string();
        Top {
            rel: rel.into(),
            qualified: qualified.into(),
            refs: refs.iter().map(|r| r.to_string()).collect(),
            answers: answers.iter().map(|e| (name.clone(), e.to_string())).collect(),
            name,
            production: true,
            layer: layer.map(|(l, s)| (l.to_string(), Some(s.to_string()))),
            service_dir: rel.split('/').nth(1).map(str::to_string),
            set_function: false,
        }
    }

    /// Translation list A (h1, h2), B (bi), the intent effect I of another service, old-location effects E1 to E3, and a set file's base handler.
    fn model() -> Model {
        let a = "app/a/protocol/translations.hy";
        let b = "app/b/protocol/translations.hy";
        let mut tops = vec![
            top(a, "app.a.protocol.translations.TRANSLATION_HANDLERS", Some(("protocol", "a")), &[], &[
                "app.a.protocol.translations.h1",
                "app.a.protocol.translations.h2",
                "app.b.protocol.translations.bi",
            ]),
            top(a, "app.a.protocol.translations.h1", Some(("protocol", "a")), &["app.old.E1"], &["app.old.E1", "app.b.intent.I", "app.old.E2", "app.old.E3"]),
            top(a, "app.a.protocol.translations.h2", Some(("protocol", "a")), &["app.old.E2"], &[]),
            top(b, "app.b.protocol.translations.TRANSLATION_HANDLERS", Some(("protocol", "b")), &[], &["app.b.protocol.translations.bi"]),
            top(b, "app.b.protocol.translations.bi", Some(("protocol", "b")), &["app.b.intent.I"], &[]),
            top("app/b/intent/effects.hy", "app.b.intent.I", Some(("intent", "b")), &[], &[]),
            top("app/c/core/other.hy", "app.c.core.other.other", Some(("core", "c")), &["app.old.E3"], &[]),
            top("app/a/handler_sets.hy", "app.a.handler_sets.production_handlers", None, &[], &[]),
            top("app/a/handler_sets.hy", "app.a.handler_sets.fh", None, &["app.old.E1"], &[]),
        ];
        tops[7].set_function = true;
        let by_name = tops.iter().enumerate().map(|(i, t)| (t.qualified.clone(), i)).collect();
        let mut edges = vec![Vec::new(); tops.len()];
        edges[0] = vec![1, 2];
        edges[3] = vec![4];
        edges[7] = vec![8];
        Model { tops, by_name, edges, set_files: ["app/a/handler_sets.hy".to_string()].into(), shared: Some("shared".into()) }
    }

    #[test]
    fn names_and_lists_are_read_from_the_source() {
        assert!(name_matches("with-*-translation", "with_land_notice_translation"));
        assert!(!name_matches("with-*-translation", "with_translation"));
        assert!(!name_matches("with-*-translation", "land_notice_translation"));
        let source = "(val LOCAL [#* ORDERS-LIST])\n\
                      (defk with-orders-translation [body]\n  \"doc\"\n  {:pre []}\n  (<- answer (with-handlers [#* p.TRANSLATION-HANDLERS #* LOCAL #* POSTING-HANDLERS (reader) #* unknown] body))\n  answer)\n\
                      (defk other [body] (with-handlers [#* POSTING-HANDLERS] body))\n\
                      (defk with-given-translation [#^ list translations body] (with-handlers [#* p.TRANSLATION-HANDLERS #* translations] body))\n";
        let resolve = |spelled: &str| match spelled {
            "p.TRANSLATION_HANDLERS" => Some("app.a.protocol.t.TRANSLATION_HANDLERS".to_string()),
            "ORDERS_LIST" => Some("app.orders.handlers.ORDERS_LIST".to_string()),
            "POSTING_HANDLERS" => Some("app.m.protocol.t.POSTING_HANDLERS".to_string()),
            _ => None,
        };
        let accepts = |target: &str| target.ends_with("TRANSLATION_HANDLERS") || target.ends_with("ORDERS_LIST");
        let points = read_points(source, &shape(), &resolve, &accepts);
        assert_eq!(
            points,
            vec![
                PointRead {
                    name: "with_orders_translation".to_string(),
                    sequence: vec!["app.a.protocol.t.TRANSLATION_HANDLERS".to_string(), "app.orders.handlers.ORDERS_LIST".to_string()],
                    strays: vec!["POSTING_HANDLERS".to_string()],
                    parameters: vec![],
                },
                // A single point that receives the list as an argument (#3408): the argument is read as an argument, neither a translation list nor an imported name.
                PointRead {
                    name: "with_given_translation".to_string(),
                    sequence: vec!["app.a.protocol.t.TRANSLATION_HANDLERS".to_string()],
                    strays: vec![],
                    parameters: vec!["translations".to_string()],
                },
            ]
        );
        // Translation lists opened outside the single point (#3408): per definition, pick up only translation lists (lists that are not translation lists, and references that do not open them, are not picked up).
        let loose_source = "(defk world [clock] (agent-world clock [#* lower #* p.TRANSLATION-HANDLERS]))\n\
                            (deftest test-x (with-handlers [#* POSTING-HANDLERS (spy)] (run (get p.TRANSLATION-HANDLERS 0))))\n\
                            (val ALL [#* ORDERS-LIST])\n\
                            (with-handlers [#* p.TRANSLATION-HANDLERS] (body))\n";
        assert_eq!(
            loose_lists(loose_source, &resolve, &accepts),
            vec![
                (Some("world".to_string()), "app.a.protocol.t.TRANSLATION_HANDLERS".to_string()),
                (Some("ALL".to_string()), "app.orders.handlers.ORDERS_LIST".to_string()),
                (None, "app.a.protocol.t.TRANSLATION_HANDLERS".to_string()),
            ]
        );
        let order = list_order("(val TRANSLATION-HANDLERS [h-one (h-two x) h-one])", "TRANSLATION-HANDLERS").unwrap();
        assert_eq!((order["h_one"], order["h_two"]), (0, 1));
        assert!(list_order("(val TRANSLATION-HANDLERS (make))", "TRANSLATION-HANDLERS").is_none());
    }

    #[test]
    fn every_law_is_judged() {
        let model = model();
        let a = "app.a.protocol.translations.TRANSLATION_HANDLERS";
        let b = "app.b.protocol.translations.TRANSLATION_HANDLERS";
        let retired = vec![("app/a/entry/old.hy".to_string(), "app.a.entry.old.handlers_of".to_string())];
        let points = vec![
            // If A is listed outside B, there is no list outside that answers the intent I of b that A re-emits.
            TranslationPoint {
                rel: "app/a/entry/x.hy".into(),
                function: "app.a.entry.x.with_a_translation".into(),
                sequence: vec![a.into(), b.into()],
                strays: vec!["POSTING_HANDLERS".into()],
                parameters: vec![],
            },
            // Listing B outside makes the order fit. A single point that receives the list as an argument is a breach by the argument's name (#3408).
            TranslationPoint {
                rel: "app/a/entry/y.hy".into(),
                function: "app.a.entry.y.with_a_translation".into(),
                sequence: vec![b.into(), a.into()],
                strays: vec![],
                parameters: vec!["translations".into()],
            },
        ];
        // Translation lists opened outside the single point by the verification environment (the same list in the same file only once — #3408).
        let loose = vec![
            LooseList { rel: "sim/world.hy".into(), at: Some("sim.world.world".into()), list: a.into() },
            LooseList { rel: "sim/world.hy".into(), at: Some("sim.world.other".into()), list: a.into() },
        ];
        let orders: HashMap<String, HashMap<String, usize>> = [(a.to_string(), [("h1".to_string(), 0), ("h2".to_string(), 1)].into())].into();
        let mut found: Vec<(String, String, bool)> =
            judge(&model, &shape(), &retired, &points, &loose, &orders).iter().map(|b| (b.rel.clone(), b.detail(), b.is_shape())).collect();
        found.sort();
        let t = "app/a/protocol/translations.hy";
        let mut wanted: Vec<(String, String, bool)> = vec![
            ("app/a/handler_sets.hy".into(), "set-file".into(), true),
            ("app/a/handler_sets.hy".into(), "foundation:production_handlers:fh:app.old.E1".into(), false),
            ("app/a/entry/old.hy".into(), "retired".into(), true),
            ("app/a/entry/x.hy".into(), "stray:POSTING_HANDLERS".into(), true),
            ("app/a/entry/y.hy".into(), "parameter:translations".into(), true),
            ("sim/world.hy".into(), format!("loose:{}", a), true),
            ("app/a/entry/x.hy".into(), format!("order:{}:app.b.intent.I", a), true),
            (t.into(), "own:app.b.protocol.translations.bi".into(), true),
            // Intent effects always; E3, which the same list does not answer; and E2 of the old location may be re-emitted because h2 of the same list answers it, but h2 is inner.
            (t.into(), "target:h1:app.b.intent.I".into(), false),
            (t.into(), "target:h1:app.old.E3".into(), false),
            (t.into(), "inner:h1:app.old.E2".into(), false),
        ];
        wanted.sort();
        assert_eq!(found, wanted);
        let intent_list = model.by_name[b];
        assert!(translation_list(&model, &shape(), b) && model.tops[intent_list].layer.is_some());
    }
}
