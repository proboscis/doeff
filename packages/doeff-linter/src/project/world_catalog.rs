//! Catalog of doeff's real-I/O handlers (`data/world_handlers.json`).
//!
//! Which handlers touch the outside world is a fact about doeff, so it is not written per user repo in architecture.hy but kept in one place on the doeff side
//! (the same idea as the raw-I/O catalog doeff-indexer `data/raw_side_effects.json`). The linter:
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
#[derive(Debug, Clone, Default)]
pub struct WorldCatalog {
    pub handlers: BTreeMap<String, CatalogHandler>,
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
        let file: CatalogFile = serde_json::from_str(text).map_err(|e| vec![format!("cannot read world_handlers.json: {}", e)])?;
        let mut problems = Vec::new();
        let mut handlers = BTreeMap::new();
        for entry in file.handlers {
            let Some(definition) = DefinitionRef::parse(&entry.handler) else {
                problems.push(format!("world_handlers.json: {} is not a \"module.path:name\" spelling", entry.handler));
                continue;
            };
            let mut touches = Vec::new();
            for word in &entry.touches {
                match WorldTouch::parse(word) {
                    Some(touch) if !touches.contains(&touch) => touches.push(touch),
                    Some(_) => problems.push(format!("world_handlers.json: touches of {} lists {} twice", entry.handler, word)),
                    None => problems.push(format!("world_handlers.json: {} in touches of {} is not an allowed word", word, entry.handler)),
                }
            }
            if touches.is_empty() {
                problems.push(format!("world_handlers.json: {} has no touches", entry.handler));
            }
            if handlers.insert(definition.target(), CatalogHandler { definition, touches, counted: entry.counted }).is_some() {
                problems.push(format!("world_handlers.json: {} is listed twice", entry.handler));
            }
        }
        if problems.is_empty() {
            Ok(WorldCatalog { handlers })
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
