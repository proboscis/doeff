//! The `:design-evidence` declaration read at the base: pyproject.toml's `[tool.doeff-linter] architecture` names the declaration files (paths or globs, default
//! architecture.hy), and those files are read at the base and parsed together. The tree after the change is read only for the declaration's spelling, to see whether it changed.

use std::path::Path;

use doeff_linter::project::architecture::{Architecture, DesignEvidenceDecl};
use doeff_linter::project::ledger_guard::declaration_spelling;
use doeff_linter::project::paths::glob_matches;

use crate::git::{BaseTree, ChangedTree, FileRead};

/// The declaration at the base and the file that holds it.
pub(crate) struct BaseDeclaration {
    pub(crate) decl: DesignEvidenceDecl,
    pub(crate) file: String,
    pub(crate) spelling: String,
}

const PYPROJECT: &str = "pyproject.toml";
const DEFAULT_ARCHITECTURE: &str = "architecture.hy";

fn architecture_patterns(pyproject: &str) -> Result<Vec<String>, String> {
    let value: toml::Value = toml::from_str(pyproject).map_err(|e| format!("{} at the base: {}", PYPROJECT, e.to_string().trim()))?;
    let declared = value.get("tool").and_then(|t| t.get("doeff-linter")).and_then(|t| t.get("architecture"));
    match declared {
        None => Ok(vec![DEFAULT_ARCHITECTURE.to_string()]),
        Some(toml::Value::String(one)) => Ok(vec![one.clone()]),
        Some(toml::Value::Array(many)) => many
            .iter()
            .map(|v| v.as_str().map(str::to_string).ok_or_else(|| format!("{} at the base: [tool.doeff-linter] architecture holds a non-string", PYPROJECT)))
            .collect(),
        Some(_) => Err(format!("{} at the base: [tool.doeff-linter] architecture is neither a path nor a list", PYPROJECT)),
    }
}

fn present(base: &BaseTree, rel: &str) -> Result<String, String> {
    match base.read(rel) {
        FileRead::Present(text) => Ok(text),
        FileRead::Absent => Err(format!("{} is not at the base", rel)),
        FileRead::Unreadable(reason) => Err(reason),
    }
}

/// Reads the declaration at the base (Err with the reason if it cannot be read or declares no `:design-evidence` — the gate then cannot judge, and fails closed).
pub(crate) fn at_base(base: &BaseTree) -> Result<BaseDeclaration, String> {
    let patterns = architecture_patterns(&present(base, PYPROJECT)?)?;
    let tree = base.paths()?;
    let files: Vec<&String> = tree.iter().filter(|rel| patterns.iter().any(|p| p == *rel || glob_matches(p, rel))).collect();
    if files.is_empty() {
        return Err(format!("no declaration file at the base matches {:?}", patterns));
    }
    let mut sources: Vec<(String, String)> = Vec::new();
    for rel in files {
        sources.push((rel.clone(), present(base, rel)?));
    }
    let borrowed: Vec<(&Path, &str)> = sources.iter().map(|(rel, text)| (Path::new(rel.as_str()), text.as_str())).collect();
    let architecture = Architecture::parse_sources(&borrowed, base.repo()).map_err(|problems| format!("the declaration at the base cannot be read: {}", problems.join("; ")))?;
    let decl = architecture.design_evidence.ok_or("the declaration at the base has no :design-evidence")?;
    let (file, spelling) = sources
        .iter()
        .find_map(|(rel, text)| declaration_spelling(text).map(|s| (rel.clone(), s)))
        .ok_or("no declaration file at the base spells :design-evidence")?;
    Ok(BaseDeclaration { decl, file, spelling })
}

/// The declaration's spelling in the tree after the change (None if the file or the key is gone — a removed declaration is a change).
pub(crate) fn spelling_now(now: &ChangedTree, file: &str) -> Result<Option<String>, String> {
    match now.read(file) {
        FileRead::Present(text) => Ok(declaration_spelling(&text)),
        FileRead::Absent => Ok(None),
        FileRead::Unreadable(reason) => Err(reason),
    }
}
