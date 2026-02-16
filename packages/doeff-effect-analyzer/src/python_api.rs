use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::{
    analyze_dotted_path, analyze_symbol as analyze_symbol_core, EffectSummary, EffectTree,
    EffectTreeNode, EffectUsage, NodeKind, Report, SourceSpan,
};

#[pyclass(module = "doeff_effect_analyzer")]
pub struct PyReport {
    inner: Report,
}

#[pymethods]
impl PyReport {
    #[getter]
    pub fn summary(&self) -> PyEffectSummary {
        PyEffectSummary {
            inner: self.inner.summary.clone(),
        }
    }

    #[getter]
    pub fn tree(&self) -> PyEffectTree {
        PyEffectTree {
            inner: self.inner.tree.clone(),
        }
    }

    pub fn to_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new(py);
        dict.set_item("summary", self.summary().to_dict(py)?)?;
        dict.set_item("tree", self.tree().to_dict(py)?)?;
        Ok(dict.into())
    }
}

#[pyclass(module = "doeff_effect_analyzer")]
#[derive(Clone)]
pub struct PyEffectSummary {
    inner: EffectSummary,
}

#[pymethods]
impl PyEffectSummary {
    #[getter]
    pub fn qualified_name(&self) -> &str {
        &self.inner.qualified_name
    }

    #[getter]
    pub fn module(&self) -> &str {
        &self.inner.module
    }

    #[getter]
    pub fn target_kind(&self) -> String {
        self.inner.target_kind.as_str().to_string()
    }

    #[getter]
    pub fn effects(&self) -> Vec<PyEffectUsage> {
        self.inner
            .effects
            .iter()
            .cloned()
            .map(|inner| PyEffectUsage { inner })
            .collect()
    }

    #[getter]
    pub fn warnings(&self) -> Vec<String> {
        self.inner.warnings.clone()
    }

    #[getter]
    pub fn defined_at(&self) -> Option<PySourceSpan> {
        self.inner
            .defined_at
            .clone()
            .map(|inner| PySourceSpan { inner })
    }

    pub fn to_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new(py);
        dict.set_item("qualified_name", &self.inner.qualified_name)?;
        dict.set_item("module", &self.inner.module)?;
        dict.set_item("target_kind", self.inner.target_kind.as_str())?;

        let effect_list = PyList::empty(py);
        for effect in &self.inner.effects {
            effect_list.append(
                PyEffectUsage {
                    inner: effect.clone(),
                }
                .to_dict(py)?,
            )?;
        }
        dict.set_item("effects", effect_list)?;
        dict.set_item("warnings", self.inner.warnings.clone())?;
        match &self.inner.defined_at {
            Some(span) => dict.set_item(
                "defined_at",
                PySourceSpan {
                    inner: span.clone(),
                }
                .to_dict(py)?,
            )?,
            None => dict.set_item("defined_at", py.None())?,
        }
        Ok(dict.into())
    }
}

#[pyclass(module = "doeff_effect_analyzer")]
#[derive(Clone)]
pub struct PyEffectUsage {
    inner: EffectUsage,
}

#[pymethods]
impl PyEffectUsage {
    #[getter]
    pub fn key(&self) -> &str {
        &self.inner.key
    }

    #[getter]
    pub fn span(&self) -> Option<PySourceSpan> {
        self.inner.span.clone().map(|inner| PySourceSpan { inner })
    }

    #[getter]
    pub fn via(&self) -> Option<&str> {
        self.inner.via.as_deref()
    }

    pub fn to_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new(py);
        dict.set_item("key", &self.inner.key)?;
        match &self.inner.span {
            Some(span) => dict.set_item(
                "span",
                PySourceSpan {
                    inner: span.clone(),
                }
                .to_dict(py)?,
            )?,
            None => dict.set_item("span", py.None())?,
        }
        match &self.inner.via {
            Some(via) => dict.set_item("via", via)?,
            None => dict.set_item("via", py.None())?,
        }
        Ok(dict.into())
    }
}

