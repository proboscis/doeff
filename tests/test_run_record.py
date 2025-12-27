"""Tests for Run v0 - Reproducible execution records."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from doeff.run_record import (
    CodeState,
    Exec,
    Patch,
    Run,
    ValidationError,
    create_run,
    generate_run_id,
    validate_run,
)


class TestGenerateRunId:
    """Tests for generate_run_id function."""

    def test_generates_valid_format(self) -> None:
        """Generated run_id matches expected format."""
        run_id = generate_run_id()
        assert run_id.startswith("run_")
        assert len(run_id) == 30  # "run_" (4) + 26 ULID chars

    def test_generates_unique_ids(self) -> None:
        """Generated run_ids are unique."""
        ids = {generate_run_id() for _ in range(100)}
        assert len(ids) == 100

    def test_uses_valid_ulid_alphabet(self) -> None:
        """Generated run_id uses valid ULID alphabet (no I, L, O, U)."""
        run_id = generate_run_id()
        ulid_part = run_id[4:]  # Remove "run_" prefix
        for char in ulid_part:
            assert char in "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class TestPatch:
    """Tests for Patch dataclass."""

    def test_valid_patch(self) -> None:
        """Valid patch is created successfully."""
        patch = Patch(
            ref="refs/patches/run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        assert patch.ref == "refs/patches/run_01ARZ3NDEKTSV4RRFFQ69G5FAV"
        assert patch.sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_invalid_ref_raises(self) -> None:
        """Invalid ref raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Patch(
                ref="invalid/ref",
                sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            )
        assert "must start with 'refs/patches/'" in str(exc_info.value)

    def test_invalid_sha256_raises(self) -> None:
        """Invalid sha256 raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Patch(
                ref="refs/patches/run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                sha256="invalid",
            )
        assert "must be 64 hex chars" in str(exc_info.value)

    def test_to_dict(self) -> None:
        """Patch converts to dictionary correctly."""
        patch = Patch(
            ref="refs/patches/run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        d = patch.to_dict()
        assert d == {
            "ref": "refs/patches/run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        }

    def test_from_dict(self) -> None:
        """Patch can be created from dictionary."""
        d = {
            "ref": "refs/patches/run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        }
        patch = Patch.from_dict(d)
        assert patch.ref == d["ref"]
        assert patch.sha256 == d["sha256"]


class TestCodeState:
    """Tests for CodeState dataclass."""

    def test_valid_code_state(self) -> None:
        """Valid code state is created successfully."""
        code_state = CodeState(
            repo_url="git@github.com:org/repo.git",
            base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
        )
        assert code_state.repo_url == "git@github.com:org/repo.git"
        assert code_state.base_commit == "a1b2c3d4e5f6789012345678901234567890abcd"
        assert code_state.patch is None

    def test_code_state_with_patch(self) -> None:
        """Code state with patch is created successfully."""
        patch = Patch(
            ref="refs/patches/run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        code_state = CodeState(
            repo_url="git@github.com:org/repo.git",
            base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
            patch=patch,
        )
        assert code_state.patch is not None
        assert code_state.patch.ref == patch.ref

    def test_invalid_commit_raises(self) -> None:
        """Invalid commit SHA raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CodeState(
                repo_url="git@github.com:org/repo.git",
                base_commit="invalid",
            )
        assert "must be 40 hex chars" in str(exc_info.value)

    def test_empty_repo_url_raises(self) -> None:
        """Empty repo_url raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CodeState(
                repo_url="",
                base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
            )
        assert "cannot be empty" in str(exc_info.value)

    def test_to_dict_without_patch(self) -> None:
        """CodeState without patch converts to dictionary correctly."""
        code_state = CodeState(
            repo_url="git@github.com:org/repo.git",
            base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
        )
        d = code_state.to_dict()
        assert d == {
            "repo_url": "git@github.com:org/repo.git",
            "base_commit": "a1b2c3d4e5f6789012345678901234567890abcd",
        }
        assert "patch" not in d

    def test_to_dict_with_patch(self) -> None:
        """CodeState with patch converts to dictionary correctly."""
        patch = Patch(
            ref="refs/patches/run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        code_state = CodeState(
            repo_url="git@github.com:org/repo.git",
            base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
            patch=patch,
        )
        d = code_state.to_dict()
        assert "patch" in d
        assert d["patch"]["ref"] == patch.ref


class TestExec:
    """Tests for Exec dataclass."""

    def test_valid_exec(self) -> None:
        """Valid exec is created successfully."""
        exec_ = Exec(
            argv=("python", "-m", "mymodule"),
            cwd=".",
            env={"KEY": "value"},
            timeout_sec=3600,
        )
        assert exec_.argv == ("python", "-m", "mymodule")
        assert exec_.cwd == "."
        assert exec_.env == {"KEY": "value"}
        assert exec_.timeout_sec == 3600

    def test_default_values(self) -> None:
        """Exec uses correct default values."""
        exec_ = Exec(argv=("cmd",), cwd=".")
        assert exec_.env == {}
        assert exec_.timeout_sec == 0

    def test_empty_argv_raises(self) -> None:
        """Empty argv raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Exec(argv=(), cwd=".")
        assert "argv cannot be empty" in str(exc_info.value)

    def test_template_variable_raises(self) -> None:
        """Template variable in argv raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Exec(argv=("python", "{module}"), cwd=".")
        assert "contains template variable" in str(exc_info.value)

    def test_negative_timeout_raises(self) -> None:
        """Negative timeout raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Exec(argv=("cmd",), cwd=".", timeout_sec=-1)
        assert "must be >= 0" in str(exc_info.value)

    def test_to_dict(self) -> None:
        """Exec converts to dictionary correctly."""
        exec_ = Exec(
            argv=("python", "-m", "mymodule"),
            cwd="src",
            env={"KEY": "value"},
            timeout_sec=60,
        )
        d = exec_.to_dict()
        assert d == {
            "argv": ["python", "-m", "mymodule"],
            "cwd": "src",
            "env": {"KEY": "value"},
            "timeout_sec": 60,
        }

    def test_from_dict(self) -> None:
        """Exec can be created from dictionary."""
        d = {
            "argv": ["python", "-m", "mymodule"],
            "cwd": "src",
            "env": {"KEY": "value"},
            "timeout_sec": 60,
        }
        exec_ = Exec.from_dict(d)
        assert exec_.argv == ("python", "-m", "mymodule")
        assert exec_.cwd == "src"
        assert exec_.env == {"KEY": "value"}
        assert exec_.timeout_sec == 60


