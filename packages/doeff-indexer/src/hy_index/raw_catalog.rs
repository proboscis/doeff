//! Catalog of raw side effects — takes in `data/raw_side_effects.json` at build time and turns it into a typed table.
//! User additions (`--raw-catalog-extra <json>`) are added to the same table. The judgment (`raw.rs`) looks only at this table.

use serde::Deserialize;
use std::collections::BTreeMap;

/// The bundled catalog (the single place of declaration).
const BUNDLED_CATALOG: &str = include_str!("../../data/raw_side_effects.json");

/// Classification of raw side effects (a closed set).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Deserialize, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RawCategory {
    Http,
    Async,
    Time,
    Random,
    File,
    Process,
    Env,
    Network,
    Db,
    Thread,
}

impl RawCategory {
        /// Catalog order (display order).
    pub const ALL: [RawCategory; 10] = [
        RawCategory::Http,
        RawCategory::Async,
        RawCategory::Time,
        RawCategory::Random,
        RawCategory::File,
        RawCategory::Process,
        RawCategory::Env,
        RawCategory::Network,
        RawCategory::Db,
        RawCategory::Thread,
    ];

        /// Spelling of the contract.
    pub fn as_str(self) -> &'static str {
        match self {
            RawCategory::Http => "http",
            RawCategory::Async => "async",
            RawCategory::Time => "time",
            RawCategory::Random => "random",
            RawCategory::File => "file",
            RawCategory::Process => "process",
            RawCategory::Env => "env",
            RawCategory::Network => "network",
            RawCategory::Db => "db",
            RawCategory::Thread => "thread",
        }
    }

        /// Looks up a classification from its spelling (None if unknown).
    pub fn parse(text: &str) -> Option<RawCategory> {
        RawCategory::ALL.into_iter().find(|category| category.as_str() == text)
    }
}

/// Evidence picked up by method name alone (weak), and the module that is the condition for counting it.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct RawMethod {
    pub name: String,
        /// Counted only when this module looks like an import of the file or a reference inside a definition. If empty, always counted.
    pub context: Vec<String>,
        /// A call with more arguments than this number (excluding the call's receiver) is not counted (no upper limit if unwritten). To tell it apart from a same-named method of another type —
        /// `Path.replace(target)` takes 1, while the string `(.replace s "/" "_")` takes 2. A reference that is not the head of a call
        /// (e.g. passing `(. p replace)` as a value) has unknown arguments, so it is counted as before.
    #[serde(default)]
    pub max_args: Option<usize>,
}

/// The catalog for one classification.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct RawCatalogEntry {
    pub category: RawCategory,
    pub patterns: Vec<String>,
    pub builtins: Vec<String>,
    pub methods: Vec<RawMethod>,
}

/// The whole catalog.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct RawCatalog {
    pub categories: Vec<RawCatalogEntry>,
        /// Names that match a pattern but are not side effects (pure modules, lowercase exception types).
    pub ignored: Vec<String>,
        /// Names ending in these words are not counted as exception types.
    pub exception_suffixes: Vec<String>,
}

/// The outer shape of the bundled file (to skip the description fields).
#[derive(Deserialize)]
struct BundledFile {
    categories: Vec<RawCatalogEntry>,
    ignored: Vec<String>,
    exception_suffixes: Vec<String>,
}

impl RawCatalog {
        /// Reads the bundled catalog. The bundled file is checked together with the build, so a failure to read it is a build error (tests turn red).
    pub fn bundled() -> Result<RawCatalog, String> {
        let file: BundledFile =
                        serde_json::from_str(BUNDLED_CATALOG).map_err(|error| format!("cannot read the bundled catalog: {}", error))?;
        Ok(RawCatalog {
            categories: file.categories,
            ignored: file.ignored,
            exception_suffixes: file.exception_suffixes,
        })
    }

        /// Adds user additions (JSON of classification -> array of names). Name notation: `.name` is a method name (weak, no condition),
        /// `builtin:name` is a builtin, anything else is a dotted name. Unknown classifications and non-array values are skipped with a reason returned.
    pub fn with_extra(&self, extra: &serde_json::Value) -> (RawCatalog, Vec<String>) {
        let mut catalog = self.clone();
        let mut problems = Vec::new();
        let Some(object) = extra.as_object() else {
                        problems.push("--raw-catalog-extra must be an object of \"classification -> array of names\"".to_string());
            return (catalog, problems);
        };
        let mut added: BTreeMap<RawCategory, Vec<String>> = BTreeMap::new();
        for (key, value) in object {
            let Some(category) = RawCategory::parse(key) else {
                let known: Vec<&str> = RawCategory::ALL.iter().map(|c| c.as_str()).collect();
                                problems.push(format!("rawSideEffects classification \"{}\" is unknown (available classifications: {})", key, known.join(", ")));
                continue;
            };
            let Some(items) = value.as_array() else {
                                problems.push(format!("rawSideEffects.{} is not an array", key));
                continue;
            };
            for item in items {
                match item.as_str().filter(|text| !text.trim().is_empty()) {
                    Some(text) => added.entry(category).or_default().push(text.to_string()),
                                        None => problems.push(format!("rawSideEffects.{} has a non-string value {}", key, item)),
                }
            }
        }
        for entry in &mut catalog.categories {
            for item in added.remove(&entry.category).unwrap_or_default() {
                if let Some(method) = item.strip_prefix('.') {
                    entry.methods.push(RawMethod { name: method.to_string(), context: Vec::new(), max_args: None });
                } else if let Some(builtin) = item.strip_prefix("builtin:") {
                    entry.builtins.push(builtin.to_string());
                } else {
                    entry.patterns.push(item);
                }
            }
        }
        (catalog, problems)
    }
}
