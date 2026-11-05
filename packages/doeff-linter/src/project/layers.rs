//! Types of the layer settings: the read form of the `[tool.doeff-linter.layers]` and `roles` sections, and the validated form of how layer numbers, locations, and tags are read.
//! These are types read from below by `settings` (which assembles the settings sections), `architecture` (which reads declarations), `semantic` (Jev questions), and `facts` (reading facts), and
//! this file reads no other module of project (split out of settings.rs).

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// `[tool.doeff-linter.layers]`: the order of layers, locations, and the import rules per layer.
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct LayersSection {
    /// Layer names (in order of distance from the outside world).
    #[serde(default)]
    pub order: Vec<String>,
    /// Layer name -> location from the repo root (one spelling or a list of spellings). The segment `*` matches a service name (e.g. `controllers/*/core`).
    #[serde(default)]
    pub paths: BTreeMap<String, PathPatterns>,
    /// Path separators outside the layer rules (exact match of a dir name or a file name, e.g. `tests`, `conftest.py`).
    #[serde(default)]
    pub exclude: Vec<String>,
    /// Extensions read as layer modules (default hy, hyk, hyp, py).
    #[serde(default)]
    pub extensions: Option<Vec<String>>,
    /// Layer name -> names of layers it may import from (write the same layer too). A layer not written is unrestricted.
    #[serde(default)]
    pub allow_imports: BTreeMap<String, Vec<String>>,
    /// Layer name -> module spellings that must not be imported directly (prefix match, e.g. httpx, urllib.request).
    #[serde(default)]
    pub forbid_modules: BTreeMap<String, Vec<String>>,
    /// Layers that hold only types (they define no functions or handlers).
    #[serde(default)]
    pub types_only: Vec<String>,
    /// Layers that pass values by type only (defwire, mappings; no untyped tuples, DOEFF170 and 171).
    #[serde(default)]
    pub wire_free: Vec<String>,
    /// Shapes of function definitions counted in type-only layers (head spellings in Hy; default defk, deff, defp, defpp, defhandler, defn).
    #[serde(default)]
    pub function_definers: Option<Vec<String>>,
    /// Layer name -> layer description (inserted into the output's layers and into the text of violation reasons).
    #[serde(default)]
    pub describe: BTreeMap<String, LayerDescription>,
}

/// How a layer's location is written: one spelling or a list of spellings (during migration, a layer-first form and a service-first form can be written side by side).
#[derive(Debug, Deserialize, Serialize, Clone, PartialEq, Eq)]
#[serde(untagged)]
pub enum PathPatterns {
    One(String),
    Many(Vec<String>),
}

impl PathPatterns {
    /// A list of spellings.
    pub fn list(&self) -> Vec<&str> {
        match self {
            PathPatterns::One(one) => vec![one.as_str()],
            PathPatterns::Many(many) => many.iter().map(String::as_str).collect(),
        }
    }
}

/// One segment of a location spelling (a literal name, or `*` matching a service name).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PlaceSegment {
    Literal(String),
    Service,
}

/// One location spelling (after validation).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlacePattern {
    /// The spelling written in the settings (after normalization).
    pub text: String,
    pub segments: Vec<PlaceSegment>,
}

/// The result of a file path matching a location (the actual dir, and the service name matched by `*`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlaceMatch {
    pub dir: String,
    pub service: Option<String>,
}

impl PlacePattern {
    /// Reads a spelling. Returns a reason for: empty, `.`, an absolute path, `..`, `*` inside a segment (`foo*`), or two or more `*`.
    pub fn parse(text: &str) -> Result<PlacePattern, String> {
        let normalized = normalize_dir(text);
        if normalized.is_empty() || normalized == "." || normalized.starts_with('/') {
            return Err(format!("location {:?} is not a dir under the repo root (empty, `.`, and absolute paths cannot be used)", text));
        }
        let mut segments = Vec::new();
        for part in normalized.split('/') {
            match part {
                "*" => segments.push(PlaceSegment::Service),
                ".." | "." | "" => return Err(format!("location {:?} cannot use `..`, `.`, or an empty segment", text)),
                other if other.contains('*') => return Err(format!("`*` in location {:?} can be written only as a whole segment", text)),
                other => segments.push(PlaceSegment::Literal(other.to_string())),
            }
        }
        if segments.iter().filter(|s| **s == PlaceSegment::Service).count() > 1 {
            return Err(format!("`*` (the service segment) in location {:?} is allowed at most once", text));
        }
        Ok(PlacePattern { text: normalized, segments })
    }

