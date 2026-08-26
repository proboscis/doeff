// Fixture for doeff-vm-traverse-must-null-guard-py-fields, nested-directory arm.
// Not compiled — no crate references this file.
//
// doeff-vm-core keeps most of the VM under src/vm/, so a `paths.include` that
// only reaches the crate's top-level src/*.rs would let this file through
// without a finding while reading as if the crate were covered.

impl UnguardedNested {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.value)
    }
}
