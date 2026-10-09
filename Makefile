# Doeff Development Makefile
# ===========================
# Centralized commands for development, testing, and linting.

.PHONY: help install sync lint lint-ruff lint-pyright lint-semgrep lint-semgrep-docs lint-doeff lint-packages \
        test test-unit test-e2e test-changed test-packages print-package-extra-test-roots test-rust test-all test-spec-audit-sa002 bench-smoke format check check-repo-hygiene \
        pre-commit-install hooks-install enforcement-ledger clean install-opencode-spec-gap-tdd

# Default target
help:
	@echo "Doeff Development Commands"
	@echo "=========================="
	@echo ""
	@echo "Setup:"
	@echo "  make install           Install all dependencies (including dev)"
	@echo "  make sync              Install deps + rebuild Rust VM extension"
	@echo "  make pre-commit-install Install pre-commit hooks"
	@echo "  make hooks-install     Install raw git pre-commit hook (enforcement ledger, ADR-DOE-ENFORCE-001 R7)"
	@echo ""
	@echo "Linting (make lint runs all):"
	@echo "  make lint              Run ALL linters (core + packages)"
	@echo "  make lint-ruff         Run ruff linter"
	@echo "  make lint-pyright      Run pyright type checker"
	@echo "  make lint-semgrep      Run semgrep architectural rules"
	@echo "  make lint-semgrep-docs Check docs for deprecated API patterns"
	@echo "  make lint-doeff        Run doeff-linter (Rust-based)"
	@echo "  make lint-packages     Run lint in all subpackages with Makefiles"
	@echo ""
	@echo "Testing:"
	@echo "  make test              Run core tests"
	@echo "  make test-unit         Run unit tests only (exclude e2e)"
	@echo "  make test-e2e          Run e2e tests only"
	@echo "  make test-changed      Run contract tests + changed tests before ai land request (60 s budget)"
	@echo "  make test-packages     Run tests in all subpackages"
	@echo "  make test-rust         Run cargo test in every Rust crate"
	@echo "  make test-all          Run ALL tests (core + packages + Rust crates)"
	@echo "  make test-spec-audit-sa002 Run SA-002 pytest + semgrep checks"
	@echo "  make bench-smoke       Run benchmark smoke checks without performance gating"
	@echo ""
	@echo "Formatting:"
	@echo "  make format            Format code with ruff"
	@echo "  make check             Run format check without modifying files"
	@echo "  make check-repo-hygiene Check generated artifacts are not tracked"
	@echo ""
	@echo "Utilities:"
	@echo "  make clean             Remove build artifacts and caches"
	@echo "  make install-opencode-spec-gap-tdd Install OpenCode spec-gap-tdd symlinks"

# =============================================================================
# Setup
# =============================================================================

install:
	uv sync --group dev

# Sync dependencies AND rebuild the Rust VM extension.
# ALWAYS use this instead of bare `uv sync` when Rust sources changed.
# ADR-DOE-ENFORCE-001 R4: VM conformance oracle は、どの build にも入っていて実行時に有効にする
# (agora-redesign #980 — 以前は cargo feature で make sync の build だけが検査つきになり、同じ venv が
# 最後に組んだ経路で 15 倍速さを変えた)。doeff の pytest は root の conftest.py が有効にし、
# tests/test_vm_invariant_checks_enabled.py が hard-fail で検査する(skip 禁止)。
# だから make sync と素の uv sync は同じ build を作る。uv は doeff-vm の tool.uv.cache-keys(.rs・Cargo.toml・Cargo.lock・
# doeff-vm-core を含む)の変化で build の口を呼び、口は同じ file の中身の鍵で wheel の保存先を先に引くので、Rust の source が
# 変わった時だけ作業木の外の一時の target で 1 回組む(ADR-DOE-BUILD-001・agora-redesign #1493・#3860 — 失敗ケース =
# tests/test_land_install_follows_rust_keys.py)。
sync:
	uv sync --group dev

pre-commit-install:
	uv run pre-commit install

# ADR-DOE-ENFORCE-001 R7: enforcement 台帳の著述時突合 hook(tracked 原本 =
# scripts/git-hooks/pre-commit)。pre-commit framework を使う機体は
# make pre-commit-install 側でも同じ検査が入る(.pre-commit-config.yaml の
# enforcement-ledger)— こちらは venv 不要の素の git hook。worktree 共通の
# hooks dir(git rev-parse --git-path hooks)へ入るので、一度の導入で全 worktree に効く。
hooks-install:
	cp scripts/git-hooks/pre-commit "$$(git rev-parse --git-path hooks)/pre-commit"
	chmod +x "$$(git rev-parse --git-path hooks)/pre-commit"