class TestRun:
    """Tests for Run dataclass."""

    def test_valid_run(self) -> None:
        """Valid run is created successfully."""
        run = Run(
            run_version=0,
            run_id="run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            exec=Exec(argv=("python", "-m", "test"), cwd="."),
            code_state=CodeState(
                repo_url="git@github.com:org/repo.git",
                base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
            ),
        )
        assert run.run_version == 0
        assert run.run_id == "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def test_invalid_version_raises(self) -> None:
        """Invalid run_version raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Run(
                run_version=1,
                run_id="run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                exec=Exec(argv=("cmd",), cwd="."),
                code_state=CodeState(
                    repo_url="git@github.com:org/repo.git",
                    base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
                ),
            )
        assert "run_version must be 0" in str(exc_info.value)

    def test_invalid_run_id_raises(self) -> None:
        """Invalid run_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Run(
                run_version=0,
                run_id="invalid",
                exec=Exec(argv=("cmd",), cwd="."),
                code_state=CodeState(
                    repo_url="git@github.com:org/repo.git",
                    base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
                ),
            )
        assert "run_id must match" in str(exc_info.value)

    def test_to_dict(self) -> None:
        """Run converts to dictionary correctly."""
        run = Run(
            run_version=0,
            run_id="run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            exec=Exec(
                argv=("python", "-m", "test"),
                cwd=".",
                env={"KEY": "value"},
                timeout_sec=60,
            ),
            code_state=CodeState(
                repo_url="git@github.com:org/repo.git",
                base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
            ),
        )
        d = run.to_dict()
        assert d["run_version"] == 0
        assert d["run_id"] == "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"
        assert d["exec"]["argv"] == ["python", "-m", "test"]
        assert d["code_state"]["repo_url"] == "git@github.com:org/repo.git"

    def test_from_dict(self) -> None:
        """Run can be created from dictionary."""
        d = {
            "run_version": 0,
            "run_id": "run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "exec": {
                "argv": ["python", "-m", "test"],
                "cwd": ".",
                "env": {"KEY": "value"},
                "timeout_sec": 60,
            },
            "code_state": {
                "repo_url": "git@github.com:org/repo.git",
                "base_commit": "a1b2c3d4e5f6789012345678901234567890abcd",
            },
        }
        run = Run.from_dict(d)
        assert run.run_version == 0
        assert run.run_id == "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"
        assert run.exec.argv == ("python", "-m", "test")

    def test_json_roundtrip(self) -> None:
        """Run can be serialized to JSON and back."""
        run = Run(
            run_version=0,
            run_id="run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            exec=Exec(
                argv=("python", "-m", "test"),
                cwd=".",
                env={"KEY": "value"},
                timeout_sec=60,
            ),
            code_state=CodeState(
                repo_url="git@github.com:org/repo.git",
                base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
            ),
        )
        json_str = run.to_json()
        parsed = Run.from_json(json_str)
        assert parsed.run_id == run.run_id
        assert parsed.exec.argv == run.exec.argv
        assert parsed.code_state.base_commit == run.code_state.base_commit

    def test_immutable(self) -> None:
        """Run dataclass is frozen/immutable."""
        run = Run(
            run_version=0,
            run_id="run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            exec=Exec(argv=("cmd",), cwd="."),
            code_state=CodeState(
                repo_url="git@github.com:org/repo.git",
                base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
            ),
        )
        with pytest.raises(AttributeError):
            run.run_id = "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"  # type: ignore[misc]


