//! Runtime invariant checks (feature = "invariant-checks").
//!
//! Each check encodes a consistency condition that otherwise exists only as
//! code shape (see docs/crystallization/invariants.md for the catalogue and
//! the evidence for each condition). The checks are exhaustive, not fast —
//! they are meant for the reference build (`cargo test --features
//! invariant-checks`), never for release.
//!
//! Policy: a violation is a *finding*, not a nuisance. Do not weaken a check
//! to make a test pass — report it.

use std::collections::{HashMap, HashSet};

use crate::arena::SlotStatus;
use crate::continuation::Continuation;
use crate::frame::{EvalReturnContinuation, Frame};
use crate::ids::FiberId;
use crate::value::Value;
use crate::vm::VM;

/// Everything the checker learned about VM-visible detached chains,
/// shared between checks.
struct DetachedView {
    /// All fiber ids owned by some VM-visible live detached chain.
    detached_ids: HashSet<FiberId>,
}

/// Full invariant report, split by severity.
///
/// `violations` are structural corruption — the VM's own operations assume
/// these never happen; any occurrence is a bug. `tensions` are *known,
/// documented* spec-vs-code contradictions (currently only B14: a Var cell
/// whose owner fiber was freed; `read_scoped_var_from` tolerates this via
/// its global-cells fallback). Tensions are reported, not fatal — resolving
/// them is a crystallization decision, not a checker decision. See
/// docs/crystallization/invariants.md.
pub struct InvariantReport {
    pub violations: Vec<String>,
    pub tensions: Vec<String>,
}

impl VM {
    /// Check all invariants. Returns every violation found (not just the first).
    /// Known tensions (see `InvariantReport`) are NOT errors here; use
    /// `invariant_report` to see both.
    pub fn check_invariants(&self) -> Result<(), Vec<String>> {
        let report = self.invariant_report();
        if report.violations.is_empty() {
            Ok(())
        } else {
            Err(report.violations)
        }
    }

    /// Full report: fatal violations + known tensions.
    pub fn invariant_report(&self) -> InvariantReport {
        let mut violations = Vec::new();
        let mut tensions = Vec::new();

        // I1 — arena slot hygiene (free list consistent with slots).
        violations.extend(self.segments.free_list_violations());

        // I2 — current_segment points at a live fiber.
        self.inv_current_segment(&mut violations);

        // I3 — parent chains are acyclic and never dangle.
        self.inv_parent_chains(&mut violations);

        // I4 — handler boundary coherence (exactly one role, unique markers).
        self.inv_handler_boundaries(&mut violations);

        // I5/I6 — single-location law for detached chains + chain integrity.
        let view = self.inv_detached_chains(&mut violations);

        // I7 — EvalReturn frames reference fibers that still exist somewhere.
        self.inv_eval_return_refs(&view, &mut violations);

        // I8 — Var cells reference owners that were never freed.
        // Known tension (B14): fires on the existing suite, tolerated by the
        // read fallback in vm/var_store.rs. Reported, not fatal.
        self.inv_var_owners(&view, &mut tensions);

        InvariantReport {
            violations,
            tensions,
        }
    }

    /// Panic with the full violation list. Called after every `step()` when
    /// the `invariant-checks` feature is enabled. Known tensions do not panic.
    pub fn assert_invariants_after_step(&self) {
        if let Err(violations) = self.check_invariants() {
            panic!(
                "VM invariant violations after step:\n  - {}",
                violations.join("\n  - ")
            );
        }
    }

    // -----------------------------------------------------------------
    // I2 — current_segment exists
    // -----------------------------------------------------------------
    fn inv_current_segment(&self, violations: &mut Vec<String>) {
        if let Some(seg_id) = self.current_segment {
            if self.segments.get(seg_id).is_none() {
                violations.push(format!(
                    "current_segment {:?} is not a live fiber (status {:?})",
                    seg_id,
                    self.segments.slot_status(seg_id)
                ));
            }
        }
    }

    // -----------------------------------------------------------------
    // I3 — parent chains: acyclic, no dangling parents
    // -----------------------------------------------------------------
    fn inv_parent_chains(&self, violations: &mut Vec<String>) {
        for (id, _) in self.segments.iter() {
            let mut seen = HashSet::new();
            let mut cursor = Some(id);
            while let Some(fid) = cursor {
                if !seen.insert(fid) {
                    violations.push(format!(
                        "parent chain starting at fiber {:?} contains a cycle (revisited {:?})",
                        id, fid
                    ));
                    break;
                }
                let Some(fiber) = self.segments.get(fid) else {
                    violations.push(format!(
                        "fiber chain from {:?} dangles: {:?} has status {:?}",
                        id,
                        fid,
                        self.segments.slot_status(fid)
                    ));
                    break;
                };
                cursor = fiber.parent;
            }
        }
    }

