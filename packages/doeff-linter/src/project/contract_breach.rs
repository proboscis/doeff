//! Kinds of DOEFF137 (handler edge-test) hits. Read by the explanation (explain.rs) and the judgement (mod.rs) — kept in its own module so
//! it need not read back from mod.rs.

/// Kinds of DOEFF137 hits.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContractTestBreach {
    /// A handler that requires an edge test has no edge test that reaches it.
    NoEdgeTest,
    /// A `:contract-test none` without a reason.
    NoneWithoutReason,
}
