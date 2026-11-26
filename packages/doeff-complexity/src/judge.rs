//! Pure complexity ratchet judgement (design sections 6 & 6.1).
//!
//! Rules:
//! 1. Function (limit F = `max_fn_cc`): reject any function with `after > F` that grew
//!    (`after > base`) or newly exceeded F, after matching moved/renamed functions against
//!    functions deleted in the same change (same name first, then remaining by descending CC).
//! 2. Type (limit T = `max_type_wmc`): reject any type/handler with `after >= T` that grew
//!    (`after > base`) or newly exceeded T (file renames mapped via git `-M`; cross-file moved
//!    types matched by same name).
//! 3. Module (limit M = `max_module_wmc`): reject ONLY modules that newly cross M
//!    (`base < M <= after`). Growing a module that was already `>= M` is allowed (decision
//!    `lt-01ZKF7Y79BQHRMK294G89CZJE5`). File renames map `new_path -> old_path` via git `-M`.
//! 4. Ceilings: reject if any changed type exceeds `ceiling_god_wmc_max` or any changed function
//!    exceeds `ceiling_fn_cc_max`.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

use crate::charter::Thresholds;
use crate::measure::{FileMetrics, FnMetric};

/// One complexity violation reported on stdout when exiting with code 1.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Violation {
    ModuleNewlyExceeded {
        path: String,
        base: usize,
        after: usize,
        limit: usize,
    },
    TypeExceeded {
        path: String,
        name: String,
        base: usize,
        after: usize,
        limit: usize,
    },
    TypeCeilingExceeded {
        path: String,
        name: String,
        after: usize,
        ceiling: usize,
    },
    FnExceeded {
        path: String,
        line: usize,
        owner: String,
        name: String,
        base: usize,
        after: usize,
        limit: usize,
    },
    FnCeilingExceeded {
        path: String,
        line: usize,
        owner: String,
        name: String,
        after: usize,
        ceiling: usize,
    },
}

impl fmt::Display for Violation {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Violation::ModuleNewlyExceeded { path, base, after, limit } => {
                write!(f, "module {}: {} -> {} (limit {})", path, base, after, limit)
            }
            Violation::TypeExceeded { path, name, base, after, limit } => {
                write!(f, "type {}::{}: {} -> {} (limit {})", path, name, base, after, limit)
            }
            Violation::TypeCeilingExceeded { path, name, after, ceiling } => {
                write!(
                    f,
                    "type {}::{}: {} exceeds ceiling_god_wmc_max {}",
                    path, name, after, ceiling
                )
            }
            Violation::FnExceeded { path, line, owner, name, base, after, limit } => {
                if owner.is_empty() {
                    write!(
                        f,
                        "function {}:{} {}: {} -> {} (limit {})",
                        path, line, name, base, after, limit
                    )
                } else {
                    write!(
                        f,
                        "function {}:{} {}.{}: {} -> {} (limit {})",
                        path, line, owner, name, base, after, limit
                    )
                }
            }
            Violation::FnCeilingExceeded { path, line, owner, name, after, ceiling } => {
                if owner.is_empty() {
                    write!(
                        f,
                        "function {}:{} {}: {} exceeds ceiling_fn_cc_max {}",
                        path, line, name, after, ceiling
                    )
                } else {
                    write!(
                        f,
                        "function {}:{} {}.{}: {} exceeds ceiling_fn_cc_max {}",
                        path, line, owner, name, after, ceiling
                    )
                }
            }
        }
    }
}

