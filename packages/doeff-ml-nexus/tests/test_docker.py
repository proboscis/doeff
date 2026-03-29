"""Tests for uv-image Dockerfile generation."""

import hy  # noqa: F401
from pathlib import Path

from doeff import run, WithHandler, do
from doeff_core_effects import reader, writer, slog_handler

from doeff_docker.handlers.dockerfile import collect_dockerfile
from doeff_ml_nexus.docker import uv_image, uv_gpu_image


def _run_with_handlers(program):
    return run(
        WithHandler(writer(),
        WithHandler(slog_handler(),
        WithHandler(reader(env={}),
            program)))
    )


class TestUvImage:
    def test_basic_uv_image(self):
        @do
        def test():
            return (yield collect_dockerfile(uv_image("python:3.13-slim")))

        result = _run_with_handlers(test())
        lines = result.split("\n")
        assert lines[0] == "FROM python:3.13-slim"
        assert any("uv" in l for l in lines)
        assert any("uv sync --frozen --no-install-project" in l for l in lines)
        assert any("uv sync --frozen" in l for l in lines)

    def test_gpu_image_has_nvidia_env(self):
        @do
        def test():
            return (yield collect_dockerfile(uv_gpu_image("nvidia/cuda:12.4")))

        result = _run_with_handlers(test())
        assert "NVIDIA_VISIBLE_DEVICES=all" in result
        assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility" in result

    def test_with_local_deps(self, tmp_path):
        # Create a fake project with local dep
        dep_dir = tmp_path / "libs" / "mylib"
        dep_dir.mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text('''
[project]
name = "test"
version = "0.1.0"
dependencies = ["mylib"]

[tool.uv.sources]
mylib = { path = "libs/mylib", editable = true }
''')

        @do
        def test():
            return (yield collect_dockerfile(uv_image("python:3.13", project_root=tmp_path)))

        result = _run_with_handlers(test())
        assert "COPY deps/mylib /deps/mylib" in result

    def test_rust_extension_detected(self, tmp_path):
        dep_dir = tmp_path / "libs" / "rustlib"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Cargo.toml").write_text("[package]\nname = 'rustlib'")
        (tmp_path / "pyproject.toml").write_text('''
[project]
name = "test"
version = "0.1.0"

[tool.uv.sources]
rustlib = { path = "libs/rustlib", editable = true }
''')

        @do
        def test():
            return (yield collect_dockerfile(uv_image("python:3.13", project_root=tmp_path)))

        result = _run_with_handlers(test())
        assert "rustup" in result  # Rust toolchain installed
