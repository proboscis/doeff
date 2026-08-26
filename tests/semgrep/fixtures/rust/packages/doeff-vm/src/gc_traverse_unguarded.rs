// Fixture for doeff-vm-traverse-must-null-guard-py-fields.
// Not compiled — no crate references this file.
//
// A `__traverse__` that hands a `Py<T>` field straight to `PyVisit::call`.
// The cycle collector can reach the instance while its Rust contents are still
// zeroed, and the null guard inside `PyVisit::call` is folded away by optimized
// builds because `Py<T>` is `NonNull` (agora-1 pod `ai usage` rc=-11, 2026-08-26).

impl Unguarded {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.value)
    }
}
