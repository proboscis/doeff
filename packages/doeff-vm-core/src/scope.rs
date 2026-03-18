//! Lexical scope runtime for handlers, interceptors, bindings, and scoped vars.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use crate::do_ctrl::InterceptMode;
use crate::frame::CallMetadata;
use crate::ids::{Marker, ScopeId, VarId};
use crate::kleisli::KleisliRef;
use crate::py_key::HashedPyKey;
use crate::py_shared::PyShared;
use crate::value::Value;

#[derive(Debug, Clone)]
pub enum ScopeBoundary {
    Handler {
        marker: Marker,
        handler: KleisliRef,
        types: Option<Vec<PyShared>>,
    },
    Interceptor {
        marker: Marker,
        interceptor: KleisliRef,
        types: Option<Vec<PyShared>>,
        mode: InterceptMode,
        metadata: Option<CallMetadata>,
    },
}

#[derive(Debug, Clone)]
pub struct Scope {
    pub parent: Option<ScopeId>,
    pub boundary: Option<ScopeBoundary>,
    pub bindings: HashMap<HashedPyKey, Value>,
    pub vars: HashMap<VarId, Value>,
}

impl Scope {
    fn root() -> Self {
        Self {
            parent: None,
            boundary: None,
            bindings: HashMap::new(),
            vars: HashMap::new(),
        }
    }
}

#[derive(Debug)]
pub struct ScopeRuntime {
    scopes: Vec<Scope>,
    var_origins: HashMap<VarId, ScopeId>,
}

impl ScopeRuntime {
    pub fn new() -> Self {
        Self {
            scopes: vec![Scope::root()],
            var_origins: HashMap::new(),
        }
    }

    pub fn get(&self, scope_id: ScopeId) -> Option<&Scope> {
        self.scopes.get(scope_id.index())
    }

    pub fn alloc_scope(
        &mut self,
        parent: ScopeId,
        boundary: Option<ScopeBoundary>,
        bindings: HashMap<HashedPyKey, Value>,
    ) -> ScopeId {
        let id = ScopeId::from_index(self.scopes.len());
        debug_assert!(
            parent.index() < id.index(),
            "scope parent must precede child in arena"
        );
        self.scopes.push(Scope {
            parent: Some(parent),
            boundary,
            bindings,
            vars: HashMap::new(),
        });
        id
    }

    pub fn parent_of(&self, scope_id: ScopeId) -> Option<ScopeId> {
        self.get(scope_id).and_then(|scope| scope.parent)
    }

    fn scope_chain_contains(&self, current_scope: ScopeId, candidate: ScopeId) -> bool {
        let mut cursor = Some(current_scope);
        while let Some(scope_id) = cursor {
            if scope_id == candidate {
                return true;
            }
            cursor = self.parent_of(scope_id);
        }
        false
    }

    pub fn insert_binding(&mut self, scope_id: ScopeId, key: HashedPyKey, value: Value) {
        let scope = self
            .scopes
            .get_mut(scope_id.index())
            .expect("insert_binding: invalid scope_id");
        scope.bindings.insert(key, value);
    }

    pub fn lookup_binding(&self, scope_id: ScopeId, key: &HashedPyKey) -> Option<Value> {
        let mut cursor = Some(scope_id);
        while let Some(current) = cursor {
            let scope = self.get(current)?;
            if let Some(value) = scope.bindings.get(key) {
                return Some(value.clone());
            }
            cursor = scope.parent;
        }
        None
    }

    pub fn alloc_var(&mut self, scope_id: ScopeId, initial_value: Value) -> VarId {
        let scope = self
            .scopes
            .get_mut(scope_id.index())
            .expect("alloc_var: invalid scope_id");
        let var_id = VarId::fresh();
        self.var_origins.insert(var_id, scope_id);
        scope.vars.insert(var_id, initial_value);
        var_id
    }

    pub fn read_var(&self, scope_id: ScopeId, var_id: VarId) -> Option<Value> {
        let mut cursor = Some(scope_id);
        while let Some(current) = cursor {
            let scope = self.get(current)?;
            if let Some(value) = scope.vars.get(&var_id) {
                return Some(value.clone());
            }
            cursor = scope.parent;
        }
        None
    }

    pub fn write_var(
        &mut self,
        scope_id: ScopeId,
        var_id: VarId,
        value: Value,
    ) -> Result<(), &'static str> {
        if self.read_var(scope_id, var_id).is_none() {
            return Err("variable not found in lexical scope");
        }
        let Some(scope) = self.scopes.get_mut(scope_id.index()) else {
            return Err("current scope not found");
        };
        scope.vars.insert(var_id, value);
        Ok(())
    }

    pub fn write_var_nonlocal(
        &mut self,
        current_scope: ScopeId,
        var_id: VarId,
        value: Value,
    ) -> Result<(), &'static str> {
        let Some(origin) = self.var_origins.get(&var_id).copied() else {
            return Err("variable origin not found");
        };
        if !self.scope_chain_contains(current_scope, origin) {
            return Err("variable origin not reachable from current lexical scope");
        }
        let Some(scope) = self.scopes.get_mut(origin.index()) else {
            return Err("variable origin scope not found");
        };
        scope.vars.insert(var_id, value);
        Ok(())
    }

    pub fn chain_boundaries(&self, scope_id: ScopeId) -> Vec<(ScopeId, ScopeBoundary)> {
        let mut entries = Vec::new();
        let mut cursor = Some(scope_id);
        while let Some(current) = cursor {
            let Some(scope) = self.get(current) else {
                break;
            };
            if let Some(boundary) = &scope.boundary {
                entries.push((current, boundary.clone()));
            }
            cursor = scope.parent;
        }
        entries.reverse();
        entries
    }
}

impl Default for ScopeRuntime {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone)]
pub struct ScopeStore {
    runtime: Arc<Mutex<ScopeRuntime>>,
    current_scope: ScopeId,
}

impl ScopeStore {
    pub fn new(runtime: Arc<Mutex<ScopeRuntime>>, current_scope: ScopeId) -> Self {
        Self {
            runtime,
            current_scope,
        }
    }

    pub fn current_scope_id(&self) -> ScopeId {
        self.current_scope
    }

    pub fn lookup_binding(&self, key: &HashedPyKey) -> Option<Value> {
        self.runtime
            .lock()
            .expect("scope lock poisoned")
            .lookup_binding(self.current_scope, key)
    }

    pub fn alloc_child_scope_with_bindings(
        &self,
        bindings: HashMap<HashedPyKey, Value>,
    ) -> ScopeId {
        self.runtime
            .lock()
            .expect("scope lock poisoned")
            .alloc_scope(self.current_scope, None, bindings)
    }
}

impl Default for ScopeStore {
    fn default() -> Self {
        Self {
            runtime: Arc::new(Mutex::new(ScopeRuntime::new())),
            current_scope: ScopeId::root(),
        }
    }
}