    // -----------------------------------------------------------------
    // I4 — handler boundary coherence
    // -----------------------------------------------------------------
    fn inv_handler_boundaries(&self, violations: &mut Vec<String>) {
        let mut marker_owners: HashMap<u64, Vec<FiberId>> = HashMap::new();
        for (id, fiber) in self.segments.iter() {
            let Some(handler) = &fiber.handler else {
                continue;
            };
            let roles = usize::from(handler.prompt_boundary().is_some())
                + usize::from(handler.intercept_boundary().is_some())
                + usize::from(handler.mask_boundary().is_some());
            if roles != 1 {
                violations.push(format!(
                    "boundary fiber {:?}: handler has {} roles set (expected exactly one of prompt/intercept/mask)",
                    id, roles
                ));
            }
            if handler.marker().raw() == 0 {
                violations.push(format!(
                    "boundary fiber {:?}: placeholder marker (0) installed in arena",
                    id
                ));
            }
            marker_owners.entry(handler.marker().raw()).or_default().push(id);
        }
        for (marker, owners) in marker_owners {
            if owners.len() > 1 {
                violations.push(format!(
                    "marker {} installed on multiple live boundary fibers: {:?}",
                    marker, owners
                ));
            }
        }
    }

    // -----------------------------------------------------------------
    // I5/I6 — detached chains: integrity + single-location law
    // -----------------------------------------------------------------
    fn inv_detached_chains(&self, violations: &mut Vec<String>) -> DetachedView {
        let mut detached_ids: HashSet<FiberId> = HashSet::new();

        // Roots visible from the VM itself (pending k handle).
        if let Some(handle) = &self.pending_handler_k_handle {
            self.scan_k_handle(
                handle,
                "VM.pending_handler_k_handle",
                &mut detached_ids,
                violations,
            );
        }

        // Roots inside live arena fibers (frame k handles + scope values).
        let live_ids: Vec<FiberId> = self.segments.iter().map(|(id, _)| id).collect();
        for id in live_ids {
            if let Some(fiber) = self.segments.get(id) {
                for frame in &fiber.frames {
                    self.scan_frame(
                        frame,
                        &format!("arena fiber {:?}", id),
                        &mut detached_ids,
                        violations,
                    );
                }
            }
        }

        // Roots inside the var store (continuations stored as values).
        for (var, value) in &self.var_store.cells {
            self.scan_value(
                value,
                &format!("var cell {:?}", var),
                &mut detached_ids,
                violations,
            );
        }
        for (key, value) in self.var_store.global_state() {
            self.scan_value(
                value,
                &format!("global_state[{key}]"),
                &mut detached_ids,
                violations,
            );
        }

        DetachedView { detached_ids }
    }

    /// Scan a PyK handle for a live continuation chain.
    fn scan_k_handle(
        &self,
        handle: &pyo3::Py<crate::continuation::PyK>,
        origin: &str,
        detached_ids: &mut HashSet<FiberId>,
        violations: &mut Vec<String>,
    ) {
        pyo3::Python::attach(|py| {
            let k = handle.borrow(py);
            if let Some(crate::continuation::OwnedControlContinuation::Started(ref cont)) =
                k.continuation_ref()
            {
                self.scan_continuation(
                    cont,
                    origin,
                    detached_ids,
                    violations,
                );
            }
        });
    }

    fn scan_frame(
        &self,
        frame: &Frame,
        origin: &str,
        detached_ids: &mut HashSet<FiberId>,
        violations: &mut Vec<String>,
    ) {
        match frame {
            Frame::Program {
                handler_k_handle, ..
            } => {
                if let Some(handle) = handler_k_handle {
                    self.scan_k_handle(
                        handle,
                        &format!("{origin} (Program.handler_k_handle)"),
                        detached_ids,
                        violations,
                    );
                }
            }
            Frame::LexicalScope {
                bindings,
                var_overrides,
            } => {
                for value in bindings.values() {
                    self.scan_value(value, origin, detached_ids, violations);
                }
                for value in var_overrides.values() {
                    self.scan_value(value, origin, detached_ids, violations);
                }
            }
            _ => {}
        }
    }

    fn scan_value(
        &self,
        value: &Value,
        origin: &str,
        detached_ids: &mut HashSet<FiberId>,
        violations: &mut Vec<String>,
    ) {
        match value {
            Value::Continuation(k) => {
                self.scan_continuation(k, origin, detached_ids, violations);
            }
            Value::List(items) => {
                for item in items {
                    self.scan_value(item, origin, detached_ids, violations);
                }
            }
            _ => {}
        }
    }

