//! Stream abstraction for stepping AST/program sources.

use std::fmt;
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;

use crate::do_ctrl::DoCtrl;
use crate::driver::PyException;
use crate::py_shared::PyShared;
use crate::python_call::PythonCall;
use crate::value::Value;
use crate::vm::RustStore;

pub trait ASTStream: fmt::Debug + Send {
    fn resume(&mut self, value: Value, store: &mut RustStore) -> ASTStreamStep;
    fn throw(&mut self, exc: PyException, store: &mut RustStore) -> ASTStreamStep;
    fn debug_location(&self) -> Option<StreamLocation> {
        None
    }
    fn python_generator(&self) -> Option<PyShared> {
        None
    }
}

pub type ASTStreamRef = Arc<Mutex<Box<dyn ASTStream>>>;

#[derive(Debug)]
pub enum ASTStreamStep {
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
}

impl ASTStream for PythonGeneratorStream {
    fn resume(&mut self, value: Value, _store: &mut RustStore) -> ASTStreamStep {
        if self.started {
            ASTStreamStep::NeedsPython(PythonCall::GenSend { value })
        } else {
            self.started = true;
            ASTStreamStep::NeedsPython(PythonCall::GenNext)
        }
    }

    fn throw(&mut self, exc: PyException, _store: &mut RustStore) -> ASTStreamStep {
        self.started = true;
        ASTStreamStep::NeedsPython(PythonCall::GenThrow { exc })
    }

    fn debug_location(&self) -> Option<StreamLocation> {
        Python::attach(|py| self.resolve_location(py))
    }

    fn python_generator(&self) -> Option<PyShared> {
        Some(self.generator.clone())
    }
}

#[cfg(test)]
mod tests {
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

            let step1 = stream.resume(Value::Unit, &mut store);
            assert!(matches!(
                step1,
                ASTStreamStep::NeedsPython(PythonCall::GenNext)
            ));

            let step2 = stream.resume(Value::Int(7), &mut store);
            assert!(matches!(
                step2,
                ASTStreamStep::NeedsPython(PythonCall::GenSend {
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
            let step = stream.throw(PyException::runtime_error("boom"), &mut store);
            assert!(matches!(
                step,
                ASTStreamStep::NeedsPython(PythonCall::GenThrow { .. })
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
}