class TestCreateRun:
    """Tests for create_run helper function."""

    def test_creates_valid_run(self) -> None:
        """create_run creates a valid Run."""
        run = create_run(
            argv=["python", "-m", "test"],
            cwd=".",
            repo_url="git@github.com:org/repo.git",
            base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
        )
        assert run.run_version == 0
        assert run.run_id.startswith("run_")
        assert run.exec.argv == ("python", "-m", "test")
        assert run.code_state.repo_url == "git@github.com:org/repo.git"

    def test_creates_with_custom_run_id(self) -> None:
        """create_run uses provided run_id."""
        run = create_run(
            argv=["cmd"],
            cwd=".",
            repo_url="git@github.com:org/repo.git",
            base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
            run_id="run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        )
        assert run.run_id == "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"

    def test_creates_with_env(self) -> None:
        """create_run includes environment variables."""
        run = create_run(
            argv=["cmd"],
            cwd=".",
            repo_url="git@github.com:org/repo.git",
            base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
            env={"MY_VAR": "value"},
        )
        assert run.exec.env == {"MY_VAR": "value"}

    def test_creates_with_patch(self) -> None:
        """create_run includes patch."""
        patch = Patch(
            ref="refs/patches/run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        run = create_run(
            argv=["cmd"],
            cwd=".",
            repo_url="git@github.com:org/repo.git",
            base_commit="a1b2c3d4e5f6789012345678901234567890abcd",
            patch=patch,
        )
        assert run.code_state.patch is not None
        assert run.code_state.patch.ref == patch.ref


class TestValidateRun:
    """Tests for validate_run function."""

    def test_valid_run_returns_empty(self) -> None:
        """Valid run data returns empty error list."""
        data = {
            "run_version": 0,
            "run_id": "run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "exec": {
                "argv": ["cmd"],
                "cwd": ".",
                "env": {},
                "timeout_sec": 0,
            },
            "code_state": {
                "repo_url": "git@github.com:org/repo.git",
                "base_commit": "a1b2c3d4e5f6789012345678901234567890abcd",
            },
        }
        errors = validate_run(data)
        assert errors == []

    def test_missing_field_returns_error(self) -> None:
        """Missing required field returns error."""
        data = {
            "run_version": 0,
            "exec": {"argv": ["cmd"], "cwd": "."},
        }
        errors = validate_run(data)
        assert any("run_id" in e for e in errors)

    def test_invalid_version_returns_error(self) -> None:
        """Invalid run_version returns error."""
        data = {
            "run_version": 1,
            "run_id": "run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "exec": {"argv": ["cmd"], "cwd": "."},
            "code_state": {
                "repo_url": "git@github.com:org/repo.git",
                "base_commit": "a1b2c3d4e5f6789012345678901234567890abcd",
            },
        }
        errors = validate_run(data)
        assert any("run_version must be 0" in e for e in errors)

    def test_template_variable_returns_error(self) -> None:
        """Template variable in argv returns error."""
        data = {
            "run_version": 0,
            "run_id": "run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "exec": {"argv": ["{cmd}"], "cwd": "."},
            "code_state": {
                "repo_url": "git@github.com:org/repo.git",
                "base_commit": "a1b2c3d4e5f6789012345678901234567890abcd",
            },
        }
        errors = validate_run(data)
        assert any("template variable" in e for e in errors)


class TestJsonSchemaCompliance:
    """Tests verifying JSON Schema compliance."""

    def test_example_from_spec(self) -> None:
        """Example from the spec is valid."""
        data = {
            "run_version": 0,
            "run_id": "run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "exec": {
                "argv": ["uv", "run", "python", "-m", "plc.runner", "--i", "7"],
                "cwd": ".",
                "env": {
                    "WANDB_DIR": "./outputs",
                    "CUDA_VISIBLE_DEVICES": "0",
                },
                "timeout_sec": 3600,
            },
            "code_state": {
                "repo_url": "git@github.com:org/repo.git",
                "base_commit": "a1b2c3d4e5f6789012345678901234567890abcd",
                "patch": {
                    "ref": "refs/patches/run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
                    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                },
            },
        }
        errors = validate_run(data)
        assert errors == []

        # Also verify it can be parsed
        run = Run.from_dict(data)
        assert run.run_id == "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"
        assert run.code_state.patch is not None
