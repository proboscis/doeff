//! Compact aggregate `counts` format for `doeff-complexity count` and `gate`.
//!
//! Stores repo-wide aggregates (count, sum, max, and optional threshold totals) for types,
//! modules, and functions — including module sum and max for daily metrics — plus one compact
//! aggregate record per production Hy module so `gate` reads base module WMC and prunes clean
//! files without reading a 14 MB per-function TSV.

use std::collections::BTreeMap;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::charter::OptionalThresholds;
use crate::measure::FileMetrics;

pub const COUNTS_SCHEMA: u32 = 1;

/// Repo-wide aggregate counts for one unit (types, modules, or functions).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UnitSummary {
    pub count: usize,
    pub sum: usize,
    pub max: usize,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub threshold: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub over_count: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub over_sum: Option<usize>,
}

/// Repo-wide aggregates across all production Hy files.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Summary {
    pub types: UnitSummary,
    pub modules: UnitSummary,
    pub functions: UnitSummary,
}

/// Compact per-module aggregate (no per-function rows).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModuleAggregate {
    pub wmc: usize,
    pub type_count: usize,
    pub type_sum: usize,
    pub type_max: usize,
    pub fn_count: usize,
    pub fn_sum: usize,
    pub fn_max: usize,
}

/// Complete `counts` file written by `count` and read by `gate`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CountsFile {
    pub schema: u32,
    pub summary: Summary,
    pub modules: BTreeMap<String, ModuleAggregate>,
}

/// Streaming builder that accumulates repo-wide and per-module aggregates without keeping per-function rows.
pub struct CountsBuilder {
    thresholds: OptionalThresholds,
    type_count: usize,
    type_sum: usize,
    type_max: usize,
    type_over_count: usize,
    type_over_sum: usize,
    module_count: usize,
    module_sum: usize,
    module_max: usize,
    module_over_count: usize,
    module_over_sum: usize,
    fn_count: usize,
    fn_sum: usize,
    fn_max: usize,
    fn_over_count: usize,
    fn_over_sum: usize,
    modules: BTreeMap<String, ModuleAggregate>,
}

impl CountsBuilder {
    pub fn new(thresholds: OptionalThresholds) -> Self {
        Self {
            thresholds,
            type_count: 0,
            type_sum: 0,
            type_max: 0,
            type_over_count: 0,
            type_over_sum: 0,
            module_count: 0,
            module_sum: 0,
            module_max: 0,
            module_over_count: 0,
            module_over_sum: 0,
            fn_count: 0,
            fn_sum: 0,
            fn_max: 0,
            fn_over_count: 0,
            fn_over_sum: 0,
            modules: BTreeMap::new(),
        }
    }

    pub fn add(&mut self, path: String, metrics: &FileMetrics) {
        let tc = metrics.type_count();
        let ts = metrics.type_sum();
        let tm = metrics.type_max();
        let fc = metrics.fn_count();
        let fs = metrics.fn_sum();
        let fm = metrics.fn_max();

        self.type_count += tc;
        self.type_sum += ts;
        self.type_max = self.type_max.max(tm);
        if let Some(t) = self.thresholds.max_type_wmc {
            for &w in metrics.types.values() {
                if w >= t {
                    self.type_over_count += 1;
                    self.type_over_sum += w;
                }
            }
        }

        // Only modules with at least one counted definition or loose branch (matching design section 3 `dist.py`).
        if metrics.module_wmc > 0 || fc > 0 || tc > 0 {
            self.module_count += 1;
            self.module_sum += metrics.module_wmc;
            self.module_max = self.module_max.max(metrics.module_wmc);
            if let Some(m) = self.thresholds.max_module_wmc {
                if metrics.module_wmc >= m {
                    self.module_over_count += 1;
                    self.module_over_sum += metrics.module_wmc;
                }
            }
        }

        self.fn_count += fc;
        self.fn_sum += fs;
        self.fn_max = self.fn_max.max(fm);
        if let Some(f) = self.thresholds.max_fn_cc {
            for func in &metrics.fns {
                if func.cc > f {
                    self.fn_over_count += 1;
                    self.fn_over_sum += func.cc;
                }
            }
        }

        if metrics.module_wmc > 0 || fc > 0 || tc > 0 {
            self.modules.insert(
                path,
                ModuleAggregate {
                    wmc: metrics.module_wmc,
                    type_count: tc,
                    type_sum: ts,
                    type_max: tm,
                    fn_count: fc,
                    fn_sum: fs,
                    fn_max: fm,
                },
            );
        }
    }

    pub fn finish(self) -> CountsFile {
        CountsFile {
            schema: COUNTS_SCHEMA,
            summary: Summary {
                types: UnitSummary {
                    count: self.type_count,
                    sum: self.type_sum,
                    max: self.type_max,
                    threshold: self.thresholds.max_type_wmc,
                    over_count: self.thresholds.max_type_wmc.map(|_| self.type_over_count),
                    over_sum: self.thresholds.max_type_wmc.map(|_| self.type_over_sum),
                },
                modules: UnitSummary {
                    count: self.module_count,
                    sum: self.module_sum,
                    max: self.module_max,
                    threshold: self.thresholds.max_module_wmc,
                    over_count: self.thresholds.max_module_wmc.map(|_| self.module_over_count),
                    over_sum: self.thresholds.max_module_wmc.map(|_| self.module_over_sum),
                },
                functions: UnitSummary {
                    count: self.fn_count,
                    sum: self.fn_sum,
                    max: self.fn_max,
                    threshold: self.thresholds.max_fn_cc,
                    over_count: self.thresholds.max_fn_cc.map(|_| self.fn_over_count),
                    over_sum: self.thresholds.max_fn_cc.map(|_| self.fn_over_sum),
                },
            },
            modules: self.modules,
        }
    }
}

impl CountsFile {
    pub fn to_json_string(&self) -> Result<String, String> {
        let mut s = serde_json::to_string(self)
            .map_err(|e| format!("cannot serialize counts: {}", e))?;
        s.push('\n');
        Ok(s)
    }

    pub fn read_file(path: &Path) -> Result<CountsFile, String> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| format!("cannot read counts file {}: {}", path.display(), e))?;
        let parsed: CountsFile = serde_json::from_str(&text)
            .map_err(|e| format!("cannot parse counts file {}: {}", path.display(), e))?;
        if parsed.schema != COUNTS_SCHEMA {
            return Err(format!(
                "unsupported counts schema {} in {} (expected {})",
                parsed.schema,
                path.display(),
                COUNTS_SCHEMA
            ));
        }
        Ok(parsed)
    }
}
