use std::collections::HashMap;
use std::sync::Arc;

use crate::py_key::HashedPyKey;
use crate::value::Value;

/// Per-segment scope state used by Local/Ask resolution.
#[derive(Debug, Clone, Default)]
pub struct ScopeStore {
    pub scope_bindings: Vec<Arc<HashMap<HashedPyKey, Value>>>,
}
