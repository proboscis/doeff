//! What a declaration change changed, item by item — the commit hook's choice of the rules a declaration change promotes to the whole-repo comparison.
//!
//! A declaration (the config's `[tool.doeff-linter]`, the architecture files, the registry rows) can change a rule's hits in files the commit does not touch. Which rules
//! can change is decided by what changed in the declaration as read, not by which declaration file is staged: the HEAD and tip declarations are read into the same
//! values the linter reads (`DeclarationView`), split into slots (one per architecture key, per layer and service field, per config section, per rule's settings), and
//! the slots that differ name the changed items (`changes`). Comments, the order of keys and of services, and the positions of declarations are not part of a slot, so a
//! comment-only change of architecture.hy changes nothing. A rule is promoted when it reads a changed item (`ProjectRule::reads_declarations`, plus what every rule reads:
//! `Decl::Population`, its own settings and exemptions, and the registry rows that name it) or when the config newly enables it (`promoted`).
//!
//! A registry row removed lets its hit become an error again (the rule it names is promoted); a row added only loosens, but can match nothing (DOEFF166 is promoted).

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::Value;

use super::architecture::{ArchLayer, ArchService, Architecture};
use super::registry::Registry;
use super::rule::ProjectRule;
use crate::config::Config;

/// One item of the declarations a rule's hits can depend on. Items no deterministic rule reads (layer and service prose, `:semantic-lines`, the semantic section, the
/// commit hook's and the log's settings) are not items: their change promotes nothing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Decl {
    /// Where files sit and which are read — read by every rule: `:root`, the layers' names, order, `:modules` and `:files`, `:shared`, `:foundation`,
    /// `:verification-environment`, `:root-modules`, `:exclude`, `:extensions`, the architecture's name, and the config's `exclude`, `include`, `root`, `architecture`,
    /// `layers`, `tags`, `roles`, `services`, `registry`, `world_catalogs` (and any key this module does not know).
    Population,
    /// A layer's `:roles` and the roles' descriptions.
    LayerRoles,
    LayerImports,
    LayerForbidModules,
    LayerTypesOnly,
    LayerWireFree,
    LayerDependencyLayers,
    OpenLayers,
    /// A service's presence, `:dir` (its name) and `:layers`.
    ServicePlaces,
    /// A service's `:depends-on` and `:public-contract`.
    ServiceDependencies,
    /// A service's `:invariants` and `:entry-modules`.
    ServiceInvariants,
    /// A service's `:clauses` and `:clause-exemptions`.
    ServiceClauses,
    ServiceSystem,
    OutsideWriters,
    PlacedDependencies,
    BlindDefinitions,
    AllowedHeads,
    CallSites,
    BroadCatches,
    /// `:plain-callable-reasons` and `:rejected-plain-callable-reasons`.
    PlainCallableReasons,
    WireModules,
    /// What the definition graph and the allowlist read: `:world-handlers`, `:stand-ins`, `:static-readers`, `:systems`, `:edge-touches`, `:edge-mark`.
    WorldReach,
    BoundaryParts,
    RawIoRoots,
    TestForms,
    RetiredWords,
    RetiredCalls,
    EnvironmentBranches,
    SinglePointVocabulary,
    ConfinedSpellings,
    CountedSpellings,
    EffectCensus,
    FieldHolders,
    HandlerArguments,
    TypedValues,
    RecordStubs,
    BusinessFakes,
    AssemblyShape,
    /// Config `[tool.doeff-linter.environment_names]`.
    EnvironmentNames,
    /// Config `[tool.doeff-linter.raw_side_effects]`.
    RawSideEffects,
    /// Config `[tool.doeff-linter.definitions]`.
    Definitions,
    /// Config `[tool.doeff-linter.smells]`.
    Smells,
    /// Config `[tool.doeff-linter.translation_effects]`.
    TranslationEffects,
    /// Config `[tool.doeff-linter.program_sleep]`.
    ProgramSleep,
    /// Config `[tool.doeff-linter.test_sleep]`.
    TestSleep,
    /// A registry row added.
    RegistryRows,
}

/// One changed thing: an item, or something only one rule reads (its `[tool.doeff-linter.rules.<ID>]`, a law naming it, a layer exemption of it, a registry row of it
/// removed) — the rule ID uppercased.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Change {
    Item(Decl),
    Rule(String),
}

