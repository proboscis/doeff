use std::env;
use std::fmt;
use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

pub mod effect_registry;
pub mod function_summary;
#[cfg(feature = "python")]
pub mod python_api;
pub mod resolver;
pub mod source;
pub mod summary;
pub mod syntax;

use crate::effect_registry::EffectRegistry;
use crate::resolver::ResolvedTarget;
use crate::summary::{summarize_target, SummarizedEffects};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EffectUsage {
    pub key: String,
    pub span: Option<SourceSpan>,
    pub via: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EffectSummary {
    pub qualified_name: String,
    pub module: String,
    pub target_kind: TargetKind,
    pub defined_at: Option<SourceSpan>,
    pub effects: Vec<EffectUsage>,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EffectTreeNode {
    pub kind: NodeKind,
    pub label: String,
    pub effects: Vec<String>,
    pub span: Option<SourceSpan>,
    pub children: Vec<EffectTreeNode>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EffectTree {
    pub root: EffectTreeNode,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Report {
    pub summary: EffectSummary,
    pub tree: EffectTree,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceSpan {
    pub file: String,
    pub line: u32,
    pub column: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TargetKind {
    KleisliProgram,
    ProgramValue,
    Other,
}

impl TargetKind {
    pub fn as_str(self) -> &'static str {
        match self {
            TargetKind::KleisliProgram => "kleisli_program",
            TargetKind::ProgramValue => "program_value",
            TargetKind::Other => "other",
        }
    }
}

impl fmt::Display for TargetKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NodeKind {
    Root,
    Function,
    Effect,
    Unresolved,
}

/// Placeholder analysis entrypoint.
///
/// Resolves the dotted path inside Rust to classify the target before returning a stubbed report.
pub fn analyze_with_root(root: &Path, dotted: &str) -> Result<Report> {
    let target = resolver::resolve(root, dotted)?;
    analyze_resolved(root, target)
}

pub fn analyze_dotted_path(dotted: &str) -> Result<Report> {
    let root = env::current_dir()?;
    analyze_with_root(&root, dotted)
}

pub fn analyze_symbol(module_path: &str, symbol: &str) -> Result<Report> {
    let dotted = format!("{module_path}.{symbol}");
    analyze_dotted_path(&dotted)
}

fn analyze_resolved(root: &Path, target: ResolvedTarget) -> Result<Report> {
    let mut warnings: Vec<String> = Vec::new();

    let source_text = fs::read_to_string(&target.file_path)
        .with_context(|| format!("failed to read '{}'", target.file_path.display()))?;

    let registry = EffectRegistry::default();

    let analysis = match syntax::parse_module(&source_text) {
        Ok(tree) => summarize_target(
            target.kind,
            &target.symbol,
            &target.module,
            &source_text,
            &tree,
            &target.file_path,
            target.definition_span.as_ref(),
            root,
            &registry,
        ),
        Err(err) => {
            warnings.push(format!(
                "Tree-sitter parse failed for '{}': {err}",
                target.file_path.display()
            ));
            SummarizedEffects::empty()
        }
    };

    let SummarizedEffects {
        label,
        effects: collected_effects,
        effect_nodes,
        warnings: analysis_warnings,
        root_span,
    } = analysis;

    warnings.extend(analysis_warnings);

    let summary = EffectSummary {
        qualified_name: target.dotted_path.clone(),
        module: target.module.clone(),
        target_kind: target.kind,
        defined_at: target.definition_span.clone(),
        effects: collected_effects,
        warnings,
    };

    let function_node = EffectTreeNode {
        kind: NodeKind::Function,
        label: if label.is_empty() {
            target.symbol.clone()
        } else {
            label
        },
        effects: summary
            .effects
            .iter()
            .map(|effect| effect.key.clone())
            .collect(),
        span: root_span.clone().or_else(|| target.definition_span.clone()),
        children: effect_nodes,
    };

    let root_node = EffectTreeNode {
        kind: NodeKind::Root,
        label: target.dotted_path,
        effects: Vec::new(),
        span: target.definition_span,
        children: vec![function_node],
    };

    Ok(Report {
        summary,
        tree: EffectTree { root: root_node },
    })
}
