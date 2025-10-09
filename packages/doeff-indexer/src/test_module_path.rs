#[cfg(test)]
mod tests {
    use crate::indexer::{compute_module_path, find_python_package_root};
    use std::fs;
    use std::path::Path;
    use tempfile::TempDir;

    #[test]
    fn test_compute_module_path_simple_package() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        // Create a simple package structure
        let pkg_dir = root.join("mypackage");
        fs::create_dir(&pkg_dir).unwrap();
        fs::write(pkg_dir.join("__init__.py"), "").unwrap();

        let module_file = pkg_dir.join("module.py");
        fs::write(&module_file, "").unwrap();

        let module_path = compute_module_path(root, &module_file);
        assert_eq!(module_path, "mypackage.module");
    }

    #[test]
    fn test_compute_module_path_nested_package() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        // Create nested package structure
        let pkg_dir = root.join("mypackage");
        let sub_pkg = pkg_dir.join("subpackage");
        fs::create_dir_all(&sub_pkg).unwrap();
        fs::write(pkg_dir.join("__init__.py"), "").unwrap();
        fs::write(sub_pkg.join("__init__.py"), "").unwrap();

        let module_file = sub_pkg.join("module.py");
        fs::write(&module_file, "").unwrap();

        let module_path = compute_module_path(root, &module_file);
        assert_eq!(module_path, "mypackage.subpackage.module");
    }

    #[test]
    fn test_compute_module_path_uv_project() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        // Create UV project structure with pyproject.toml
        let pyproject_content = r#"
[project]
name = "myproject"
version = "0.1.2"
"#;
        fs::write(root.join("pyproject.toml"), pyproject_content).unwrap();

        // Create package matching project name
        let pkg_dir = root.join("myproject");
        fs::create_dir(&pkg_dir).unwrap();
        fs::write(pkg_dir.join("__init__.py"), "").unwrap();

        let module_file = pkg_dir.join("core.py");
        fs::write(&module_file, "").unwrap();

        let module_path = compute_module_path(root, &module_file);
        assert_eq!(module_path, "myproject.core");
    }

    #[test]
    fn test_compute_module_path_init_file() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        // Create package with __init__.py
        let pkg_dir = root.join("mypackage");
        fs::create_dir(&pkg_dir).unwrap();

        let init_file = pkg_dir.join("__init__.py");
        fs::write(&init_file, "").unwrap();

        let module_path = compute_module_path(root, &init_file);
        assert_eq!(module_path, "mypackage");
    }

    #[test]
    fn test_compute_module_path_main_file() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        // Create package with __main__.py
        let pkg_dir = root.join("mypackage");
        fs::create_dir(&pkg_dir).unwrap();
        fs::write(pkg_dir.join("__init__.py"), "").unwrap();

        let main_file = pkg_dir.join("__main__.py");
        fs::write(&main_file, "").unwrap();

        let module_path = compute_module_path(root, &main_file);
        assert_eq!(module_path, "mypackage.__main__");
    }

    #[test]
    fn test_compute_module_path_no_package() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        // Create standalone script (no __init__.py)
        let script_file = root.join("script.py");
        fs::write(&script_file, "").unwrap();

        let module_path = compute_module_path(root, &script_file);
        assert_eq!(module_path, "script");
    }

    #[test]
    fn test_compute_module_path_examples_dir() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        // Create examples directory (common pattern)
        let examples_dir = root.join("examples");
        fs::create_dir(&examples_dir).unwrap();

        let example_file = examples_dir.join("demo.py");
        fs::write(&example_file, "").unwrap();

        let module_path = compute_module_path(root, &example_file);
        assert_eq!(module_path, "examples.demo");
    }

    #[test]
    fn test_compute_module_path_tests_dir() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        // Create tests directory with __init__.py
        let tests_dir = root.join("tests");
        fs::create_dir(&tests_dir).unwrap();
        fs::write(tests_dir.join("__init__.py"), "").unwrap();

        let test_file = tests_dir.join("test_feature.py");
        fs::write(&test_file, "").unwrap();

        let module_path = compute_module_path(root, &test_file);
        assert_eq!(module_path, "tests.test_feature");
    }

    #[test]
    fn test_find_python_package_root_with_init() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        let pkg_dir = root.join("package");
        fs::create_dir(&pkg_dir).unwrap();
        fs::write(pkg_dir.join("__init__.py"), "").unwrap();

        let module_file = pkg_dir.join("module.py");
        fs::write(&module_file, "").unwrap();

        let package_root = find_python_package_root(root, &module_file);
        assert!(package_root.is_some());
    }

    #[test]
    fn test_find_python_package_root_uv_project() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        // Create UV project
        let pyproject_content = r#"
[project]
name = "testproject"
version = "0.1.2"
"#;
        fs::write(root.join("pyproject.toml"), pyproject_content).unwrap();

        let pkg_dir = root.join("testproject");
        fs::create_dir(&pkg_dir).unwrap();
        fs::write(pkg_dir.join("__init__.py"), "").unwrap();

        let module_file = pkg_dir.join("module.py");
        fs::write(&module_file, "").unwrap();

        let package_root = find_python_package_root(root, &module_file);
        assert_eq!(package_root, Some(root.to_path_buf()));
    }

    #[test]
    fn test_module_path_windows_style() {
        // Test that Windows-style paths are converted correctly
        let root = Path::new("/project");
        let file_path = Path::new("/project/mypackage\\subdir\\module.py");

        // This would be converted to use forward slashes
        let module_path = compute_module_path(root, file_path);
        assert!(!module_path.contains('\\'));
        assert!(module_path.contains('.'));
    }

    #[test]
    fn test_compute_module_path_absolute_paths() {
        let temp_dir = TempDir::new().unwrap();
        let root = temp_dir.path();

        // Create package
        let pkg_dir = root.join("package");
        fs::create_dir(&pkg_dir).unwrap();
        fs::write(pkg_dir.join("__init__.py"), "").unwrap();

        let module_file = pkg_dir.join("module.py");
        fs::write(&module_file, "").unwrap();

        // Use absolute paths
        let abs_root = root.canonicalize().unwrap();
        let abs_file = module_file.canonicalize().unwrap();

        let module_path = compute_module_path(&abs_root, &abs_file);
        assert_eq!(module_path, "package.module");
    }
}
