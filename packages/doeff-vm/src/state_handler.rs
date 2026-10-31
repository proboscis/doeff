//! The state handler as a Rust handler: answers `Get(key)` and `Put(key, value)`
//! from its store inside the VM, without calling a Python generator per effect.
//!
//! `doeff_core_effects.handlers.state()` makes one `StateHandler` per call and
//! installs it like any handler. The store stays a Python dict, so the handler
//! answers exactly what the Python `@do` handler answered: a missing key reads
//! `None` (SPEC-EFF-002 D1), `Put` resumes with `None`, and an effect of
//! another type passes on as the annotation `effect: Get | Put` made it pass.
//! The state is not held anywhere else: a handler inside it still answers the
//! `Get` of a key before the state handler sees it (ADR-DOE-HY-006).
//!
//! The Python object is the handler itself: `GetHandlers` / boundary capture
//! answer this same object, and `WithHandler` takes it back (`wrap_handler`).

use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::pyclass::{PyTraverseError, PyVisit};
use pyo3::types::{PyDict, PyTuple, PyType};

use doeff_vm_core::do_ctrl::DoCtrl;
use doeff_vm_core::py_shared::PyShared;
use doeff_vm_core::value::{Callable, CallableRef};
use doeff_vm_core::{VMError, Value};

use crate::gc::visit_py_field;
use crate::python_generator_stream::{python_to_value, raised_object};

/// The name the Python `@do` handler had: the handler chain of a traceback and
/// the UnhandledEffect message show the state handler as before.
const QUALNAME: &str = "state.<locals>.handler";

#[pyclass(name = "StateHandler", module = "doeff_vm.doeff_vm", frozen)]
pub struct PyStateHandler {
    store: Py<PyDict>,
    get_type: Py<PyType>,
    put_type: Py<PyType>,
    /// `(Get, Put)`: what `handler_effect_types` answers for this handler.
    effect_types: Py<PyTuple>,
}

impl PyStateHandler {
    /// The VM-side callable of this handler object.
    pub(crate) fn callable(handler: &Bound<'_, PyStateHandler>) -> CallableRef {
        std::sync::Arc::new(StateCallable {
            handler: handler.clone().unbind(),
        })
    }

    /// The handler object a VM-side callable wraps, when it is a state handler.
    pub(crate) fn of<'py>(py: Python<'py>, callable: &CallableRef) -> Option<Bound<'py, PyAny>> {
        callable
            .as_any()
            .downcast_ref::<StateCallable>()
            .map(|state| state.handler.bind(py).clone().into_any())
    }

    /// The effect types of the state handler a VM-side callable wraps.
    pub(crate) fn effect_types_of(py: Python<'_>, callable: &CallableRef) -> Option<Py<PyTuple>> {
        callable
            .as_any()
            .downcast_ref::<StateCallable>()
            .map(|state| state.handler.get().effect_types.clone_ref(py))
    }
}

#[pymethods]
impl PyStateHandler {
    /// `StateHandler(store, Get, Put)`: answers `Get` / `Put` (instances of the two
    /// classes) from `store`, which it keeps (not a copy).
    #[new]
    fn new(
        py: Python<'_>,
        store: Bound<'_, PyDict>,
        get_type: Bound<'_, PyType>,
        put_type: Bound<'_, PyType>,
    ) -> PyResult<Self> {
        let effect_types = PyTuple::new(py, [&get_type, &put_type])?.unbind();
        Ok(Self {
            store: store.unbind(),
            get_type: get_type.unbind(),
            put_type: put_type.unbind(),
            effect_types,
        })
    }

    #[getter]
    fn effect_types(&self, py: Python<'_>) -> Py<PyTuple> {
        self.effect_types.clone_ref(py)
    }

