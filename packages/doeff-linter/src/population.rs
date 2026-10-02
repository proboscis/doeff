//! Python の文ごとの規則の母集団を、file ごとに、その file を持つ package の architecture.hy の層の宣言から決める 1 点
//! (agora-redesign #2811)。
//!
//! linter は 1 回の実行に宣言 1 つ(設定の architecture か、根の architecture.hy)を読む。doeff の根の実行(pre-commit・make lint-doeff)は
//! package の宣言を読まないので、package が層に書いた規則の除外が効かなかった。ここでは file から上へ最も近い architecture.hy
//! (その package の持ち主の宣言)を引き、その file が除外を持つ層(`:modules` の名指し・`:exempt [(rule 規則 ID "理由")]`)の module なら:
//!   - 除外した規則の当たりを落とす(理由は宣言に在る — 理由の無い除外は宣言の読みが断る)
//!   - その層の `:forbid-modules` の module の import を DOEFF032 で名指す(外した層に業務の code が入ったら赤)
//! 宣言を読めない時は、その file の誤りとして名指す(黙って外さない・黙って当てない)。
//!
//! module の名は Python の決まり(`__init__.py` の在る dir を上へたどる)で出す — 設定の根(root)にも architecture.hy の :root
//! (層の置き場の根)にも依らない。

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use rustpython_ast::{Mod, Stmt};

use crate::models::{Severity, Violation};
use crate::project::architecture::Architecture;

/// 外した層の module が業務の module を import した当たりの規則 ID(名の持ち主は rules::doeff032_rule_population_declaration)。
pub use crate::rules::doeff032_rule_population_declaration::RULE_ID as BUSINESS_IMPORT_RULE_ID;

/// file 1 つの母集団の答え。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FilePopulation {
    /// 除外を持つ層の module ではない — どの規則もそのまま当たる。
    Plain,
    /// 除外を持つ層の名指しの module。
    Exempt {
        layer: String,
        rules: Vec<String>,
        forbid_modules: Vec<String>,
        declaration: PathBuf,
    },
}

type Declarations = HashMap<PathBuf, Result<Arc<Architecture>, String>>;

/// 宣言の file → 読んだ宣言(か読めなかった理由)。1 回の実行の中で同じ宣言を 1 度だけ読む。
fn declarations() -> &'static Mutex<Declarations> {
    static CACHE: OnceLock<Mutex<Declarations>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// file から上へ最も近い architecture.hy。repo の根(`.git` の在る dir)より上は見ない。
fn nearest_declaration(file: &Path) -> Option<PathBuf> {
    let mut dir = file.parent()?;
    loop {
        let candidate = dir.join("architecture.hy");
        if candidate.is_file() {
            return Some(candidate);
        }
        if dir.join(".git").exists() {
            return None;
        }
        dir = dir.parent()?;
    }
}

/// file の module の名(点で区切った綴り)— `__init__.py` の在る dir を上へたどって組む(`a/b/__init__.py` は `a.b`)。
fn module_name(file: &Path) -> Option<String> {
    let stem = file.file_stem()?.to_str()?.to_string();
    let mut parts: Vec<String> = if stem == "__init__" {
        Vec::new()
    } else {
        vec![stem]
    };
    let mut dir = file.parent()?;
    while dir.join("__init__.py").is_file() {
        parts.push(dir.file_name()?.to_str()?.to_string());
        dir = dir.parent()?;
    }
    if parts.is_empty() {
        return None;
    }
    parts.reverse();
    Some(parts.join("."))
}

/// 宣言の file を読む(同じ宣言は 1 度だけ)。根は宣言の在る dir。
fn declaration(path: &Path) -> Result<Arc<Architecture>, String> {
    let mut cache = declarations()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    cache
        .entry(path.to_path_buf())
        .or_insert_with(|| {
            let root = path.parent().unwrap_or(Path::new("."));
            Architecture::load(path, root)
                .map(Arc::new)
                .map_err(|problems| {
                    format!(
                        "{} の誤り(この file の規則の母集団を決められない):\n  {}",
                        path.display(),
                        problems.join("\n  ")
                    )
                })
        })
        .clone()
}