/// The declarations of one tree as the linter reads them (None = not declared there).
#[derive(Debug, Clone, Default)]
pub struct DeclarationView {
    pub config: Option<Config>,
    pub architecture: Option<Architecture>,
    /// The registry's keys (rows).
    pub registry: BTreeSet<String>,
}

impl DeclarationView {
    /// Reads the declarations of the tree at root with the config file `config` (absent file = no config) — the same files and the same readers as the linter's run
    /// (config::declaration_files, Architecture::load_all, Registry::load with the config's dirs, files, and config_files).
    pub fn load(root: &Path, config: Option<&Path>) -> Result<DeclarationView, String> {
        let config = match config.filter(|path| path.is_file()) {
            Some(path) => Some((crate::config::load_config_file(path)?.config, path.parent().unwrap_or(root).to_path_buf())),
            None => None,
        };
        let config_dir = config.as_ref().map(|(_, dir)| dir.clone()).unwrap_or_else(|| root.to_path_buf());
        let files = crate::config::declaration_files(config.as_ref().map(|(c, _)| c), &config_dir, root)?;
        let architecture = match files.is_empty() {
            true => None,
            false => Some(Architecture::load_all(&files, root).map_err(|problems| format!("architecture errors: {}", problems.join(" / ")))?),
        };
        let registry = match config.as_ref().and_then(|(c, _)| c.registry.as_ref()) {
            Some(section) => {
                let mut keys = Registry::load(root, &section.dirs, &section.files).keys;
                keys.extend(Registry::load(&config_dir, &[], &section.config_files).keys);
                keys
            }
            None => BTreeSet::new(),
        };
        Ok(DeclarationView { config: config.map(|(c, _)| c), architecture, registry })
    }
}

/// A slot of the declarations: what changes when its shape differs (the changes of both sides are taken) and its shape.
type Slots = BTreeMap<String, (Vec<Change>, String)>;

/// Pure: what changed from the HEAD declarations to the tip's (empty = nothing a rule reads changed).
pub fn changes(head: &DeclarationView, tip: &DeclarationView) -> BTreeSet<Change> {
    let (before, after) = (slots(head), slots(tip));
    let mut changed: BTreeSet<Change> = before
        .keys()
        .chain(after.keys())
        .filter(|key| before.get(*key).map(|(_, shape)| shape) != after.get(*key).map(|(_, shape)| shape))
        .flat_map(|key| before.get(key).into_iter().chain(after.get(key)).flat_map(|(changes, _)| changes.iter().cloned()))
        .collect();
    let laws: Vec<&crate::project::settings::LawEntry> = [head, tip].iter().flat_map(|view| view.config.iter().flat_map(|c| c.laws.iter())).collect();
    for removed in head.registry.difference(&tip.registry) {
        changed.extend(rules_of_key(removed, &laws).into_iter().map(Change::Rule));
    }
    if tip.registry.difference(&head.registry).next().is_some() {
        changed.insert(Change::Item(Decl::RegistryRows));
    }
    changed
}

/// Pure: the rules a registry key names (its second `::` field — a rule ID, or a law's name standing for the rules that judge it).
fn rules_of_key(key: &str, laws: &[&crate::project::settings::LawEntry]) -> Vec<String> {
    let Some(segment) = key.split("::").nth(1) else { return Vec::new() };
    match laws.iter().filter(|law| law.name == segment).flat_map(|law| law.rules.iter()).map(|id| id.to_uppercase()).collect::<Vec<_>>() {
        named if !named.is_empty() => named,
        _ => vec![segment.to_uppercase()],
    }
}

/// Pure: of the enabled rules (order kept), the ones to compare over the whole repo for a change — Jev rules excluded; those that read a changed item, and those the HEAD
/// config did not enable (`head_enabled` — their hits are all new to HEAD).
pub fn promoted(enabled: &[String], head_enabled: &[String], changes: &BTreeSet<Change>) -> Vec<String> {
    let everything = changes.contains(&Change::Item(Decl::Population));
    enabled
        .iter()
        .filter(|rule| !crate::config::is_semantic(rule))
        .filter(|rule| {
            let id = rule.to_uppercase();
            everything
                || !head_enabled.iter().any(|r| r.eq_ignore_ascii_case(&id))
                || changes.contains(&Change::Rule(id.clone()))
                || ProjectRule::parse(&id).is_some_and(|r| r.reads_declarations().iter().any(|item| changes.contains(&Change::Item(*item))))
        })
        .cloned()
        .collect()
}

