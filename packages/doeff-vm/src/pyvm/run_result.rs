use pyo3::prelude::*;
use crate::step::PyException;
use doeff_vm_core::{PyResultErr, PyResultOk};

// ---------------------------------------------------------------------------
// PyRunResult — execution output [R8-J]
// ---------------------------------------------------------------------------

#[pyclass(frozen, name = "DoeffTracebackData")]
pub struct PyDoeffTracebackData {
    #[pyo3(get)]
    pub entries: Py<PyAny>,
    #[pyo3(get)]
    pub active_chain: Py<PyAny>,
}

#[pymethods]
impl PyDoeffTracebackData {
    #[new]
    #[pyo3(signature = (entries, active_chain=None))]
    fn new(py: Python<'_>, entries: Py<PyAny>, active_chain: Option<Py<PyAny>>) -> Self {
        PyDoeffTracebackData {
            entries,
            active_chain: active_chain.unwrap_or_else(|| py.None()),
        }
    }
}

#[pyclass(frozen, name = "RunResult")]
pub struct PyRunResult {
    pub(super) result: Result<Py<PyAny>, PyException>,
    #[pyo3(get)]
    pub(super) traceback_data: Option<Py<PyDoeffTracebackData>>,
    pub(super) raw_store: Py<pyo3::types::PyDict>,
    pub(super) log: Py<PyAny>,
    pub(super) trace: Py<PyAny>,
}

impl PyRunResult {
    fn preview_sequence(seq: &Bound<'_, PyAny>, max_items: usize) -> String {
        let mut lines: Vec<String> = Vec::new();
        if let Ok(iter) = seq.try_iter() {
            for (idx, item_res) in iter.enumerate() {
                if idx >= max_items {
                    lines.push("  ...".to_string());
                    break;
                }
                let text = match item_res {
                    Ok(item) => item
                        .repr()
                        .map(|v| v.to_string())
                        .unwrap_or_else(|_| "<item>".to_string()),
                    Err(_) => "<iter-error>".to_string(),
                };
                lines.push(format!("  {}. {}", idx + 1, text));
            }
            if lines.is_empty() {
                lines.push("  (empty)".to_string());
            }
            return lines.join("\n");
        }
        let fallback = seq
            .repr()
            .map(|v| v.to_string())
            .unwrap_or_else(|_| "<unavailable>".to_string());
        format!("  {}", fallback)
    }

    fn format_traceback_data_preview(
        traceback_data: &Bound<'_, PyDoeffTracebackData>,
        verbose: bool,
    ) -> String {
        let mut lines: Vec<String> = Vec::new();
        let max_items = if verbose { 32 } else { 8 };
        let py = traceback_data.py();
        let traceback_data_ref = traceback_data.borrow();
        let active_chain = traceback_data_ref.active_chain.bind(py);
        let entries = traceback_data_ref.entries.bind(py);

        if !active_chain.is_none() {
            lines.push("ActiveChain:".to_string());
            lines.push(Self::preview_sequence(active_chain, max_items));
        }

        let entry_count = entries.len().ok();
        if verbose {
            lines.push("TraceEntries:".to_string());
            lines.push(Self::preview_sequence(entries, max_items));
        } else if let Some(count) = entry_count {
            lines.push(format!("TraceEntries: {count}"));
        } else {
            lines.push("TraceEntries: <unknown>".to_string());
        }

        if lines.is_empty() {
            return "TracebackData: <unavailable>".to_string();
        }
        lines.join("\n")
    }
}

#[pymethods]
impl PyRunResult {
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.result {
            Ok(v) => Ok(v.clone_ref(py)),
            Err(e) => Err(e.to_pyerr(py)),
        }
    }

    #[getter]
    fn error(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.result {
            Err(e) => Ok(e.value_clone_ref(py)),
            Ok(_) => Err(pyo3::exceptions::PyValueError::new_err(
                "RunResult is Ok, not Err",
            )),
        }
    }

    // D9: Returns Ok(value) or Err(exception) objects per SPEC-008.
    #[getter]
    fn result(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match &self.result {
            Ok(v) => {
                let ok_obj = Bound::new(
                    py,
                    PyResultOk {
                        value: v.clone_ref(py),
                    },
                )?;
                Ok(ok_obj.into_any().unbind())
            }
            Err(e) => {
                let err_obj = e.value_clone_ref(py);
                let err_obj = Bound::new(
                    py,
                    PyResultErr {
                        error: err_obj,
                        captured_traceback: py.None(),
                    },
                )?;
                Ok(err_obj.into_any().unbind())
            }
        }
    }

    #[getter]
    fn raw_store(&self, py: Python<'_>) -> Py<PyAny> {
        self.raw_store.clone_ref(py).into_any()
    }

    #[getter]
    fn log(&self, py: Python<'_>) -> Py<PyAny> {
        self.log.clone_ref(py)
    }

    #[getter]
    fn trace(&self, py: Python<'_>) -> Py<PyAny> {
        self.trace.clone_ref(py)
    }

    fn is_ok(&self) -> bool {
        self.result.is_ok()
    }

    fn is_err(&self) -> bool {
        self.result.is_err()
    }

    #[pyo3(signature = (verbose=false))]
    fn display(&self, py: Python<'_>, verbose: bool) -> PyResult<String> {
        if let Err(err) = &self.result {
            let err_obj = err.value_clone_ref(py);
            let label = if verbose { "verbose" } else { "default" };
            let mut lines = vec![
                format!("RunResult status: err ({label})"),
                format!("Error: {:?}", err_obj),
            ];
            if let Some(traceback_data) = &self.traceback_data {
                lines.push(Self::format_traceback_data_preview(
                    traceback_data.bind(py),
                    verbose,
                ));
            } else {
                lines.push("TracebackData: none".to_string());
            }
            return Ok(lines.join("\n"));
        }

        let value_text = match &self.result {
            Ok(value) => value
                .bind(py)
                .repr()
                .map(|v| v.to_string())
                .unwrap_or_else(|_| "<value>".to_string()),
            Err(_) => "<error>".to_string(),
        };
        Ok(format!("RunResult status: ok\nValue: {value_text}"))
    }
}