/// file の母集団を決める。宣言を読めない時は Err(理由)。
pub fn population_of(file: &Path) -> Result<FilePopulation, String> {
    let absolute = file.canonicalize().unwrap_or_else(|_| file.to_path_buf());
    let Some(path) = nearest_declaration(&absolute) else {
        return Ok(FilePopulation::Plain);
    };
    let architecture = declaration(&path)?;
    let Some(module) = module_name(&absolute) else {
        return Ok(FilePopulation::Plain);
    };
    Ok(match architecture.exempt_layer_of(&module) {
        Some(layer) => FilePopulation::Exempt {
            layer: layer.name.clone(),
            rules: layer.exempt.iter().map(|e| e.rule.clone()).collect(),
            forbid_modules: layer.forbid_modules.clone(),
            declaration: path,
        },
        None => FilePopulation::Plain,
    })
}

/// import の名(`a.b.c`)が、禁じた module(`a` か `a.b`)かその下か。
fn forbidden(name: &str, forbid_modules: &[String]) -> Option<String> {
    forbid_modules
        .iter()
        .find(|m| {
            name == m.as_str()
                || name
                    .strip_prefix(m.as_str())
                    .is_some_and(|rest| rest.starts_with('.'))
        })
        .cloned()
}

/// 文の列(入れ子の関数・class・分岐の中を含む)から、禁じた module の import を集める(位置と import の名と禁じた module)。
fn forbidden_imports(
    stmts: &[Stmt],
    forbid_modules: &[String],
    out: &mut Vec<(usize, String, String)>,
) {
    for stmt in stmts {
        match stmt {
            Stmt::Import(import) => {
                for alias in &import.names {
                    if let Some(hit) = forbidden(alias.name.as_str(), forbid_modules) {
                        out.push((import.range.start().to_usize(), alias.name.to_string(), hit));
                    }
                }
            }
            Stmt::ImportFrom(import) => {
                // 相対 import(level > 0)は同じ package の中 — 禁じた module の綴りにはならない。
                let level: u32 = import.level.as_ref().map(|l| l.to_u32()).unwrap_or(0);
                if let (Some(module), 0) = (&import.module, level) {
                    if let Some(hit) = forbidden(module.as_str(), forbid_modules) {
                        out.push((import.range.start().to_usize(), module.to_string(), hit));
                    }
                }
            }
            Stmt::FunctionDef(def) => forbidden_imports(&def.body, forbid_modules, out),
            Stmt::AsyncFunctionDef(def) => forbidden_imports(&def.body, forbid_modules, out),
            Stmt::ClassDef(def) => forbidden_imports(&def.body, forbid_modules, out),
            Stmt::If(s) => {
                forbidden_imports(&s.body, forbid_modules, out);
                forbidden_imports(&s.orelse, forbid_modules, out);
            }
            Stmt::For(s) => {
                forbidden_imports(&s.body, forbid_modules, out);
                forbidden_imports(&s.orelse, forbid_modules, out);
            }
            Stmt::While(s) => {
                forbidden_imports(&s.body, forbid_modules, out);
                forbidden_imports(&s.orelse, forbid_modules, out);
            }
            Stmt::With(s) => forbidden_imports(&s.body, forbid_modules, out),
            Stmt::Try(s) => {
                forbidden_imports(&s.body, forbid_modules, out);
                for handler in &s.handlers {
                    if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                        forbidden_imports(&h.body, forbid_modules, out);
                    }
                }
                forbidden_imports(&s.orelse, forbid_modules, out);
                forbidden_imports(&s.finalbody, forbid_modules, out);
            }
            _ => {}
        }
    }
}

/// 外した層の module の、禁じた module の import の当たり(DOEFF032)。
pub fn business_import_violations(
    file_path: &str,
    ast: &Mod,
    layer: &str,
    forbid_modules: &[String],
    declaration: &Path,
) -> Vec<Violation> {
    let Mod::Module(module) = ast else {
        return Vec::new();
    };
    let mut hits = Vec::new();
    forbidden_imports(&module.body, forbid_modules, &mut hits);
    hits.into_iter()
        .map(|(offset, name, hit)| {
            Violation::new(
                BUSINESS_IMPORT_RULE_ID.to_string(),
                format!(
                    "規則の母集団から外した層 {} の module が業務の module {} を import している({} — 層の :forbid-modules の {})。外した層は \
                     起動の時点のような Program の外の code だけを置く所で、業務の code が入ると、外した規則がその code にも当たらなくなる。\
                     業務の code は外していない層へ移す({})",
                    layer,
                    name,
                    file_path,
                    hit,
                    declaration.display()
                ),
                offset,
                file_path.to_string(),
                Severity::Error,
            )
        })
        .collect()
}
