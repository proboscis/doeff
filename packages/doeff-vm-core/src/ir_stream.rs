//! Stream abstraction for stepping AST/program sources.

use std::fmt;
use std::ops::Deref;
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;

use crate::do_ctrl::DoCtrl;
use crate::driver::PyException;
use crate::memory_stats;
use crate::py_shared::PyShared;
use crate::python_call::PythonCall;
use crate::segment::ScopeStore;
use crate::value::Value;
use crate::vm::RustStore;

pub trait IRStream: fmt::Debug + Send {
    fn resume(
        &mut self,
        value: Value,
        store: &mut RustStore,
        scope: &mut ScopeStore,
    ) -> IRStreamStep;
    fn throw(
        &mut self,
        exc: PyException,
        store: &mut RustStore,
        scope: &mut ScopeStore,
    ) -> IRStreamStep;
    fn debug_location(&self) -> Option<StreamLocation> {
        None
    }
    fn python_generator(&self) -> Option<PyShared> {
        None
    }
    fn is_tail_resume_return(&self) -> bool {
        false
    }
}

#[derive(Debug)]
struct TrackedIRStream {
    stream: Mutex<Box<dyn IRStream>>,
}

impl Drop for TrackedIRStream {
    fn drop(&mut self) {
        memory_stats::unregister_ir_stream();
    }
}

#[derive(Debug, Clone)]
pub struct IRStreamRef(Arc<TrackedIRStream>);

impl IRStreamRef {
    pub fn new(stream: Box<dyn IRStream>) -> Self {
        memory_stats::register_ir_stream();
        IRStreamRef(Arc::new(TrackedIRStream {
            stream: Mutex::new(stream),
        }))
    }

    pub fn ptr_eq(lhs: &Self, rhs: &Self) -> bool {
        Arc::ptr_eq(&lhs.0, &rhs.0)
    }

    pub fn strong_count(&self) -> usize {
        Arc::strong_count(&self.0)
    }
}

impl Deref for IRStreamRef {
    type Target = Mutex<Box<dyn IRStream>>;

    fn deref(&self) -> &Self::Target {
        &self.0.stream
    }
}

#[derive(Debug)]
pub enum IRStreamStep {
    Yield(DoCtrl),
    Return(Value),
    Throw(PyException),
    NeedsPython(PythonCall),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamLocation {
    pub function_name: String,
    pub source_file: String,
    pub source_line: u32,
    pub phase: Option<String>,
}

pub struct PythonGeneratorStream {
    generator: PyShared,
    get_frame: PyShared,
    started: bool,
}

impl fmt::Debug for PythonGeneratorStream {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("PythonGeneratorStream")
            .field("started", &self.started)
            .finish()
    }
}

impl PythonGeneratorStream {
    pub fn new(generator: PyShared, get_frame: PyShared) -> Self {
        PythonGeneratorStream {
            generator,
            get_frame,
            started: false,
        }
    }

    fn resolve_location(&self, py: Python<'_>) -> Option<StreamLocation> {
        let frame = self
            .get_frame
            .bind(py)
            .call1((self.generator.bind(py),))
            .ok()?;
        if frame.is_none() {
            return None;
        }

        let code = frame.getattr("f_code").ok()?;
        let function_name = code.getattr("co_name").ok()?.extract::<String>().ok()?;
        let source_file = code.getattr("co_filename").ok()?.extract::<String>().ok()?;
        let source_line = frame.getattr("f_lineno").ok()?.extract::<u32>().ok()?;

        Some(StreamLocation {
            function_name,
            source_file,
            source_line,
            phase: None,
        })
    }

    fn is_tail_resume_return_inner(&self, py: Python<'_>) -> Option<bool> {
        let frame = self
            .get_frame
            .bind(py)
            .call1((self.generator.bind(py),))
            .ok()?;
        if frame.is_none() {
            return None;
        }
        let lasti = frame.getattr("f_lasti").ok()?.extract::<usize>().ok()?;
        let code = frame.getattr("f_code").ok()?;
        let dis = py.import("dis").ok()?;
        let instructions = dis.call_method1("get_instructions", (code,)).ok()?;
        let mut saw_resume = false;
        for instruction in instructions.try_iter().ok()? {
            let instruction = instruction.ok()?;
            let offset = instruction
                .getattr("offset")
                .ok()?
                .extract::<usize>()
                .ok()?;
            if saw_resume {
                let opname = instruction
                    .getattr("opname")
                    .ok()?
                    .extract::<String>()
                    .ok()?;
                return Some(opname == "RETURN_VALUE");
            }
            if offset == lasti {
                saw_resume = true;
            }
        }
        None
    }
}

impl IRStream for PythonGeneratorStream {
    fn resume(
        &mut self,
        value: Value,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        if self.started {
            IRStreamStep::NeedsPython(PythonCall::GenSend { value })
        } else {
            self.started = true;
            IRStreamStep::NeedsPython(PythonCall::GenNext)
        }
    }

    fn throw(
        &mut self,
        exc: PyException,
        _store: &mut RustStore,
        _scope: &mut ScopeStore,
    ) -> IRStreamStep {
        self.started = true;
        IRStreamStep::NeedsPython(PythonCall::GenThrow { exc })
    }