#[pyclass(module = "doeff_effect_analyzer")]
#[derive(Clone)]
pub struct PySourceSpan {
    inner: SourceSpan,
}

#[pymethods]
impl PySourceSpan {
    #[getter]
    pub fn file(&self) -> &str {
        &self.inner.file
    }

    #[getter]
    pub fn line(&self) -> u32 {
        self.inner.line
    }

    #[getter]
    pub fn column(&self) -> u32 {
        self.inner.column
    }

    pub fn to_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new(py);
        dict.set_item("file", &self.inner.file)?;
        dict.set_item("line", self.inner.line)?;
        dict.set_item("column", self.inner.column)?;
        Ok(dict.into())
    }
}

#[pyclass(module = "doeff_effect_analyzer")]
#[derive(Clone)]
pub struct PyEffectTree {
    inner: EffectTree,
}

#[pymethods]
impl PyEffectTree {
    #[getter]
    pub fn root(&self) -> PyEffectTreeNode {
        PyEffectTreeNode {
            inner: self.inner.root.clone(),
        }
    }

    pub fn to_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
        PyEffectTreeNode {
            inner: self.inner.root.clone(),
        }
        .to_dict(py)
    }
}

#[pyclass(module = "doeff_effect_analyzer")]
#[derive(Clone)]
pub struct PyEffectTreeNode {
    inner: EffectTreeNode,
}

#[pymethods]
impl PyEffectTreeNode {
    #[getter]
    pub fn kind(&self) -> String {
        match self.inner.kind {
            NodeKind::Root => "root".to_string(),
            NodeKind::Function => "function".to_string(),
            NodeKind::Effect => "effect".to_string(),
            NodeKind::Unresolved => "unresolved".to_string(),
        }
    }

    #[getter]
    pub fn label(&self) -> &str {
        &self.inner.label
    }

    #[getter]
    pub fn effects(&self) -> Vec<String> {
        self.inner.effects.clone()
    }

    #[getter]
    pub fn span(&self) -> Option<PySourceSpan> {
        self.inner.span.clone().map(|inner| PySourceSpan { inner })
    }

    #[getter]
    pub fn children(&self) -> Vec<PyEffectTreeNode> {
        self.inner
            .children
            .iter()
            .cloned()
            .map(|inner| PyEffectTreeNode { inner })
            .collect()
    }

    pub fn to_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
        let dict = PyDict::new(py);
        dict.set_item("kind", self.kind())?;
        dict.set_item("label", &self.inner.label)?;
        dict.set_item("effects", self.inner.effects.clone())?;
        match &self.inner.span {
            Some(span) => dict.set_item(
                "span",
                PySourceSpan {
                    inner: span.clone(),
                }
                .to_dict(py)?,
            )?,
            None => dict.set_item("span", py.None())?,
        }

        let children = PyList::empty(py);
        for child in &self.inner.children {
            children.append(
                PyEffectTreeNode {
                    inner: child.clone(),
                }
                .to_dict(py)?,
            )?;
        }
        dict.set_item("children", children)?;
        Ok(dict.into())
    }
}

#[pyfunction]
pub fn analyze(dotted_path: &str) -> PyResult<PyReport> {
    let report = analyze_dotted_path(dotted_path)
        .map_err(|err| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(err.to_string()))?;
    Ok(PyReport { inner: report })
}

#[pyfunction]
pub fn analyze_symbol(module: &str, symbol: &str) -> PyResult<PyReport> {
    let report = analyze_symbol_core(module, symbol)
        .map_err(|err| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(err.to_string()))?;
    Ok(PyReport { inner: report })
}

#[pymodule]
pub fn doeff_effect_analyzer(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(analyze, m)?)?;
    m.add_function(wrap_pyfunction!(analyze_symbol, m)?)?;
    m.add_class::<PyReport>()?;
    m.add_class::<PyEffectSummary>()?;
    m.add_class::<PyEffectUsage>()?;
    m.add_class::<PySourceSpan>()?;
    m.add_class::<PyEffectTree>()?;
    m.add_class::<PyEffectTreeNode>()?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
