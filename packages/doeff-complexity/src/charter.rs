//! Reading complexity thresholds and ceilings from `[budget]` in `repo-charter.toml`.

use serde::Deserialize;

/// The complexity lines and ceilings read from `[budget]` in the base commit's `repo-charter.toml`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Thresholds {
    /// Function CC limit F (`after > F` is rejected when newly exceeding or growing).
    pub max_fn_cc: usize,
    /// Type WMC limit T (`after >= T` is rejected when newly exceeding or growing).
    pub max_type_wmc: usize,
    /// Module WMC limit M (`base < M && after >= M` is rejected — newly exceeding only).
    pub max_module_wmc: usize,
    /// Optional ceiling for maximum type WMC (`after > ceiling` is rejected).
    pub ceiling_god_wmc_max: Option<usize>,
    /// Optional ceiling for maximum function CC (`after > ceiling` is rejected).
    pub ceiling_fn_cc_max: Option<usize>,
}

/// Optional thresholds for `count` summary reporting when `repo-charter.toml` may be absent or incomplete.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct OptionalThresholds {
    pub max_fn_cc: Option<usize>,
    pub max_type_wmc: Option<usize>,
    pub max_module_wmc: Option<usize>,
}

#[derive(Debug, Deserialize)]
struct CharterDoc {
    budget: Option<BudgetSection>,
}

#[derive(Debug, Deserialize)]
struct BudgetSection {
    max_fn_cc: Option<usize>,
    max_type_wmc: Option<usize>,
    max_module_wmc: Option<usize>,
    ceiling_god_wmc_max: Option<usize>,
    ceiling_fn_cc_max: Option<usize>,
}

/// Parses required gate thresholds from `repo-charter.toml` text (read at `--base`).
pub fn parse_gate_thresholds(toml_text: &str) -> Result<Thresholds, String> {
    let doc: CharterDoc = toml::from_str(toml_text)
        .map_err(|e| format!("cannot parse repo-charter.toml: {}", e))?;
    let budget = doc
        .budget
        .ok_or_else(|| "repo-charter.toml has no [budget] section".to_string())?;
    let max_fn_cc = budget
        .max_fn_cc
        .ok_or_else(|| "repo-charter.toml [budget] is missing max_fn_cc".to_string())?;
    let max_type_wmc = budget
        .max_type_wmc
        .ok_or_else(|| "repo-charter.toml [budget] is missing max_type_wmc".to_string())?;
    let max_module_wmc = budget
        .max_module_wmc
        .ok_or_else(|| "repo-charter.toml [budget] is missing max_module_wmc".to_string())?;
    Ok(Thresholds {
        max_fn_cc,
        max_type_wmc,
        max_module_wmc,
        ceiling_god_wmc_max: budget.ceiling_god_wmc_max,
        ceiling_fn_cc_max: budget.ceiling_fn_cc_max,
    })
}

/// Best-effort parse of threshold values from `repo-charter.toml` for `count` summary stats.
pub fn parse_optional_thresholds(toml_text: &str) -> Result<OptionalThresholds, String> {
    let doc: CharterDoc = toml::from_str(toml_text)
        .map_err(|e| format!("cannot parse repo-charter.toml: {}", e))?;
    let Some(budget) = doc.budget else {
        return Ok(OptionalThresholds::default());
    };
    Ok(OptionalThresholds {
        max_fn_cc: budget.max_fn_cc,
        max_type_wmc: budget.max_type_wmc,
        max_module_wmc: budget.max_module_wmc,
    })
}