# ADR-DOE-ENFORCE-001 R9: enforcement 台帳(docs/adr/enforcement-ledger.json)は生成物 — 手で書かず、これで作り直す。
# 台帳から外れた項目(木から消えた law・検・規則)は名前で申告されるので、意図した削除かを確かめてから stage する。
# 勘定は stdlib 単独の script(PEP 723)なので project の環境は要らない(doeff の sync を起こさない)。
enforcement-ledger:
	uv run --script scripts/check_enforcement_ledger.py --write

# =============================================================================
# Linting - Architectural Enforcement
# =============================================================================

# Run ALL linters (core + packages)
lint: lint-ruff lint-pyright lint-semgrep lint-doeff lint-packages
	@echo ""
	@echo "All linters passed!"

# Ruff: Fast Python linter (style, imports, common issues)
lint-ruff:
	@echo "Running ruff..."
	uv run ruff check doeff/ tests/ packages/

# Pyright: Type checking
lint-pyright:
	@echo "Running pyright..."
	uv run pyright doeff/

# Semgrep: Architectural pattern enforcement (installed by make sync)
lint-semgrep:
	@echo "Running semgrep architectural rules..."
	uv run semgrep --metrics=off --disable-version-check --config .semgrep.yaml doeff/ packages/ --error

# Semgrep: Check docs for deprecated Runtime/Runner API usage
lint-semgrep-docs:
	@echo "Running semgrep on documentation..."
	uv run semgrep --metrics=off --disable-version-check --config .semgrep.yaml docs/ README.md --error

# Doeff-linter: Custom Rust-based linter for doeff patterns
lint-doeff:
	@echo "Running doeff-linter..."
	@if command -v doeff-linter >/dev/null 2>&1; then \
		root=0; doeff-linter --no-log doeff/ packages/ || root=$$?; \
		echo "Running doeff-linter in packages/doeff-cluster (package の設定と architecture.hy で)..."; \
		cluster=0; sh $(dir $(abspath $(lastword $(MAKEFILE_LIST))))scripts/lint-doeff-cluster.sh || cluster=$$?; \
		[ "$$root" -eq 0 ] && [ "$$cluster" -eq 0 ]; \
	else \
		echo "doeff-linter が未導入のため検査できません。成功として扱いません。" >&2; \
		echo "導入: cd packages/doeff-linter && cargo install --path ." >&2; \
		exit 127; \
	fi