    /// The literal segments before `*` (the dir where the search starts).
    pub fn base(&self) -> String {
        self.segments
            .iter()
            .take_while(|s| matches!(s, PlaceSegment::Literal(_)))
            .map(|s| match s {
                PlaceSegment::Literal(text) => text.as_str(),
                PlaceSegment::Service => "",
            })
            .collect::<Vec<_>>()
            .join("/")
    }

    /// If the file's path from the repo root is under this location, returns the actual dir and the service name.
    pub fn matches(&self, rel: &str) -> Option<PlaceMatch> {
        let parts: Vec<&str> = rel.split('/').collect();
        if parts.len() <= self.segments.len() {
            return None;
        }
        let mut service = None;
        for (segment, part) in self.segments.iter().zip(parts.iter()) {
            match segment {
                PlaceSegment::Literal(text) if text == part => {}
                PlaceSegment::Literal(_) => return None,
                PlaceSegment::Service => service = Some(part.to_string()),
            }
        }
        Some(PlaceMatch { dir: parts[..self.segments.len()].join("/"), service })
    }

    /// The number of segments (when two locations match, the one with more segments is taken).
    pub fn depth(&self) -> usize {
        self.segments.len()
    }
}

/// `[tool.doeff-linter.layers.describe.<layer>]`: what the layer is (not written in Rust; the per-repo settings hold it).
#[derive(Debug, Deserialize, Serialize, Default, Clone, PartialEq, Eq)]
pub struct LayerDescription {
    /// Summary of the layer (e.g. business decisions and Program).
    pub summary: Option<String>,
    /// What this layer knows.
    pub knows: Option<String>,
    /// What this layer does not know.
    pub does_not_know: Option<String>,
    /// The question to ask when unsure.
    pub question: Option<String>,
}

/// `[tool.doeff-linter.roles]`: the closed list of roles and the roles allowed per layer.
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct RolesSection {
    #[serde(default)]
    pub names: Vec<String>,
    #[serde(default)]
    pub by_layer: BTreeMap<String, Vec<String>>,
    /// role -> description of that role (inserted into the text of violation reasons. Descriptions of old roles removed from the list can also be written).
    #[serde(default)]
    pub describe: BTreeMap<String, String>,
}

/// Layer number in the settings (the index into `order`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct LayerId(pub usize);

/// One layer after validation.
#[derive(Debug, Clone)]
pub struct LayerSpec {
    pub name: String,
    /// Location spellings from the repo root (a `*` segment is the service).
    pub places: Vec<PlacePattern>,
    /// Layers it may import from (None = unrestricted).
    pub allowed: Option<BTreeSet<LayerId>>,
    pub forbid_modules: BTreeSet<String>,
    pub types_only: bool,
    /// Whether it is a layer that passes values by type only (DOEFF170 and 171).
    pub wire_free: bool,
    /// Allowed roles (None = the role rule is not applied).
    pub roles: Option<BTreeSet<String>>,
    /// Description of the layer (all fields are None if absent from the settings).
    pub description: LayerDescription,
}

/// How tags are read (after validation).
#[derive(Debug, Clone)]
pub struct TagReading {
    pub module_variable_hy: String,
    pub module_variable_py: String,
    pub contract_definers: BTreeSet<String>,
    pub plain_definers: BTreeSet<String>,
    pub effect_definers: BTreeSet<String>,
    pub record_definers: BTreeSet<String>,
    pub function_definers: BTreeSet<String>,
    /// Keys required in a definition's :tags.
    pub required: Vec<String>,
    /// Whether the module-head tag can fill them in.
    pub module_default: bool,
    /// Heads of definitions that require tags.
    pub require_on: BTreeSet<String>,
}

/// The layer settings (after validation).
#[derive(Debug, Clone)]
pub struct LayerSettings {
    pub layers: Vec<LayerSpec>,
    /// role -> description.
    pub role_descriptions: BTreeMap<String, String>,
    pub exclude: BTreeSet<String>,
    pub extensions: BTreeSet<String>,
    pub tags: TagReading,
    /// The root of architecture.hy (if present, infers the layer of modules outside the declared locations from the :role tag).
    pub infer_root: Option<String>,
}

/// Normalizes a dir spelling (removes `./` and a trailing `/`, and makes the separator `/`).
pub fn normalize_dir(dir: &str) -> String {
    let unified = dir.replace('\\', "/");
    let trimmed = unified.trim_start_matches("./").trim_end_matches('/');
    trimmed.to_string()
}