/// Pure: the rules among `promoted` whose HEAD side is not measured (the HEAD config does not enable them — every hit of theirs is new).
pub fn newly_enabled(promoted: &[String], head_enabled: &[String]) -> Vec<String> {
    promoted.iter().filter(|rule| !head_enabled.iter().any(|r| r.eq_ignore_ascii_case(rule))).cloned().collect()
}

/// Positions and declaration files inside the shapes (`range: Range { … }`, `file: "…"`) — where a declaration is written is not what it declares.
static POSITIONS: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\brange: Range \{ start: Position \{ line: \d+, character: \d+ \}, end: Position \{ line: \d+, character: \d+ \} \}(, )?|\bfile: "(?:[^"\\]|\\.)*"(, )?"#)
        .expect("the positions pattern is a regex")
});

/// Pure: the shape of a declared value (its Debug text without positions).
fn shape(value: &impl std::fmt::Debug) -> String {
    POSITIONS.replace_all(&format!("{:?}", value), "").into_owned()
}

fn put(out: &mut Slots, key: String, changes: Vec<Change>, shape: String) {
    out.insert(key, (changes, shape));
}

fn item(decl: Decl) -> Vec<Change> {
    vec![Change::Item(decl)]
}

/// Pure: the slots of one tree's declarations.
fn slots(view: &DeclarationView) -> Slots {
    let mut out = Slots::new();
    if let Some(config) = &view.config {
        config_slots(config, &mut out);
    }
    match &view.architecture {
        Some(architecture) => architecture_slots(architecture, &mut out),
        None => put(&mut out, "architecture".to_string(), item(Decl::Population), "none".to_string()),
    }
    out
}

/// The config's slots: one per `[tool.doeff-linter]` key, per rule settings and per law. `enable` and `disable` are read as the enabled rules (`promoted`).
fn config_slots(config: &Config, out: &mut Slots) {
    let Ok(Value::Object(table)) = serde_json::to_value(config) else {
        put(out, "config".to_string(), item(Decl::Population), shape(config));
        return;
    };
    for (key, value) in table {
        let changes = match key.as_str() {
            "enable" | "disable" | "git" | "log_file" | "commit_hook" | "semantic" => continue,
            "rules" => {
                for (id, settings) in value.as_object().into_iter().flatten() {
                    put(out, format!("config.rules.{}", id.to_uppercase()), vec![Change::Rule(id.to_uppercase())], settings.to_string());
                }
                continue;
            }
            // A law's name is the `<rule>` field of its rules' keys (the registry matches them by it) and its text heads their messages.
            "laws" => {
                for law in &config.laws {
                    put(out, format!("config.laws.{}", law.name), law.rules.iter().map(|id| Change::Rule(id.to_uppercase())).collect(), shape(law));
                }
                continue;
            }
            "environment_names" => item(Decl::EnvironmentNames),
            "raw_side_effects" => item(Decl::RawSideEffects),
            "definitions" => item(Decl::Definitions),
            "smells" => item(Decl::Smells),
            "translation_effects" => item(Decl::TranslationEffects),
            "program_sleep" => item(Decl::ProgramSleep),
            "test_sleep" => item(Decl::TestSleep),
            _ => item(Decl::Population),
        };
        put(out, format!("config.{}", key), changes, value.to_string());
    }
}

