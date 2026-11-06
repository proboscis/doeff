//! Catalog of doeff's real-I/O handlers (`data/world_handlers.json`), plus the catalogs a using repo adds.
//!
//! Which handlers touch the outside world is a fact about the package that defines them, so it is not written per user repo in architecture.hy but kept in one
//! place next to that package (the same idea as the raw-I/O catalog doeff-indexer `data/raw_side_effects.json`). doeff's own handlers are in the bundled catalog;
//! handlers of packages outside doeff are in a catalog file kept by the package's repo, which a using repo names in `[tool.doeff-linter] world_catalogs`
//! (paths from the config file's dir — read on top of the bundled catalog, `WorldCatalog::with_repo_catalogs`). The linter:
//!   * treats every handler in the catalog as a counterpart of DOEFF131 (naming outside the roster) and DOEFF133 (kinds of tests).
//!   * allows `:wraps` of `:world-handlers` only for handlers in the catalog (otherwise it is a configuration error).
//!   * matches the catalog's touch targets (`touches`) against `:edge-touches` in DOEFF133.
//!
//! Shape: `{"handlers": [{"handler": "doeff_core_effects.os_file:os-file-handler", "touches": ["file"], "why": "…"}]}`.

use std::collections::BTreeMap;
use std::sync::OnceLock;

use serde::Deserialize;

use super::architecture::{Architecture, DefinitionRef, WorldTouch};
use doeff_indexer::hy_index::RawCategory;

#[derive(Deserialize)]
struct CatalogFile {
    handlers: Vec<CatalogEntry>,
}

#[derive(Deserialize)]
struct CatalogEntry {
    handler: String,
    touches: Vec<String>,
    /// false = can be written in the roster's :wraps but is not counted as real I/O (a row kept after moving the touch target to another row, until the repo's :wraps moves over).
    #[serde(default = "counted_by_default")]
    counted: bool,
    /// The grounds for listing it in the catalog (read only by humans).
    #[allow(dead_code)]
    why: String,
}

/// One handler of the catalog.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogHandler {
    pub definition: DefinitionRef,
    pub touches: Vec<WorldTouch>,
    /// false = can be written in :wraps but is not counted (a row during migration).
    pub counted: bool,
}

fn counted_by_default() -> bool {
    true
}

impl CatalogHandler {
    /// Whether a place that names this row is counted as real I/O (a row that is not counted can only be written in :wraps).
    pub fn counts(&self) -> bool {
        self.counted
    }
}

/// The catalog (fully qualified name -> handler).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct WorldCatalog {
    pub handlers: BTreeMap<String, CatalogHandler>,
    /// The repo's catalog files that do not exist (read without them — `with_repo_catalogs`). A config error that names a :wraps outside the catalog names them too.
    pub absent: Vec<String>,
}

impl WorldCatalog {
    /// Fully qualified name of doeff's real-I/O handler in the catalog -> (spelling, the spellings of the roster definitions that list it in a declaration's :wraps, touch targets).
    /// Matching a declaration (architecture) against the catalog is the catalog's job; a declaration does not read the catalog.
    pub fn world_targets(&self, architecture: &Architecture) -> BTreeMap<String, (String, Vec<String>, Vec<WorldTouch>)> {
        let wrapped = architecture.wrapped_targets();
        self.handlers
            .iter()
            .map(|(target, handler)| {
                let by = wrapped.get(target).map(|(_, by)| by.clone()).unwrap_or_default();
                (target.clone(), (handler.definition.spelling(), by, handler.touches.clone()))
            })
            .collect()
    }

    /// Turns the JSON contents into a catalog (elements that cannot be read become a list of reasons).
    pub fn parse(text: &str) -> Result<WorldCatalog, Vec<String>> {
        WorldCatalog::parse_named(text, "world_handlers.json")
    }