    /// Validate one continuation's chain integrity and single-location law.
    /// With move-only continuations there are no shared cells to deduplicate.
    fn scan_continuation(
        &self,
        k: &Continuation,
        origin: &str,
        detached_ids: &mut HashSet<FiberId>,
        violations: &mut Vec<String>,
    ) {
        k.inspect_chain(|chain| {
            let Some(chain) = chain else {
                return; // consumed — nothing to check
            };

            let fibers = chain.fibers();
            if fibers.is_empty() {
                violations.push(format!("{origin}: live chain has no fibers"));
                return;
            }

            // Membership + duplicate ids within the chain.
            let mut ids: HashSet<FiberId> = HashSet::new();
            for entry in fibers {
                if !ids.insert(entry.id) {
                    violations.push(format!(
                        "{origin}: fiber {:?} appears twice in one chain",
                        entry.id
                    ));
                }
            }
            for anchor in [chain.head(), chain.last_fiber()] {
                if !ids.contains(&anchor) {
                    violations.push(format!(
                        "{origin}: anchor fiber {:?} not among owned fibers",
                        anchor
                    ));
                }
            }

            // Connectivity: head → … → last must visit every owned fiber.
            let by_id: HashMap<FiberId, &crate::segment::Fiber> =
                fibers.iter().map(|e| (e.id, &e.fiber)).collect();
            let mut visited = 0usize;
            let mut cursor = Some(chain.head());
            let mut reached_tail = false;
            while let Some(fid) = cursor {
                let Some(fiber) = by_id.get(&fid) else {
                    violations.push(format!(
                        "{origin}: chain walk escaped owned set at {:?}",
                        fid
                    ));
                    break;
                };
                visited += 1;
                if visited > fibers.len() {
                    violations.push(format!("{origin}: chain walk cycles"));
                    break;
                }
                if fid == chain.last_fiber() {
                    reached_tail = true;
                    if fiber.parent.is_some() {
                        violations.push(format!(
                            "{origin}: detached tail {:?} still has parent {:?}",
                            fid, fiber.parent
                        ));
                    }
                    break;
                }
                cursor = fiber.parent;
            }
            if !reached_tail {
                violations.push(format!(
                    "{origin}: head {:?} does not reach tail {:?}",
                    chain.head(),
                    chain.last_fiber()
                ));
            } else if visited != fibers.len() {
                violations.push(format!(
                    "{origin}: chain path covers {} of {} owned fibers",
                    visited,
                    fibers.len()
                ));
            }

            // Single-location law: a detached fiber's arena slot must be
            // vacant-reserved (not live, not freed), and no fiber may be
            // owned by two different chains.
            for entry in fibers {
                let status = self.segments.slot_status(entry.id);
                if status != SlotStatus::VacantReserved {
                    violations.push(format!(
                        "{origin}: detached fiber {:?} has arena status {:?} (single-location law: expected VacantReserved)",
                        entry.id, status
                    ));
                }
                if !detached_ids.insert(entry.id) {
                    violations.push(format!(
                        "{origin}: fiber {:?} owned by two distinct chains (single-location law)",
                        entry.id
                    ));
                }

                // Recurse into frames carried by detached fibers that may
                // carry their own handler_k_handle.
                for frame in &entry.fiber.frames {
                    if let Frame::Program {
                        handler_k_handle: Some(nested_handle),
                        ..
                    } = frame
                    {
                        self.scan_k_handle(
                            nested_handle,
                            &format!("{origin} → detached fiber {:?}", entry.id),
                            detached_ids,
                            violations,
                        );
                    }
                }
            }
        });
    }

    // -----------------------------------------------------------------
    // I7 — EvalReturn frames reference fibers that still exist
    // -----------------------------------------------------------------
    fn inv_eval_return_refs(&self, view: &DetachedView, violations: &mut Vec<String>) {
        for (id, fiber) in self.segments.iter() {
            for frame in &fiber.frames {
                let Frame::EvalReturn(cont) = frame else {
                    continue;
                };
                let head = match cont.as_ref() {
                    EvalReturnContinuation::ResumeToContinuation { head_fiber }
                    | EvalReturnContinuation::ReturnToContinuation { head_fiber }
                    | EvalReturnContinuation::EvalInScopeReturn { head_fiber } => *head_fiber,
                    _ => continue,
                };
                let in_arena = self.segments.get(head).is_some();
                let in_detached = view.detached_ids.contains(&head);
                if !in_arena && !in_detached {
                    violations.push(format!(
                        "fiber {:?}: EvalReturn references fiber {:?} which is neither live nor VM-visibly detached (status {:?})",
                        id,
                        head,
                        self.segments.slot_status(head)
                    ));
                }
            }
        }
    }