/// The architecture's slots — every field named (an exhaustive destructure: a new field does not compile until it is given an item).
fn architecture_slots(architecture: &Architecture, out: &mut Slots) {
    let Architecture {
        name,
        root,
        layers,
        shared,
        foundation,
        verification_environment,
        root_modules,
        outside_writers,
        open_layers,
        placed_dependencies,
        blind_definitions,
        allowed_heads,
        call_sites,
        broad_catches,
        services,
        plain_callable_reasons,
        rejected_plain_callable_reasons,
        wire_modules,
        world_handlers,
        stand_ins,
        boundary_parts,
        raw_io_roots,
        edge_mark,
        static_readers,
        systems,
        edge_touches,
        test_forms,
        retired_words,
        retired_calls,
        environment_branches,
        semantic_lines: _,
        single_point_vocabulary,
        confined_spellings,
        counted_spellings,
        effect_census,
        field_holders,
        handler_arguments,
        typed_values,
        record_stubs,
        business_fakes,
        assembly_shape,
        role_descriptions,
        exclude,
        extensions,
        path: _,
        notices: _,
        repo_catalog: _,
        import_roots: _,
    } = architecture;
    let membership: Vec<(&String, &Vec<String>, &Vec<String>)> = layers.iter().map(|l| (&l.name, &l.modules, &l.files)).collect();
    put(
        out,
        "architecture.population".to_string(),
        item(Decl::Population),
        shape(&(name, root, &membership, shared, foundation, verification_environment, root_modules, exclude, extensions)),
    );
    for layer in layers {
        layer_slots(layer, out);
    }
    for service in services {
        service_slots(service, out);
    }
    let fields: [(&str, Decl, String); 31] = [
        ("roles", Decl::LayerRoles, shape(role_descriptions)),
        ("outside-writers", Decl::OutsideWriters, shape(outside_writers)),
        ("open-layers", Decl::OpenLayers, shape(open_layers)),
        ("placed-dependencies", Decl::PlacedDependencies, shape(placed_dependencies)),
        ("blind-definitions", Decl::BlindDefinitions, shape(blind_definitions)),
        ("allowed-heads", Decl::AllowedHeads, shape(allowed_heads)),
        ("call-sites", Decl::CallSites, shape(call_sites)),
        ("broad-catches", Decl::BroadCatches, shape(broad_catches)),
        ("plain-callable-reasons", Decl::PlainCallableReasons, shape(&(plain_callable_reasons, rejected_plain_callable_reasons))),
        ("wire-modules", Decl::WireModules, shape(wire_modules)),
        ("world-handlers", Decl::WorldReach, shape(world_handlers)),
        ("stand-ins", Decl::WorldReach, shape(stand_ins)),
        ("edge", Decl::WorldReach, shape(&(edge_mark, static_readers, systems, edge_touches))),
        ("boundary-parts", Decl::BoundaryParts, shape(boundary_parts)),
        ("raw-io-roots", Decl::RawIoRoots, shape(raw_io_roots)),
        ("test-forms", Decl::TestForms, shape(test_forms)),
        ("retired-words", Decl::RetiredWords, shape(retired_words)),
        ("retired-calls", Decl::RetiredCalls, shape(retired_calls)),
        ("environment-branches", Decl::EnvironmentBranches, shape(environment_branches)),
        ("single-point-vocabulary", Decl::SinglePointVocabulary, shape(single_point_vocabulary)),
        ("confined-spellings", Decl::ConfinedSpellings, shape(confined_spellings)),
        ("counted-spellings", Decl::CountedSpellings, shape(counted_spellings)),
        ("effect-census", Decl::EffectCensus, shape(effect_census)),
        ("field-holders", Decl::FieldHolders, shape(field_holders)),
        ("handler-arguments", Decl::HandlerArguments, shape(handler_arguments)),
        ("typed-values", Decl::TypedValues, shape(typed_values)),
        ("record-stubs", Decl::RecordStubs, shape(record_stubs)),
        ("business-fakes", Decl::BusinessFakes, shape(business_fakes)),
        ("assembly-shape", Decl::AssemblyShape, shape(assembly_shape)),
        // The layers' order is part of membership (architecture.population); a layer's own fields are in layer_slots.
        ("layers", Decl::Population, shape(&layers.iter().map(|l| &l.name).collect::<Vec<_>>())),
        ("services", Decl::ServicePlaces, shape(&services.iter().map(|s| &s.name).collect::<BTreeSet<_>>())),
    ];
    for (key, decl, value) in fields {
        put(out, format!("architecture.{}", key), item(decl), value);
    }
}

