use std::path::PathBuf;
use std::sync::Once;
use std::time::Duration;

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use doeff_vm::pyvm::doeff_vm;
use pyo3::append_to_inittab;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

#[derive(Clone)]
struct BenchmarkCase {
    name: String,
    runner: String,
    workload: String,
    invoke: Py<PyAny>,
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("repo root should resolve")
}

fn venv_site_packages() -> PathBuf {
    std::env::var_os("DOEFF_BENCH_SITE_PACKAGES")
        .map(PathBuf::from)
        .expect("DOEFF_BENCH_SITE_PACKAGES should be set")
        .canonicalize()
        .expect("venv site-packages should resolve")
}

fn initialize_python() {
    static INIT: Once = Once::new();
    INIT.call_once(|| {
        append_to_inittab!(doeff_vm);
        Python::initialize();
    });
}

fn load_cases(iterations: usize) -> PyResult<Vec<BenchmarkCase>> {
    initialize_python();
    Python::attach(|py| {
        let sys = py.import("sys")?;
        let sys_path_obj = sys.getattr("path")?;
        let sys_path = sys_path_obj.cast::<PyList>()?;
        let repo_root = repo_root();
        let repo_root_str = repo_root.to_string_lossy().to_string();
        let site_packages = venv_site_packages();
        let site_packages_str = site_packages.to_string_lossy().to_string();
        sys_path.insert(0, repo_root_str.as_str())?;
        sys_path.insert(0, site_packages_str.as_str())?;

        let modules_obj = sys.getattr("modules")?;
        let modules = modules_obj.cast::<PyDict>()?;
        let doeff_vm_module = py.import("doeff_vm")?;
        doeff_vm_module.setattr("doeff_vm", doeff_vm_module.clone())?;
        modules.set_item("doeff_vm.doeff_vm", doeff_vm_module)?;

        let workload_module = py.import("benchmarks.pyvm_workloads")?;
        let cases = workload_module.call_method1("build_raw_vm_benchmark_cases", (iterations,))?;

        let mut loaded = Vec::new();
        for item in cases.try_iter()? {
            let item = item?;
            loaded.push(BenchmarkCase {
                name: item.getattr("name")?.extract()?,
                runner: item.getattr("runner")?.extract()?,
                workload: item.getattr("workload")?.extract()?,
                invoke: item.getattr("invoke")?.unbind(),
            });
        }
        Ok(loaded)
    })
}

fn benchmark_pyvm_baseline(c: &mut Criterion) {
    let cases = load_cases(25).expect("criterion workload cases should load");
    let mut group = c.benchmark_group("doeff_vm_baseline");
    group.warm_up_time(Duration::from_millis(500));
    group.measurement_time(Duration::from_secs(3));
    group.sample_size(20);

    for case in cases {
        let label = BenchmarkId::new(case.runner.clone(), case.workload.clone());
        let invoke = case.invoke;
        let name = case.name.clone();
        group.bench_function(label, move |b| {
            b.iter(|| {
                Python::attach(|py| {
                    let result = invoke
                        .bind(py)
                        .call0()
                        .unwrap_or_else(|err| panic!("{name} failed: {err}"));
                    black_box(result);
                });
            });
        });
    }

    group.finish();
}

criterion_group!(benches, benchmark_pyvm_baseline);
criterion_main!(benches);
