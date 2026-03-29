"""Tests for uv dependency detection and rewriting."""

import hy  # noqa: F401
import tempfile
from pathlib import Path

from doeff_ml_nexus.uv_deps import (
    find_local_deps,
    rewrite_pyproject_for_container,
    rewrite_uv_lock_for_container,
)


def _make_project(tmp_path: Path, pyproject: str, uv_lock: str = ""):
    (tmp_path / "pyproject.toml").write_text(pyproject)
    if uv_lock:
        (tmp_path / "uv.lock").write_text(uv_lock)
    return tmp_path


class TestFindLocalDeps:
    def test_no_sources(self, tmp_path):
        _make_project(tmp_path, '[project]\nname = "test"\nversion = "0.1.0"')
        assert find_local_deps(tmp_path) == []

    def test_finds_path_deps(self, tmp_path):
        # Create a fake dep dir
        dep_dir = tmp_path / "libs" / "mylib"
        dep_dir.mkdir(parents=True)

        _make_project(tmp_path, '''
[project]
name = "test"
version = "0.1.0"
dependencies = ["mylib"]

[tool.uv.sources]
mylib = { path = "libs/mylib", editable = true }
''')
        deps = find_local_deps(tmp_path)
        assert len(deps) == 1
        assert deps[0].name == "mylib"
        assert deps[0].relative_path == "libs/mylib"

    def test_no_pyproject(self, tmp_path):
        assert find_local_deps(tmp_path) == []

    def test_ignores_non_path_sources(self, tmp_path):
        _make_project(tmp_path, '''
[project]
name = "test"
version = "0.1.0"

[tool.uv.sources]
requests = { git = "https://github.com/psf/requests.git" }
''')
        assert find_local_deps(tmp_path) == []


class TestRewritePyproject:
    def test_rewrites_paths(self, tmp_path):
        dep_dir = tmp_path / "libs" / "mylib"
        dep_dir.mkdir(parents=True)

        _make_project(tmp_path, '''
[tool.uv.sources]
mylib = { path = "libs/mylib", editable = true }
''')
        result = rewrite_pyproject_for_container(tmp_path, "/deps")
        assert 'path = "/deps/mylib"' in result
        assert 'path = "libs/mylib"' not in result

    def test_nested_dep_maps_through_parent(self, tmp_path):
        parent = tmp_path / "libs" / "parent"
        child = parent / "packages" / "child"
        child.mkdir(parents=True)

        _make_project(tmp_path, '''
[tool.uv.sources]
parent = { path = "libs/parent", editable = true }
child = { path = "libs/parent/packages/child", editable = true }
''')
        result = rewrite_pyproject_for_container(tmp_path, "/deps")
        assert 'path = "/deps/parent"' in result
        assert 'path = "/deps/parent/packages/child"' in result


class TestRewriteUvLock:
    def test_rewrites_editable_paths(self, tmp_path):
        dep_dir = tmp_path / "libs" / "mylib"
        dep_dir.mkdir(parents=True)

        _make_project(tmp_path,
            '[tool.uv.sources]\nmylib = { path = "libs/mylib", editable = true }',
            'source = { editable = "./libs/mylib" }\n'
        )
        result = rewrite_uv_lock_for_container(tmp_path, "/deps")
        assert 'editable = "/deps/mylib"' in result

    def test_transitive_deps(self, tmp_path):
        parent = tmp_path / "libs" / "parent"
        (parent / "packages" / "sub").mkdir(parents=True)

        _make_project(tmp_path,
            '[tool.uv.sources]\nparent = { path = "libs/parent", editable = true }',
            '{ name = "sub", editable = "./libs/parent/packages/sub" }\n'
        )
        result = rewrite_uv_lock_for_container(tmp_path, "/deps")
        assert 'editable = "/deps/parent/packages/sub"' in result