/// Judges changed Hy files against `thresholds` using base module WMCs, loaded base file metrics,
/// after file metrics, and git rename mappings (`new_path -> old_path`).
pub fn judge_changes(
    thresholds: Thresholds,
    base_module_wmcs: &BTreeMap<String, usize>,
    base_files: &BTreeMap<String, FileMetrics>,
    after_files: &BTreeMap<String, FileMetrics>,
    rename_map: &BTreeMap<String, String>,
) -> Vec<Violation> {
    let mut violations = Vec::new();

    // 1. Module check (newly exceeding only: base < M <= after).
    for (path, after) in after_files {
        let base_path = rename_map.get(path).unwrap_or(path);
        let base_wmc = base_module_wmcs.get(base_path).copied().unwrap_or(0);
        if base_wmc < thresholds.max_module_wmc && after.module_wmc >= thresholds.max_module_wmc {
            violations.push(Violation::ModuleNewlyExceeded {
                path: path.clone(),
                base: base_wmc,
                after: after.module_wmc,
                limit: thresholds.max_module_wmc,
            });
        }
    }

    // 2. Type check (after >= T && after > base, plus ceiling_god_wmc_max).
    let mut after_logical_types: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for (path, after) in after_files {
        let logical = rename_map.get(path).unwrap_or(path).clone();
        let entry = after_logical_types.entry(logical).or_default();
        for name in after.types.keys() {
            entry.insert(name.clone());
        }
    }
    // Deleted types across changed base files (types present in base that disappeared from that logical file).
    let mut deleted_types: Vec<(String, usize, bool)> = Vec::new();
    for (base_path, bf) in base_files {
        let present_after = after_logical_types.get(base_path);
        for (name, &wmc) in &bf.types {
            if !present_after.is_some_and(|set| set.contains(name)) {
                deleted_types.push((name.clone(), wmc, false));
            }
        }
    }
    deleted_types.sort_by(|a, b| b.1.cmp(&a.1));

    // Added/grown types to check (collect added over-limit types to match in descending WMC order).
    let mut added_types_over: Vec<(String, String, usize)> = Vec::new();
    for (path, after) in after_files {
        let base_path = rename_map.get(path).unwrap_or(path);
        let base_types = base_files.get(base_path).map(|bf| &bf.types);
        for (type_name, &after_wmc) in &after.types {
            if let Some(ceil) = thresholds.ceiling_god_wmc_max {
                if after_wmc > ceil {
                    violations.push(Violation::TypeCeilingExceeded {
                        path: path.clone(),
                        name: type_name.clone(),
                        after: after_wmc,
                        ceiling: ceil,
                    });
                }
            }
            if after_wmc < thresholds.max_type_wmc {
                continue;
            }
            if let Some(&base_wmc) = base_types.and_then(|m| m.get(type_name)) {
                if after_wmc > base_wmc {
                    violations.push(Violation::TypeExceeded {
                        path: path.clone(),
                        name: type_name.clone(),
                        base: base_wmc,
                        after: after_wmc,
                        limit: thresholds.max_type_wmc,
                    });
                }
            } else {
                added_types_over.push((path.clone(), type_name.clone(), after_wmc));
            }
        }
    }
    added_types_over.sort_by(|a, b| b.2.cmp(&a.2).then_with(|| a.0.cmp(&b.0)).then_with(|| a.1.cmp(&b.1)));
    for (path, type_name, after_wmc) in added_types_over {
        let matched = deleted_types
            .iter_mut()
            .find(|(del_name, _, used)| !*used && *del_name == type_name);
        match matched {
            Some((_, del_wmc, used)) => {
                *used = true;
                if after_wmc > *del_wmc {
                    violations.push(Violation::TypeExceeded {
                        path,
                        name: type_name,
                        base: *del_wmc,
                        after: after_wmc,
                        limit: thresholds.max_type_wmc,
                    });
                }
            }
            None => {
                violations.push(Violation::TypeExceeded {
                    path,
                    name: type_name,
                    base: 0,
                    after: after_wmc,
                    limit: thresholds.max_type_wmc,
                });
            }
        }
    }

    // 3. Function check (ceiling_fn_cc_max + in-place match + deleted-pool move/rename match).
    for (path, after) in after_files {
        if let Some(ceil) = thresholds.ceiling_fn_cc_max {
            for func in &after.fns {
                if func.cc > ceil {
                    violations.push(Violation::FnCeilingExceeded {
                        path: path.clone(),
                        line: func.line,
                        owner: func.owner.clone(),
                        name: func.name.clone(),
                        after: func.cc,
                        ceiling: ceil,
                    });
                }
            }
        }
    }

    type FnKey = (String, String, String); // (logical_path, owner, name)
    let mut base_groups: BTreeMap<FnKey, Vec<usize>> = BTreeMap::new();
    for (base_path, bf) in base_files {
        for func in &bf.fns {
            base_groups
                .entry((base_path.clone(), func.owner.clone(), func.name.clone()))
                .or_default()
                .push(func.cc);
        }
    }
    for list in base_groups.values_mut() {
        list.sort_by(|a, b| b.cmp(a));
    }

    let mut after_groups: BTreeMap<FnKey, Vec<(String, &FnMetric)>> = BTreeMap::new();
    for (path, after) in after_files {
        let logical = rename_map.get(path).unwrap_or(path).clone();
        for func in &after.fns {
            after_groups
                .entry((logical.clone(), func.owner.clone(), func.name.clone()))
                .or_default()
                .push((path.clone(), func));
        }
    }
    for list in after_groups.values_mut() {
        list.sort_by(|a, b| b.1.cc.cmp(&a.1.cc).then_with(|| a.1.line.cmp(&b.1.line)));
    }

    let mut all_keys: BTreeSet<FnKey> = BTreeSet::new();
    all_keys.extend(base_groups.keys().cloned());
    all_keys.extend(after_groups.keys().cloned());

    let mut deleted_pool: Vec<(String, usize, bool)> = Vec::new(); // (name, cc, used)
    let mut added_over_limit: Vec<(String, &FnMetric)> = Vec::new();

    for key in all_keys {
        let bs = base_groups.get(&key).map(Vec::as_slice).unwrap_or(&[]);
        let as_ = after_groups.get(&key).map(Vec::as_slice).unwrap_or(&[]);
        let paired = bs.len().min(as_.len());
        for i in 0..paired {
            let base_cc = bs[i];
            let (ref after_path, af) = as_[i];
            if af.cc > thresholds.max_fn_cc && af.cc > base_cc {
                violations.push(Violation::FnExceeded {
                    path: after_path.clone(),
                    line: af.line,
                    owner: af.owner.clone(),
                    name: af.name.clone(),
                    base: base_cc,
                    after: af.cc,
                    limit: thresholds.max_fn_cc,
                });
            }
        }
        for &b_cc in &bs[paired..] {
            deleted_pool.push((key.2.clone(), b_cc, false));
        }
        for (after_path, af) in &as_[paired..] {
            if af.cc > thresholds.max_fn_cc {
                added_over_limit.push((after_path.clone(), *af));
            }
        }
    }

    // Sort deleted pool by CC descending so same-name and fallback matches pick highest CC first.
    deleted_pool.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    added_over_limit.sort_by(|a, b| {
        b.1.cc
            .cmp(&a.1.cc)
            .then_with(|| a.0.cmp(&b.0))
            .then_with(|| a.1.line.cmp(&b.1.line))
    });

    // Pass 1: same-name match against deleted functions (handles moving method <-> free fn, or across files).
    let mut unmatched_added: Vec<(String, &FnMetric)> = Vec::new();
    for (after_path, af) in added_over_limit {
        let matched = deleted_pool
            .iter_mut()
            .find(|(del_name, _, used)| !*used && *del_name == af.name);
        match matched {
            Some((_, del_cc, used)) => {
                *used = true;
                if af.cc > *del_cc {
                    violations.push(Violation::FnExceeded {
                        path: after_path,
                        line: af.line,
                        owner: af.owner.clone(),
                        name: af.name.clone(),
                        base: *del_cc,
                        after: af.cc,
                        limit: thresholds.max_fn_cc,
                    });
                }
            }
            None => unmatched_added.push((after_path, af)),
        }
    }

    // Pass 2: remaining unmatched added functions paired with remaining deleted functions by descending CC.
    let remaining_deleted: Vec<usize> = deleted_pool
        .into_iter()
        .filter(|(_, _, used)| !*used)
        .map(|(_, cc, _)| cc)
        .collect();
    for (idx, (after_path, af)) in unmatched_added.into_iter().enumerate() {
        let matched_base = remaining_deleted.get(idx).copied().unwrap_or(0);
        if af.cc > matched_base {
            violations.push(Violation::FnExceeded {
                path: after_path,
                line: af.line,
                owner: af.owner.clone(),
                name: af.name.clone(),
                base: matched_base,
                after: af.cc,
                limit: thresholds.max_fn_cc,
            });
        }
    }

    violations
}