# Run lint in all subpackages that have Makefiles
lint-packages:
	@echo "Running lint in subpackages..."
	@for dir in packages/*/; do \
		if [ -f "$$dir/Makefile" ]; then \
			echo ""; \
			echo "=== Linting $$(basename $$dir) ==="; \
			$(MAKE) -C "$$dir" lint || exit 1; \
		fi; \
	done

# =============================================================================
# Testing
# =============================================================================

PYTEST_MEM_GUARD_MB ?= 8192
PYTEST_MEM_GUARD_POLL_INTERVAL ?= 1.0
# 見張りの設定は tests/conftest.py の option に命令行で渡す(conftest は環境を読まない — #2896)。
PYTEST_MEMORY_OPTIONS := --mem-guard-mb=$(PYTEST_MEM_GUARD_MB) \
	--mem-guard-poll-interval=$(PYTEST_MEM_GUARD_POLL_INTERVAL)

# ADR-DOE-ENFORCE-001 R4: VM conformance oracle の Rust 側テスト(invariant-checks 有効)。
test-vm-invariants:
	cd packages/doeff-vm-core && cargo test --features "invariant-checks python_bridge"

test: bench-smoke
	uv run pytest $(PYTEST_MEMORY_OPTIONS)

test-unit:
	uv run pytest $(PYTEST_MEMORY_OPTIONS) -m "not e2e and not slow"

test-e2e:
	uv run pytest $(PYTEST_MEMORY_OPTIONS) -m "e2e"

# 登記(ai land request)の前に手元で走らせる変えた所の検(agora-redesign #2605)。分岐点(既定 git merge-base HEAD origin/main)から
# 作業木までに変えた file が当たる契約の検の組(root の pyproject.toml の [[tool.doeff.contract-tests]])を先頭に、変えた検の file と
# 逆依存の検を、下の test-packages と同じ session の境目(repo の根・package ごとの tests・PACKAGE_EXTRA_TEST_ROOTS)ごとに別の pytest で
# 走らせ、合計 60 秒で打ち切る(agora-redesign #2682)。終わらなかった file は「未測」と名指し、赤だけ rc 1。commit の hook では走らせない
# (#1122・#794)。分岐点を変える: make test-changed TEST_CHANGED_ARGS="--base <rev>"。
TEST_CHANGED_ARGS ?=
test-changed:
	uv run --script scripts/run_changed_tests.py $(TEST_CHANGED_ARGS)

# Run tests in all subpackages that have tests/ directories
# - tests/ に Python の検が 1 本も無い package(doeff-indexer / doeff-linter — 検は Rust の cargo test、
#   tests/fixtures の test_*.py は検体)は pytest に渡さない。検は test_*.py か test_*.hy(Hy の deftest か上から順に
#   実行する script — root の ini の doeff_hy_test_files で doeff-adr の plugin が集める・agora-redesign #2591)。渡すと「収集 0 件」の rc 5 で loop が止まり、
#   後ろの package が 1 本も走らない(2026-09-24 実測)。
# - 実 API / 実 CLI を撃つ e2e は日次の門(.agents/land-queue.toml gate.full)と同じく除く(-m "not e2e")。
# - PACKAGE_UV_RUN: 日次の門は make sync の直後に `uv run --no-sync` で呼ぶ(素の uv run の暗黙の再 sync で
#   直前に組んだ VM を組み直さないように)。
# - package の dir へ cd せず、repo の根から `pytest packages/<p>/tests` を呼ぶ。pytest の要約の FAILED 行は
#   cwd からの相対なので、package の dir から走らせると `tests/test_cli.py::…` の形になり、同名の test file を
#   持つ package どうしで失敗名が衝突する(日次の道具は失敗名を repo の根から pytest へそのまま渡す)。
#   session は package ごとに独立のまま(1 session に畳むと同名の test file が衝突する)。
# - 1 package の赤で止めず全 package を走らせ、最後に失敗の package を名指して 0 以外で終わる(test-rust と
#   同じ形)。赤で止めると後ろの package が 1 本も走らないまま日次に見えない(ADR-DOE-ENFORCE-001 R8・
#   tests/test_daily_test_population.py・card acp:kanban-issue:ki-08ec2d7c901f)。
# - PACKAGE_PYTEST_DURATIONS: package ごとの session の終わりに、10 秒以上かかった検を長い順に 20 本まで出す(日次の log に
#   1 本ごとの秒を残す)。2026-10-09 20:00 の日次で doeff-cluster が 814 → 1,740 秒に伸び、処理ステージ packages が時間切れに
#   なったが、log にも台帳にも 1 本ごとの秒が無く、どの検が伸びたかを割れなかった(card acp:kanban-issue:ki-107a03190129)。
PACKAGE_UV_RUN ?= uv run
PACKAGE_PYTEST_DURATIONS ?= --durations=20 --durations-min=10
# package の tests/ の外に在る検の根(package ごとの tests/ と同じく、根ごとに別の session で走らせる)。
# - packages/doeff-cluster/src/doeff_cluster/sim: 模擬の環境の下の deftest(各 service の入口の組み立てを模擬の handler の組で回す
#   検 — doeff-linter の DOEFF136 は検がこの dir の下に在ることを求める・fixture は package の根の conftest.py(source は pytest を
#   import しない — src の外で検の dir の祖先に当たる置き場・agora-redesign #2681)・agora-redesign #2542)。
PACKAGE_EXTRA_TEST_ROOTS = packages/doeff-cluster/src/doeff_cluster/sim
# 母集団の根の定義はこの変数の 1 点。置き場の検(tests/test_daily_test_population.py)と、登記の前の入口の session の境目
# (scripts/run_changed_tests.py の read_session_roots・agora-redesign #2682)は下の target で読む
# (検の側に 2 つ目の一覧を書かない — 書くと、根を足した時に片方だけが古くなる・agora-redesign #2577)。
print-package-extra-test-roots:
	@echo $(PACKAGE_EXTRA_TEST_ROOTS)
test-packages:
	@echo "Running tests in subpackages..."
	@failed=""; \
	for dir in packages/*/; do \
		if [ -d "$$dir/tests" ]; then \
			if [ -z "$$(find "$$dir/tests" \( -name 'test_*.py' -o -name 'test_*.hy' \) -not -path '*/fixtures/*' | head -1)" ]; then \
				echo ""; \
				echo "=== Skipping $$(basename $$dir) (no Python tests — Rust tests run under cargo) ==="; \
				continue; \
			fi; \
			echo ""; \
			echo "=== Testing $$(basename $$dir) ==="; \
			$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$(basename $$dir)"; \
		fi; \
	done; \
	for root in $(PACKAGE_EXTRA_TEST_ROOTS); do \
		if [ ! -d "$$root" ]; then echo ""; echo "=== Skipping $$root (no such directory) ==="; continue; fi; \
		echo ""; \
		echo "=== Testing $$root ==="; \
		$(PACKAGE_UV_RUN) pytest "$$root" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$root"; \
	done; \
	if [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; exit 1; fi

# Run cargo test in every Rust crate under packages/ (first red does not hide the rest — every crate
# runs, the target fails at the end if any crate failed). doeff-vm / doeff-vm-core embed CPython in
# their tests (pyo3 dev-dependency feature auto-initialize), so the test binary links libpython of the
# uv environment: PYO3_PYTHON names that interpreter and the loader path names its LIBDIR (the uv
# python is not installed system-wide — without it the binaries die with "libpython3.14t.so: cannot
# open shared object file", 2026-09-24 zeus). doeff-vm-core additionally runs its invariant-checks
# conformance oracle (test-vm-invariants) because dev builds always enable it (ADR-DOE-ENFORCE-001 R4).
RUST_TEST_PYTHON = $$(uv run --no-sync python -c 'import sys; print(sys.executable)')
RUST_TEST_LIBDIR = $$(uv run --no-sync python -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')
test-rust:
	@py="$(RUST_TEST_PYTHON)"; libdir="$(RUST_TEST_LIBDIR)"; failed=""; \
	for manifest in packages/*/Cargo.toml; do \
		dir=$$(dirname "$$manifest"); \
		echo ""; \
		echo "=== cargo test $$(basename $$dir) ==="; \
		(cd "$$dir" && PYO3_PYTHON="$$py" LD_LIBRARY_PATH="$$libdir$${LD_LIBRARY_PATH:+:$$LD_LIBRARY_PATH}" \
			DYLD_FALLBACK_LIBRARY_PATH="$$libdir$${DYLD_FALLBACK_LIBRARY_PATH:+:$$DYLD_FALLBACK_LIBRARY_PATH}" \
			cargo test) || failed="$$failed $$(basename $$dir)"; \
	done; \
	echo ""; \
	echo "=== cargo test doeff-vm-core --features invariant-checks python_bridge ==="; \
	(cd packages/doeff-vm-core && PYO3_PYTHON="$$py" LD_LIBRARY_PATH="$$libdir$${LD_LIBRARY_PATH:+:$$LD_LIBRARY_PATH}" \
		DYLD_FALLBACK_LIBRARY_PATH="$$libdir$${DYLD_FALLBACK_LIBRARY_PATH:+:$$DYLD_FALLBACK_LIBRARY_PATH}" \
		cargo test --features "invariant-checks python_bridge") || failed="$$failed doeff-vm-core[invariant-checks]"; \
	if [ -n "$$failed" ]; then echo "cargo test failed:$$failed"; exit 1; fi

# Run ALL tests: core + all subpackages + Rust crates
test-all: test test-packages test-rust
	@echo ""
	@echo "All tests passed!"

test-spec-audit-sa002:
	uv run pytest tests/core/test_sa002_spec_gaps.py
	uv run semgrep --metrics=off --disable-version-check --config specs/audits/SA-002/semgrep/rules.yml doeff/ packages/

bench-smoke:
	uv run python benchmarks/benchmark_runner.py --smoke --no-output
	cd packages/doeff-vm-core && PYO3_PYTHON="$$(cd ../.. && uv run python -c 'import sys; print(sys.executable)')" cargo bench --features python_bridge --bench dispatch -- --test

# =============================================================================
# Formatting
# =============================================================================

format:
	uv run ruff format doeff/ tests/ packages/
	uv run ruff check --fix doeff/ tests/ packages/

check: check-repo-hygiene
	uv run ruff format --check doeff/ tests/ packages/
	uv run ruff check doeff/ tests/ packages/

check-repo-hygiene:
	bash scripts/check-repo-hygiene.sh

# =============================================================================
# Utilities
# =============================================================================

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache __pycache__
	rm -rf dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

install-opencode-spec-gap-tdd:
	bash scripts/install-opencode-spec-gap-tdd.sh