/// `[tool.doeff-linter.tags]`: how tags are read (doeff-hy's spelling; the default is fine).
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct TagsSection {
    /// Name of the tag at the head of a Hy module (`(val MODULE-TAGS {…})`).
    pub module_variable_hy: Option<String>,
    /// Name of the tag at the head of a Python module (`MODULE_TAGS = {…}`).
    pub module_variable_py: Option<String>,
    /// Shapes of definitions that may write `:tags` in a contract dict.
    pub contract_definers: Option<Vec<String>>,
    /// Shapes of definitions that have no place to write tags (relying on the module-head tag).
    pub plain_definers: Option<Vec<String>>,
    /// Shape of an effect type definition of the form `(name "doc"? {… :tags {…}})` (default defeffect).
    pub effect_definers: Option<Vec<String>>,
    /// Shape of a record type definition of the form `(name "doc"? {:tags {…} :check […]}? field …)` (default defrecord; doeff-hy's head dict).
    /// If a head dict exists, its :tags are read; if not, it is a definition that writes no tags.
    pub record_definers: Option<Vec<String>>,
    /// DOEFF112: keys required in a definition's :tags (default context, role).
    pub required: Option<Vec<String>>,
    /// DOEFF112: whether the module-head tag can fill in a definition's tags (default true).
    pub module_default: Option<bool>,
    /// DOEFF112: heads of definitions that require tags (default defk, deff, defp, defhandler, defeffect. defrecord can write :tags in its head dict,
    /// so adding it here makes it required; it is not added by default, because the head dict is a form that can be omitted).
    pub require_on: Option<Vec<String>>,
}

/// Turns a list of strings into a set (to match the settings' defaults when omitted).
fn set_of(values: &[&str]) -> BTreeSet<String> {
    values.iter().map(|s| s.to_string()).collect()
}

/// Builds how tags are read from the settings (omitted fields take doeff-hy's default spelling).
pub fn tag_reading(tags: Option<&TagsSection>, function_definers: Option<&Vec<String>>) -> TagReading {
    let tags = tags.cloned().unwrap_or_default();
    TagReading {
        module_variable_hy: tags.module_variable_hy.unwrap_or_else(|| "MODULE-TAGS".to_string()),
        module_variable_py: tags.module_variable_py.unwrap_or_else(|| "MODULE_TAGS".to_string()),
        contract_definers: tags
            .contract_definers
            .map(|v| v.into_iter().collect())
            .unwrap_or_else(|| set_of(&["defk", "deff", "defp", "defpp", "defhandler"])),
        plain_definers: tags.plain_definers.map(|v| v.into_iter().collect()).unwrap_or_else(|| set_of(&["defn", "defclass", "defenum"])),
        effect_definers: tags.effect_definers.map(|v| v.into_iter().collect()).unwrap_or_else(|| set_of(&["defeffect"])),
        record_definers: tags.record_definers.map(|v| v.into_iter().collect()).unwrap_or_else(|| set_of(&["defrecord", "defwire"])),
        function_definers: function_definers
            .map(|v| v.iter().cloned().collect())
            .unwrap_or_else(|| set_of(&["defk", "deff", "defp", "defpp", "defhandler", "defn"])),
        required: tags.required.unwrap_or_else(|| vec!["context".to_string(), "role".to_string()]),
        module_default: tags.module_default.unwrap_or(true),
        require_on: tags
            .require_on
            .map(|v| v.into_iter().collect())
            .unwrap_or_else(|| set_of(&["defk", "deff", "defp", "defhandler", "defeffect"])),
    }
}

#[cfg(test)]
mod place_tests {
    use super::*;

    #[test]
    fn place_patterns_capture_the_service_segment() {
        let pattern = PlacePattern::parse("./controllers/*/core/").unwrap();
        assert_eq!(pattern.base(), "controllers");
        assert_eq!(
            pattern.matches("controllers/land_notice/core/goal.hy"),
            Some(PlaceMatch { dir: "controllers/land_notice/core".into(), service: Some("land_notice".into()) })
        );
        assert_eq!(pattern.matches("controllers/core/goal.hy"), None);
        assert_eq!(pattern.matches("controllers/land_notice/core"), None, "the dir itself is not a file");
        let plain = PlacePattern::parse("controllers/core").unwrap();
        assert_eq!(plain.matches("controllers/core/a/b.hy"), Some(PlaceMatch { dir: "controllers/core".into(), service: None }));
        for bad in ["", ".", "/abs", "a/../b", "a/b*", "*/x/*"] {
            assert!(PlacePattern::parse(bad).is_err(), "{:?}", bad);
        }
    }
}