    // -----------------------------------------------------------------
    // I8 — Var cells: owner fiber was never freed
    // -----------------------------------------------------------------
    fn inv_var_owners(&self, view: &DetachedView, violations: &mut Vec<String>) {
        for var in self.var_store.cells.keys() {
            let owner = var.owner_segment();
            let status = self.segments.slot_status(owner);
            let ok = matches!(status, SlotStatus::Live | SlotStatus::VacantReserved)
                || view.detached_ids.contains(&owner);
            if !ok {
                violations.push(format!(
                    "[B14-tension] var {:?}: owner fiber {:?} has status {:?} (freed or never allocated)",
                    var, owner, status
                ));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use crate::ids::Marker;
    use crate::segment::{Fiber, Handler};
    use crate::value::Value;
    use crate::vm::VM;

    #[test]
    fn empty_vm_holds_invariants() {
        let vm = VM::new();
        assert!(vm.check_invariants().is_ok());
    }

    #[test]
    fn simple_chain_holds_invariants() {
        let mut vm = VM::new();
        let root = vm.alloc_segment(Fiber::new(None));
        let child = vm.alloc_segment(Fiber::new(Some(root)));
        vm.current_segment = Some(child);
        assert!(vm.check_invariants().is_ok());
    }

    #[test]
    fn detects_parent_cycle() {
        let mut vm = VM::new();
        let a = vm.alloc_segment(Fiber::new(None));
        let b = vm.alloc_segment(Fiber::new(Some(a)));
        vm.segments.get_mut(a).unwrap().parent = Some(b);
        let violations = vm.check_invariants().unwrap_err();
        assert!(violations.iter().any(|v| v.contains("cycle")));
    }

    #[test]
    fn detects_dangling_current_segment() {
        let mut vm = VM::new();
        let a = vm.alloc_segment(Fiber::new(None));
        vm.segments.free(a);
        vm.current_segment = Some(a);
        let violations = vm.check_invariants().unwrap_err();
        assert!(violations.iter().any(|v| v.contains("current_segment")));
    }

    #[test]
    fn detects_duplicate_markers() {
        let mut vm = VM::new();
        let marker = Marker::fresh();
        let make_handler = || Handler {
            marker,
            prompt: None,
            intercept: None,
            mask: Some(crate::segment::MaskSpec {
                masked_effects: vec![],
                behind: false,
            }),
        };
        vm.alloc_segment(Fiber::new_boundary(None, make_handler()));
        vm.alloc_segment(Fiber::new_boundary(None, make_handler()));
        let violations = vm.check_invariants().unwrap_err();
        assert!(violations
            .iter()
            .any(|v| v.contains("installed on multiple live boundary fibers")));
    }

    #[test]
    fn properly_detached_chain_holds_invariants() {
        // Python::attach auto-initializes with the `auto-initialize` feature.
        let mut vm = VM::new();
        let boundary = vm.alloc_segment(Fiber::new(None));
        let body = vm.alloc_segment(Fiber::new(Some(boundary)));
        let chain = vm.segments.detach_chain(body, boundary).unwrap();
        let k = crate::continuation::Continuation::from_chain(chain);
        pyo3::Python::attach(|py| {
            let py_k = pyo3::Py::new(
                py,
                crate::continuation::PyK::from_continuation(k),
            )
            .unwrap();
            vm.pending_handler_k_handle = Some(py_k);
        });
        assert!(vm.check_invariants().is_ok());
    }

    #[test]
    fn detects_detached_fiber_still_live_in_arena() {
        use crate::continuation::{Continuation, DetachedFiber, DetachedFiberChain};
        // Python::attach auto-initializes with the `auto-initialize` feature.
        let mut vm = VM::new();
        // A fiber that is LIVE in the arena...
        let live = vm.alloc_segment(Fiber::new(None));
        // ...and simultaneously claimed by a detached chain — the
        // single-location law violation the checker must catch.
        let fake = DetachedFiberChain::new(
            live,
            live,
            vec![DetachedFiber {
                id: live,
                fiber: Fiber::new(None),
            }],
        );
        let k = Continuation::from_chain(fake);
        pyo3::Python::attach(|py| {
            let py_k = pyo3::Py::new(
                py,
                crate::continuation::PyK::from_continuation(k),
            )
            .unwrap();
            vm.pending_handler_k_handle = Some(py_k);
        });
        let violations = vm.check_invariants().unwrap_err();
        assert!(violations
            .iter()
            .any(|v| v.contains("single-location law")));
    }

    #[test]
    fn var_owner_freed_is_reported_as_tension_not_violation() {
        let mut vm = VM::new();
        let seg = vm.alloc_segment(Fiber::new(None));
        let var = vm.alloc_scoped_var_in_segment(seg, Value::Int(1));
        vm.segments.free(seg);
        // Not fatal: the read path tolerates freed owners via fallback.
        assert!(vm.check_invariants().is_ok());
        // But the tension is visible in the full report.
        let report = vm.invariant_report();
        assert!(report.tensions.iter().any(|v| v.contains("B14-tension")));
        let _ = var;
    }
}