    #[getter(__qualname__)]
    fn qualname(&self) -> &'static str {
        QUALNAME
    }

    #[getter(__name__)]
    fn name(&self) -> &'static str {
        "handler"
    }

    #[pyo3(signature = (*_args, **_kwargs))]
    fn __call__(
        &self,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<()> {
        Err(PyTypeError::new_err(
            "the state handler is dispatched by the VM; install it with WithHandler",
        ))
    }

    fn __repr__(&self) -> &'static str {
        "<state handler>"
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit_py_field(&visit, &self.store)?;
        visit_py_field(&visit, &self.get_type)?;
        visit_py_field(&visit, &self.put_type)?;
        visit_py_field(&visit, &self.effect_types)?;
        Ok(())
    }
}

/// The VM-side callable: holds the handler object, so `GetHandlers` answers the
/// object `state()` made, not a copy.
#[derive(Debug)]
struct StateCallable {
    handler: Py<PyStateHandler>,
}

enum Kind {
    Get,
    Put,
}

impl StateCallable {
    fn kind(&self, py: Python<'_>, effect: &Bound<'_, PyAny>) -> Option<Kind> {
        let state = self.handler.get();
        let effect_type = effect.get_type();
        if effect_type.is(state.get_type.bind(py)) {
            return Some(Kind::Get);
        }
        if effect_type.is(state.put_type.bind(py)) {
            return Some(Kind::Put);
        }
        // A subclass of Get / Put: isinstance, as the annotation `Get | Put` filtered.
        // An error (a broken __instancecheck__) is not an instance.
        if effect.is_instance(state.get_type.bind(py)).unwrap_or(false) {
            return Some(Kind::Get);
        }
        if effect.is_instance(state.put_type.bind(py)).unwrap_or(false) {
            return Some(Kind::Put);
        }
        None
    }

    fn answer(&self, py: Python<'_>, kind: Kind, effect: &Bound<'_, PyAny>) -> PyResult<Value> {
        let store = self.handler.get().store.bind(py);
        let key = effect.getattr(pyo3::intern!(py, "key"))?;
        match kind {
            Kind::Get => {
                let value = store
                    .get_item(&key)?
                    .unwrap_or_else(|| py.None().into_bound(py));
                Ok(python_to_value(py, &value))
            }
            Kind::Put => {
                let value = effect.getattr(pyo3::intern!(py, "value"))?;
                store.set_item(key, value)?;
                Ok(Value::Opaque(PyShared::new(py.None())))
            }
        }
    }
}

impl Callable for StateCallable {
    fn call(&self, _args: Vec<Value>) -> Result<Value, VMError> {
        Err(VMError::type_error(
            "the state handler is dispatched by the VM, not called",
        ))
    }

    fn call_handler(&self, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        let mut args = args.into_iter();
        let effect = args
            .next()
            .ok_or_else(|| VMError::internal("state: missing effect"))?;
        let Some(Value::Continuation(k)) = args.next() else {
            return Err(VMError::internal("state: expected a continuation"));
        };
        let Value::Opaque(obj) = effect else {
            return Ok(DoCtrl::Pass { effect, k });
        };
        Python::attach(|py| {
            let bound = obj.bind(py);
            let Some(kind) = self.kind(py, bound) else {
                return Ok(DoCtrl::Pass {
                    effect: Value::Opaque(obj),
                    k,
                });
            };
            // Resume, not Transfer: a synchronous handler has no stream frame of its own,
            // and Transfer pops the top frame first — here a frame of the handler's parent
            // (the task wrapper of a spawned task). Resuming from the parent is what the
            // Python handler's Transfer did after popping its own frame, as the scheduler
            // prompt answers.
            match self.answer(py, kind, bound) {
                Ok(value) => Ok(DoCtrl::Resume { k, value }),
                // An unhashable key, a raising __hash__ / __eq__: raised at the yield of the
                // Get / Put, where the VM threw the Python handler's exception.
                Err(err) => Ok(DoCtrl::ResumeThrow {
                    k,
                    exception: Value::Opaque(PyShared::new(raised_object(py, &err))),
                }),
            }
        })
    }

    fn name(&self) -> Option<String> {
        Some(QUALNAME.to_string())
    }

    fn accepts(&self, effect: &Value) -> bool {
        let Value::Opaque(obj) = effect else {
            return false;
        };
        Python::attach(|py| self.kind(py, obj.bind(py)).is_some())
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyStateHandler>()?;
    Ok(())
}