    fn debug_location(&self) -> Option<StreamLocation> {
        Python::attach(|py| self.resolve_location(py))
    }

    fn python_generator(&self) -> Option<PyShared> {
        Some(self.generator.clone())
    }

    fn is_tail_resume_return(&self) -> bool {
        Python::attach(|py| self.is_tail_resume_return_inner(py).unwrap_or(false))
    }
}

#[cfg(test)]
mod tests {
    use crate::memory_stats::live_object_counts;
    use pyo3::types::PyDict;

    use super::*;

    #[test]
    fn test_python_generator_stream_resume_sequence() {
        Python::attach(|py| {
            let locals = PyDict::new(py);
            py.run(
                c"def _gen():\n    yield 1\n\ngen = _gen()\n\ndef _get_frame(g):\n    return g.gi_frame\n",
                Some(&locals),
                Some(&locals),
            )
            .expect("failed to define generator test fixtures");

            let generator = locals
                .get_item("gen")
                .expect("locals.get_item failed")
                .expect("gen missing")
                .unbind();
            let get_frame = locals
                .get_item("_get_frame")
                .expect("locals.get_item failed")
                .expect("_get_frame missing")
                .unbind();

            let mut stream =
                PythonGeneratorStream::new(PyShared::new(generator), PyShared::new(get_frame));
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();

            let step1 = stream.resume(Value::Unit, &mut store, &mut scope);
            assert!(matches!(
                step1,
                IRStreamStep::NeedsPython(PythonCall::GenNext)
            ));

            let step2 = stream.resume(Value::Int(7), &mut store, &mut scope);
            assert!(matches!(
                step2,
                IRStreamStep::NeedsPython(PythonCall::GenSend {
                    value: Value::Int(7)
                })
            ));
        });
    }

    #[test]
    fn test_python_generator_stream_throw_uses_gen_throw() {
        Python::attach(|py| {
            let locals = PyDict::new(py);
            py.run(
                c"def _gen():\n    yield 1\n\ngen = _gen()\n\ndef _get_frame(g):\n    return g.gi_frame\n",
                Some(&locals),
                Some(&locals),
            )
            .expect("failed to define generator test fixtures");

            let generator = locals
                .get_item("gen")
                .expect("locals.get_item failed")
                .expect("gen missing")
                .unbind();
            let get_frame = locals
                .get_item("_get_frame")
                .expect("locals.get_item failed")
                .expect("_get_frame missing")
                .unbind();

            let mut stream =
                PythonGeneratorStream::new(PyShared::new(generator), PyShared::new(get_frame));
            let mut store = RustStore::new();
            let mut scope = ScopeStore::default();
            let step = stream.throw(PyException::runtime_error("boom"), &mut store, &mut scope);
            assert!(matches!(
                step,
                IRStreamStep::NeedsPython(PythonCall::GenThrow { .. })
            ));
        });
    }

    #[test]
    fn test_python_generator_stream_debug_location_uses_get_frame_callback() {
        Python::attach(|py| {
            let locals = PyDict::new(py);
            py.run(
                c"def _gen():\n    yield 1\n\ngen = _gen()\nnext(gen)\n\ndef _get_frame(g):\n    return g.gi_frame\n",
                Some(&locals),
                Some(&locals),
            )
            .expect("failed to define generator test fixtures");

            let generator = locals
                .get_item("gen")
                .expect("locals.get_item failed")
                .expect("gen missing")
                .unbind();
            let get_frame = locals
                .get_item("_get_frame")
                .expect("locals.get_item failed")
                .expect("_get_frame missing")
                .unbind();
            let stream =
                PythonGeneratorStream::new(PyShared::new(generator), PyShared::new(get_frame));

            let location = stream
                .debug_location()
                .expect("expected location from get_frame callback");
            assert_eq!(location.function_name, "_gen");
            assert!(!location.source_file.is_empty());
            assert!(location.source_line > 0);
            assert_eq!(location.phase, None);
        });
    }

    #[test]
    fn test_ir_stream_live_count_tracks_underlying_stream() {
        #[derive(Debug)]
        struct DummyStream;

        impl IRStream for DummyStream {
            fn resume(
                &mut self,
                _value: Value,
                _store: &mut RustStore,
                _scope: &mut ScopeStore,
            ) -> IRStreamStep {
                IRStreamStep::Return(Value::Unit)
            }

            fn throw(
                &mut self,
                exc: PyException,
                _store: &mut RustStore,
                _scope: &mut ScopeStore,
            ) -> IRStreamStep {
                IRStreamStep::Throw(exc)
            }
        }

        let baseline = live_object_counts().live_ir_streams;
        let stream = IRStreamRef::new(Box::new(DummyStream));
        assert_eq!(live_object_counts().live_ir_streams, baseline + 1);

        let stream_clone = stream.clone();
        assert_eq!(live_object_counts().live_ir_streams, baseline + 1);
        assert_eq!(stream_clone.strong_count(), 2);

        drop(stream_clone);
        assert_eq!(live_object_counts().live_ir_streams, baseline + 1);

        drop(stream);
        assert_eq!(live_object_counts().live_ir_streams, baseline);
    }
}
