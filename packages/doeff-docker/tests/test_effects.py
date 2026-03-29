"""Tests for doeff-docker effects and Dockerfile collection."""

import hy  # noqa: F401
import pytest
from doeff import run, WithHandler, Pure, do
from doeff_core_effects import reader, writer, slog_handler

from doeff_docker.effects import (
    From, Run, Copy, Workdir, SetEnv, Expose,
    DockerBuild, DockerRun, ImagePush,
)
from doeff_docker.handlers.dockerfile import (
    collect_dockerfile,
    dockerfile_collector_handler,
    render_dockerfile,
)


def _run_with_handlers(program):
    return run(
        WithHandler(writer(),
        WithHandler(slog_handler(),
        WithHandler(reader(env={}),
            program)))
    )


class TestDockerfileEffects:
    def test_from_is_frozen(self):
        e = From(image="python:3.13")
        assert e.image == "python:3.13"
        with pytest.raises(AttributeError):
            e.image = "other"

    def test_run_effect(self):
        e = Run(command="apt-get update")
        assert e.command == "apt-get update"

    def test_copy_effect(self):
        e = Copy(src=".", dst="/app/")
        assert e.src == "."
        assert e.dst == "/app/"

    def test_setenv_effect(self):
        e = SetEnv(key="PATH", value="/usr/bin")
        assert e.key == "PATH"

    def test_docker_build_defaults(self):
        from pathlib import Path
        e = DockerBuild(dockerfile="FROM x", tag="t:1", context_path=Path("/ctx"))
        assert e.host == "localhost"

    def test_docker_run_defaults(self):
        e = DockerRun(image="img:1", program=Pure(1))
        assert e.host == "localhost"
        assert e.gpu is False
        assert e.mounts == ()
        assert e.env_vars == ()


class TestCollectDockerfile:
    def test_simple_dockerfile(self):
        @do
        def image():
            yield From(image="python:3.13-slim")
            yield Run(command="echo hello")
            yield Copy(src=".", dst="/app/")

        @do
        def test():
            return (yield collect_dockerfile(image()))

        result = _run_with_handlers(test())
        lines = result.strip().split("\n")
        assert lines[0] == "FROM python:3.13-slim"
        assert lines[1] == "RUN echo hello"
        assert lines[2] == "COPY . /app/"

    def test_with_env_and_workdir(self):
        @do
        def image():
            yield From(image="ubuntu:22.04")
            yield SetEnv(key="FOO", value="bar")
            yield Workdir(path="/opt")
            yield Expose(port=8080)

        @do
        def test():
            return (yield collect_dockerfile(image()))

        result = _run_with_handlers(test())
        assert "ENV FOO=bar" in result
        assert "WORKDIR /opt" in result
        assert "EXPOSE 8080" in result

    def test_conditional_instructions(self):
        """Dockerfile = Program means conditionals work naturally."""
        @do
        def image(gpu: bool):
            yield From(image="python:3.13")
            if gpu:
                yield SetEnv(key="NVIDIA_VISIBLE_DEVICES", value="all")
            yield Run(command="echo done")

        @do
        def test():
            df_cpu = yield collect_dockerfile(image(False))
            df_gpu = yield collect_dockerfile(image(True))
            return (df_cpu, df_gpu)

        cpu, gpu = _run_with_handlers(test())
        assert "NVIDIA" not in cpu
        assert "NVIDIA_VISIBLE_DEVICES=all" in gpu

    def test_empty_dockerfile(self):
        @do
        def image():
            return None  # no instructions

        @do
        def test():
            return (yield collect_dockerfile(image()))

        result = _run_with_handlers(test())
        assert result == ""