    /// Turns the JSON contents of the file `name` into a catalog (the reasons name the file).
    fn parse_named(text: &str, name: &str) -> Result<WorldCatalog, Vec<String>> {
        let file: CatalogFile = serde_json::from_str(text).map_err(|e| vec![format!("cannot read {}: {}", name, e)])?;
        let mut problems = Vec::new();
        let mut handlers = BTreeMap::new();
        for entry in file.handlers {
            let Some(definition) = DefinitionRef::parse(&entry.handler) else {
                problems.push(format!("{}: {} is not a \"module.path:name\" spelling", name, entry.handler));
                continue;
            };
            let mut touches = Vec::new();
            for word in &entry.touches {
                match WorldTouch::parse(word) {
                    Some(touch) if !touches.contains(&touch) => touches.push(touch),
                    Some(_) => problems.push(format!("{}: touches of {} lists {} twice", name, entry.handler, word)),
                    None => problems.push(format!("{}: {} in touches of {} is not an allowed word", name, word, entry.handler)),
                }
            }
            if touches.is_empty() {
                problems.push(format!("{}: {} has no touches", name, entry.handler));
            }
            if handlers.insert(definition.target(), CatalogHandler { definition, touches, counted: entry.counted }).is_some() {
                problems.push(format!("{}: {} is listed twice", name, entry.handler));
            }
        }
        if problems.is_empty() {
            Ok(WorldCatalog { handlers, absent: Vec::new() })
        } else {
            Err(problems)
        }
    }

    /// The bundled catalog with the repo's catalog files laid on top (`[tool.doeff-linter] world_catalogs` — the files in the order written).
    /// A file that does not exist is read as no rows and listed in `absent` (the using repo's checkout of the package's repo can be behind its main for a while,
    /// and while the bundled catalog still has the rows nothing needs the file — a :wraps that does need it is the config error, and it names the absent file).
    /// A file that exists but cannot be read, or a row that cannot be read, is a reason. A row already in the bundled catalog or an earlier file is not taken again
    /// (the earlier row stays) — so a package's repo can carry its rows while doeff still bundles them, and doeff can drop them afterwards without a gap.
    pub fn with_repo_catalogs(paths: &[std::path::PathBuf]) -> Result<WorldCatalog, Vec<String>> {
        let mut catalog = WorldCatalog::bundled().clone();
        let mut problems = Vec::new();
        for path in paths {
            let name = path.display().to_string();
            let text = match std::fs::read_to_string(path) {
                Ok(text) => text,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    catalog.absent.push(name);
                    continue;
                }
                Err(error) => {
                    problems.push(format!("cannot read the repo's catalog of real I/O handlers {} (world_catalogs): {}", name, error));
                    continue;
                }
            };
            match WorldCatalog::parse_named(&text, &name) {
                Ok(read) => {
                    for (target, handler) in read.handlers {
                        catalog.handlers.entry(target).or_insert(handler);
                    }
                }
                Err(reasons) => problems.extend(reasons),
            }
        }
        if problems.is_empty() {
            Ok(catalog)
        } else {
            Err(problems)
        }
    }

    /// The catalog bundled with this binary (read only once).
    pub fn bundled() -> &'static WorldCatalog {
        static BUNDLED: OnceLock<WorldCatalog> = OnceLock::new();
        BUNDLED.get_or_init(|| {
            WorldCatalog::parse(include_str!("../../data/world_handlers.json")).expect("the bundled world_handlers.json cannot be read (the bundled_catalog_is_well_formed test should be red)")
        })
    }
}