/// One layer's slots (keyed by its name). Its prose (`:summary`, `:knows`, `:does-not-know`, `:question`) is read only by the Jev rules.
fn layer_slots(layer: &ArchLayer, out: &mut Slots) {
    let ArchLayer {
        name,
        summary: _,
        knows: _,
        does_not_know: _,
        question: _,
        roles,
        imports,
        forbid_modules,
        types_only,
        wire_free,
        dependency_layers,
        modules: _,
        files: _,
        exempt,
    } = layer;
    let fields = [
        ("roles", Decl::LayerRoles, shape(roles)),
        ("imports", Decl::LayerImports, shape(imports)),
        ("forbid-modules", Decl::LayerForbidModules, shape(forbid_modules)),
        ("types-only", Decl::LayerTypesOnly, shape(types_only)),
        ("wire-free", Decl::LayerWireFree, shape(wire_free)),
        ("dependency-layers", Decl::LayerDependencyLayers, shape(dependency_layers)),
    ];
    for (key, decl, value) in fields {
        put(out, format!("layer.{}.{}", name, key), item(decl), value);
    }
    // An exemption changes only the rule it names (its reason changes nothing a rule judges).
    for rule in exempt.iter().map(|e| e.rule.to_uppercase()).collect::<BTreeSet<_>>() {
        put(out, format!("layer.{}.exempt.{}", name, rule), vec![Change::Rule(rule.clone())], "exempt".to_string());
    }
}

/// One service's slots (keyed by its name — the order of defservice forms is not read). Its `:description` is prose.
fn service_slots(service: &ArchService, out: &mut Slots) {
    let ArchService {
        name,
        dir,
        description: _,
        depends_on,
        layers,
        public_contract,
        invariants,
        clauses,
        clause_exemptions,
        entry_modules,
        system,
        pod: _, // read by a generator, no rule judges it
        range: _,
        file: _,
    } = service;
    let fields = [
        ("places", Decl::ServicePlaces, shape(&(dir, layers))),
        ("dependencies", Decl::ServiceDependencies, shape(&(depends_on, public_contract))),
        ("invariants", Decl::ServiceInvariants, shape(&(invariants, entry_modules))),
        ("clauses", Decl::ServiceClauses, shape(&(clauses, clause_exemptions))),
        ("system", Decl::ServiceSystem, shape(system)),
    ];
    for (key, decl, value) in fields {
        put(out, format!("service.{}.{}", name, key), item(decl), value);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ARCHITECTURE: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "pure" :roles [judgment] :imports [core])
           (layer entry :roles [entry] :imports [core entry])]
  :retired-words [(retired-words "old" :words ["legacy-word"] :files ["app/**/*.hy"] :instead "current")])