/// Maps the categories of raw-I/O evidence to touch-target words (async waits and threads become thread; time and randomness become clock; both are non-deterministic sources).
pub fn touch_of_raw(category: RawCategory) -> WorldTouch {
    match category {
        RawCategory::Http => WorldTouch::Http,
        RawCategory::Async | RawCategory::Thread => WorldTouch::Thread,
        RawCategory::Time | RawCategory::Random => WorldTouch::Clock,
        RawCategory::File => WorldTouch::File,
        RawCategory::Process => WorldTouch::Process,
        RawCategory::Env => WorldTouch::Env,
        RawCategory::Network => WorldTouch::Network,
        RawCategory::Db => WorldTouch::Db,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundled_catalog_is_well_formed() {
        let parsed = WorldCatalog::parse(include_str!("../../data/world_handlers.json"));
        let catalog = parsed.unwrap_or_else(|problems| panic!("the bundled catalog cannot be read:\n{}", problems.join("\n")));
        assert!(catalog.handlers.contains_key("doeff_core_effects.os_file.os_file_handler"), "os-file-handler is not in the catalog");
        // The layer-2-only entry (starts the child process claude) is also in the catalog as a real-I/O handler (two spellings).
        for name in ["doeff_agents._handlers.claude_process_layer_handler", "doeff_agents.claude_process_layer_handler"] {
            assert!(catalog.handlers.contains_key(name), "{} is not in the catalog", name);
        }
        // Even after moving the children of doeff_core_effects to _, the old spelling can be written in :wraps while the relocation module remains.
        for name in [
            "doeff_core_effects._handlers.await_handler",
            "doeff_core_effects.await_handler",
            "doeff_core_effects.handlers.await_handler",
            "doeff_core_effects.handlers.env_var_ask",
        ] {
            assert!(catalog.handlers.contains_key(name), "{} is not in the catalog", name);
        }
        // The children for compute, VM, and measurement likewise exist in three spellings: the _ spelling, the entry spelling, and the old spelling.
        for (child, handler) in [
            ("thread_pool_compute", "thread_pool_compute_handler"),
            ("gc_freeze", "gc_freeze_handler"),
            ("faulthandler_stack_dump", "faulthandler_stack_dump_handler"),
            // The children for file, memo, and cache are the same.
            ("os_file", "os_file_handler"),
            ("cache_handlers", "sqlite_cache_handler"),
            ("memo_handlers", "sqlite_memo_handler"),
            // The children for process are the same.
            ("os_process", "subprocess_handler"),
            ("stop_signal_handlers", "os_signal_stop_handler"),
            ("process_meter", "process_meter_handler"),
            ("process_latest", "process_latest_handler"),
            // The children for http and ws are the same.
            ("http_handlers", "http_production_handler"),
            ("http_handlers", "http_fixture_handler"),
            ("aiohttp_ws_client", "aiohttp_ws_link_handler"),
            // The children for sql are the same.
            ("postgres_sql", "postgres_sql_handler"),
            ("pooled_postgres_sql", "pooled_postgres_sql_handler"),
            ("clickhouse_http_sql", "clickhouse_http_sql_handler"),
        ] {
            for name in [
                format!("doeff_core_effects._{child}.{handler}"),
                format!("doeff_core_effects.{handler}"),
                format!("doeff_core_effects.{child}.{handler}"),
            ] {
                assert!(catalog.handlers.contains_key(&name), "{} is not in the catalog", name);
            }
        }
    }

    /// For a handler whose child was moved to _, the row with the old spelling is also kept in the catalog while the relocation module (line 1 `# moved-submodule-shim: <old path> -> <new path>`) remains.
    /// The :wraps of external repos are still written with the old spelling, and removing it from the catalog is a configuration error that stops every commit
    /// (42f897000 replaced the old aiohttp-http-server spelling with the _ spelling and stopped the agora-controllers commit-msg hook).
    #[test]
    fn catalog_keeps_the_old_spelling_of_every_moved_handler() {
        let packages = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
        let mut moved_from = BTreeMap::new();
        for entry in walkdir::WalkDir::new(&packages)
            .into_iter()
            .filter_entry(|e| !matches!(e.file_name().to_str(), Some("target" | "node_modules" | ".venv")))
            .filter_map(Result::ok)
            .filter(|e| e.path().extension().is_some_and(|x| x == "py"))
        {
            let Ok(text) = std::fs::read_to_string(entry.path()) else { continue };
            let Some(mark) = text.lines().next().and_then(|line| line.strip_prefix("# moved-submodule-shim: ")) else { continue };
            if let Some((old, new)) = mark.split_once(" -> ") {
                moved_from.insert(new.trim().to_string(), old.trim().to_string());
            }
        }
        // The shell of aiohttp_http_server was removed because it has the same name as the entry name (ADR-DOE-API-001 R34); use a remaining shell to check the search.
        assert!(moved_from.contains_key("doeff_core_effects._thread_pool_compute"), "cannot find the relocation module (search root {})", packages.display());
        let catalog = WorldCatalog::bundled();
        let missing: Vec<String> = catalog
            .handlers
            .values()
            .filter_map(|handler| {
                let spelling = handler.definition.spelling();
                let (module, name) = spelling.split_once(':')?;
                let old = moved_from.get(module)?;
                let old_spelling = format!("{}:{}", old, name);
                let known = DefinitionRef::parse(&old_spelling).is_some_and(|d| catalog.handlers.contains_key(&d.target()));
                (!known).then(|| format!("{} old spelling {}", spelling, old_spelling))
            })
            .collect();
        assert!(missing.is_empty(), "the relocation module remains but the catalog has no row for the old spelling:\n{}", missing.join("\n"));
    }

    /// A repo's catalog file is laid on top of the bundled catalog: its rows are added, a row the bundled catalog already has stays the bundled row
    /// (a package's repo can carry its rows before doeff drops them), and a file that cannot be read or a row that cannot be read is a reason naming the file.
    #[test]
    fn repo_catalogs_are_laid_on_the_bundled_catalog() {
        let dir = tempfile::TempDir::new().unwrap();
        let agents = dir.path().join("agents.json");
        std::fs::write(
            &agents,
            r#"{"handlers": [
                {"handler": "pkg_agents:agent-handler", "touches": ["process"], "why": "w"},
                {"handler": "doeff_core_effects.os_file:os-file-handler", "touches": ["clock"], "why": "w"}]}"#,
        )
        .unwrap();
        let catalog = WorldCatalog::with_repo_catalogs(&[agents.clone()]).unwrap_or_else(|problems| panic!("{}", problems.join("\n")));
        assert_eq!(catalog.handlers["pkg_agents.agent_handler"].touches, vec![WorldTouch::Process]);
        assert_eq!(catalog.handlers["doeff_core_effects.os_file.os_file_handler"], WorldCatalog::bundled().handlers["doeff_core_effects.os_file.os_file_handler"]);
        assert_eq!(catalog.handlers.len(), WorldCatalog::bundled().handlers.len() + 1);

        let bad = dir.path().join("bad.json");
        std::fs::write(&bad, r#"{"handlers": [{"handler": "nocolon", "touches": ["file"], "why": "w"}]}"#).unwrap();
        let problems = WorldCatalog::with_repo_catalogs(&[bad.clone()]).unwrap_err().join("\n");
        assert!(problems.contains(&format!("{}: nocolon is not", bad.display())), "{}", problems);
        // A file that does not exist is read as no rows and named in absent (not a reason).
        let missing = dir.path().join("missing.json");
        let catalog = WorldCatalog::with_repo_catalogs(&[missing.clone(), agents.clone()]).unwrap_or_else(|problems| panic!("{}", problems.join("\n")));
        assert_eq!(catalog.absent, vec![missing.display().to_string()]);
        assert!(catalog.handlers.contains_key("pkg_agents.agent_handler"));
    }

    #[test]
    fn misreadings_are_reasons() {
        let bad = r#"{"handlers": [
            {"handler": "a.b:x", "touches": ["file", "smoke"], "why": "w"},
            {"handler": "nocolon", "touches": ["file"], "why": "w"},
            {"handler": "a.b:y", "touches": [], "why": "w"},
            {"handler": "a.b:x", "touches": ["file"], "why": "w"}]}"#;
        let problems = WorldCatalog::parse(bad).unwrap_err().join("\n");
        for needle in ["smoke in touches of a.b:x is not an allowed word", "nocolon is not", "a.b:y has no touches", "a.b:x is listed twice"] {
            assert!(problems.contains(needle), "{} is missing:\n{}", needle, problems);
        }
    }
}