(defservice queue "queue" {:layers [core entry] :clauses ["fenced"] :invariants ["app.queue.inv:check"]})
(defservice lease "lease" {:layers [core]})
"#;

    fn view(architecture: &str, config: &str, registry: &[&str]) -> DeclarationView {
        let config: Config = toml::from_str::<toml::Value>(config)
            .ok()
            .and_then(|v| v.get("tool").and_then(|t| t.get("doeff-linter")).cloned())
            .map(|v| v.try_into().unwrap())
            .unwrap_or_default();
        DeclarationView {
            config: Some(config),
            architecture: Some(Architecture::parse(architecture, Path::new("architecture.hy")).unwrap()),
            registry: registry.iter().map(|k| k.to_string()).collect(),
        }
    }

    const CONFIG: &str = "[tool.doeff-linter]\nenable = [\"DOEFF102\"]\n";

    fn items(changes: &BTreeSet<Change>) -> Vec<String> {
        changes.iter().map(|c| format!("{:?}", c)).collect()
    }

    /// Comments, blank lines, the order of the defarchitecture keys and of the defservice forms, and a layer's prose change nothing a rule reads.
    #[test]
    fn comments_order_and_prose_change_no_item() {
        let base = view(ARCHITECTURE, CONFIG, &[]);
        let reordered = ARCHITECTURE
            .replace("(defarchitecture sample\n", ";; a comment\n\n(defarchitecture sample ; why\n")
            .replace(":summary \"pure\"", ":summary \"pure functions\"")
            .replace(
                "(defservice queue \"queue\" {:layers [core entry] :clauses [\"fenced\"] :invariants [\"app.queue.inv:check\"]})\n(defservice lease \"lease\" {:layers [core]})",
                "(defservice lease \"lease\" {:layers [core]})\n;; moved\n(defservice queue \"queue\" {:invariants [\"app.queue.inv:check\"] :layers [core entry] :clauses [\"fenced\"]})",
            );
        assert_ne!(reordered, ARCHITECTURE);
        let config = "# settings\n[tool.doeff-linter]\n\nenable = [ \"DOEFF102\" ]  # one rule\n";
        assert_eq!(items(&changes(&base, &view(&reordered, config, &[]))), Vec::<String>::new());
    }

    /// Failure case: each kind of change names its item, and only it (a layer's forbidden modules, a service's clauses, a service added, a retired-words group, a rule's
    /// settings, an exemption, a law).
    #[test]
    fn each_change_names_its_item() {
        let base = view(ARCHITECTURE, CONFIG, &[]);
        let changed = |architecture: &str, config: &str| items(&changes(&base, &view(architecture, config, &[])));
        assert_eq!(changed(&ARCHITECTURE.replace(":imports [core])\n", ":imports [core] :forbid-modules [json])\n"), CONFIG), vec!["Item(LayerForbidModules)"]);
        assert_eq!(changed(&ARCHITECTURE.replace(":clauses [\"fenced\"]", ":clauses [\"fenced\" \"single\"]"), CONFIG), vec!["Item(ServiceClauses)"]);
        assert_eq!(changed(&ARCHITECTURE.replace("\"legacy-word\"", "\"legacy-word\" \"older\""), CONFIG), vec!["Item(RetiredWords)"]);
        let added = changed(&format!("{}(defservice audit \"audit\" {{:layers [core] :system {{:exempt \"no job\"}}}})\n", ARCHITECTURE), CONFIG);
        assert!(added.contains(&"Item(ServicePlaces)".to_string()) && added.contains(&"Item(ServiceSystem)".to_string()), "{:?}", added);
        assert!(!added.contains(&"Item(Population)".to_string()), "{:?}", added);
        assert_eq!(changed(ARCHITECTURE, &format!("{}\n[tool.doeff-linter.rules.DOEFF003]\nmax_lines = 9\n", CONFIG)), vec!["Rule(\"DOEFF003\")"]);
        assert_eq!(
            changed(&ARCHITECTURE.replace("(layer entry :roles [entry] :imports [core entry])", "(layer entry :roles [entry] :imports [core entry] :modules [\"app.main\"] :exempt [(rule DOEFF110 \"entry point\")])"), CONFIG)
                .into_iter()
                .filter(|c| c.starts_with("Rule"))
                .collect::<Vec<_>>(),
            vec!["Rule(\"DOEFF110\")"]
        );
        assert_eq!(
            changed(ARCHITECTURE, &format!("{}\n[[tool.doeff-linter.laws]]\nname = \"no-json\"\nrules = [\"DOEFF102\"]\n", CONFIG)),
            vec!["Rule(\"DOEFF102\")"]
        );
        // Where files sit is read by every rule.
        assert_eq!(changed(&ARCHITECTURE.replace(":root \"app\"", ":root \"src\""), CONFIG), vec!["Item(Population)"]);
        assert_eq!(changed(ARCHITECTURE, &format!("{}exclude = [\"tests\"]\n", CONFIG)), vec!["Item(Population)"]);
    }

    /// A registry row removed names its rule (the hit becomes an error); a row added names DOEFF166's item (it may match nothing). A key naming a law names that law's rules.
    #[test]
    fn registry_rows_removed_name_their_rule_and_rows_added_the_stale_rule() {
        let config = format!("{}\n[[tool.doeff-linter.laws]]\nname = \"no-json\"\nrules = [\"DOEFF102\"]\n", CONFIG);
        let head = view(ARCHITECTURE, &config, &["app/a.hy::DOEFF163::queue", "app/b.hy::no-json::json"]);
        let tip = view(ARCHITECTURE, &config, &["app/c.hy::DOEFF124::g"]);
        assert_eq!(items(&changes(&head, &tip)), vec!["Item(RegistryRows)", "Rule(\"DOEFF102\")", "Rule(\"DOEFF163\")"]);
    }

    fn ids(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    /// The promoted rules: those reading a changed item, those with a changed rule-only setting, those the HEAD config did not enable; never Jev rules. Population
    /// promotes every rule.
    #[test]
    fn promoted_rules_are_the_readers_of_the_changed_items_and_the_newly_enabled() {
        let enabled = ids(&["DOEFF003", "DOEFF101", "DOEFF102", "DOEFF150", "DOEFF163", "DOEFF201"]);
        let head = ids(&["DOEFF003", "DOEFF101", "DOEFF102", "DOEFF163", "DOEFF201"]);
        let of = |changes: &[Change]| promoted(&enabled, &head, &changes.iter().cloned().collect());
        assert_eq!(of(&[]), ids(&["DOEFF150"]));
        assert_eq!(of(&[Change::Item(Decl::LayerForbidModules)]), ids(&["DOEFF102", "DOEFF150"]));
        assert_eq!(of(&[Change::Item(Decl::ServicePlaces)]), ids(&["DOEFF101", "DOEFF150", "DOEFF163"]));
        assert_eq!(of(&[Change::Rule("DOEFF003".to_string())]), ids(&["DOEFF003", "DOEFF150"]));
        assert_eq!(of(&[Change::Item(Decl::Population)]), ids(&["DOEFF003", "DOEFF101", "DOEFF102", "DOEFF150", "DOEFF163"]));
        assert_eq!(newly_enabled(&of(&[Change::Item(Decl::LayerForbidModules)]), &head), ids(&["DOEFF150"]));
    }

    /// Every item is read by some rule (an item no rule names would promote nothing — a forgotten reader). Population is read by every rule.
    #[test]
    fn every_item_has_a_reader() {
        let all = [
            Decl::LayerRoles,
            Decl::LayerImports,
            Decl::LayerForbidModules,
            Decl::LayerTypesOnly,
            Decl::LayerWireFree,
            Decl::LayerDependencyLayers,
            Decl::OpenLayers,
            Decl::ServicePlaces,
            Decl::ServiceDependencies,
            Decl::ServiceInvariants,
            Decl::ServiceClauses,
            Decl::ServiceSystem,
            Decl::OutsideWriters,
            Decl::PlacedDependencies,
            Decl::BlindDefinitions,
            Decl::AllowedHeads,
            Decl::CallSites,
            Decl::BroadCatches,
            Decl::PlainCallableReasons,
            Decl::WireModules,
            Decl::WorldReach,
            Decl::BoundaryParts,
            Decl::RawIoRoots,
            Decl::TestForms,
            Decl::RetiredWords,
            Decl::RetiredCalls,
            Decl::EnvironmentBranches,
            Decl::SinglePointVocabulary,
            Decl::ConfinedSpellings,
            Decl::CountedSpellings,
            Decl::EffectCensus,
            Decl::FieldHolders,
            Decl::HandlerArguments,
            Decl::TypedValues,
            Decl::RecordStubs,
            Decl::BusinessFakes,
            Decl::AssemblyShape,
            Decl::EnvironmentNames,
            Decl::RawSideEffects,
            Decl::Definitions,
            Decl::Smells,
            Decl::TranslationEffects,
            Decl::ProgramSleep,
            Decl::TestSleep,
            Decl::RegistryRows,
        ];
        for decl in all {
            assert!(ProjectRule::ALL.iter().any(|r| r.reads_declarations().contains(&decl)), "no rule reads {:?}", decl);
        }
    }

    /// Every config key is given an item or named as read by no rule (an unknown key falls to Population — this pins the known ones).
    #[test]
    fn every_config_key_is_classified() {
        let Value::Object(table) = serde_json::to_value(Config::default()).unwrap() else { panic!("config is a table") };
        let population = ["exclude", "layers", "tags", "roles", "services", "registry", "architecture", "root", "include", "world_catalogs"];
        let named = [
            "enable", "disable", "git", "log_file", "commit_hook", "semantic", "rules", "laws", "environment_names", "raw_side_effects", "definitions", "smells",
            "translation_effects", "program_sleep", "test_sleep",
        ];
        for key in table.keys() {
            assert!(population.contains(&key.as_str()) || named.contains(&key.as_str()), "config key {} is not classified", key);
        }
    }
}
