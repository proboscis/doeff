SHELL := /bin/bash

ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPO_ROOT := $(abspath $(ROOT_DIR)/../../..)
INDEXER_DIR := $(REPO_ROOT)/packages/doeff-indexer
BIN_DIR := $(ROOT_DIR)/bin
EXTENSION_NAME := $(shell node -p "require('./package.json').name")
EXTENSION_VERSION := $(shell node -p "require('./package.json').version")
VSIX := $(ROOT_DIR)/$(EXTENSION_NAME)-$(EXTENSION_VERSION).vsix
CURSOR_BIN ?= cursor

UNAME_S := $(shell uname -s 2>/dev/null || echo "")
UNAME_M := $(shell uname -m 2>/dev/null || echo "")

ifeq ($(OS),Windows_NT)
  PLATFORM := windows
  BIN_SUFFIX := .exe
else ifeq ($(UNAME_S),Darwin)
  PLATFORM := darwin
  BIN_SUFFIX :=
else ifeq ($(UNAME_S),Linux)
  PLATFORM := linux
  BIN_SUFFIX :=
else ifneq (,$(findstring MINGW,$(UNAME_S)))
  PLATFORM := windows
  BIN_SUFFIX := .exe
else ifneq (,$(findstring MSYS,$(UNAME_S)))
  PLATFORM := windows
  BIN_SUFFIX := .exe
else ifneq (,$(findstring CYGWIN,$(UNAME_S)))
  PLATFORM := windows
  BIN_SUFFIX := .exe
else
  $(error Unsupported platform: $(UNAME_S))
endif

ifeq ($(PLATFORM),windows)
  ARCH := x64
else ifeq ($(UNAME_M),arm64)
  ARCH := arm64
else ifeq ($(UNAME_M),aarch64)
  ARCH := arm64
else
  ARCH := x64
endif

ifeq ($(PLATFORM),windows)
  BUNDLED_BINARY := doeff-indexer-windows-x64.exe
else
  BUNDLED_BINARY := doeff-indexer-$(PLATFORM)-$(ARCH)
endif

INDEXER_BUILD := $(INDEXER_DIR)/target/release/doeff-indexer$(BIN_SUFFIX)

.PHONY: bundle-indexer build package clean-indexer install-to-cursor

bundle-indexer:
	@mkdir -p $(BIN_DIR)
	@echo "Bundling doeff-indexer -> $(BIN_DIR)/$(BUNDLED_BINARY)"
	@cd $(INDEXER_DIR) && cargo build --release --no-default-features --bin doeff-indexer
	@cp $(INDEXER_BUILD) $(BIN_DIR)/$(BUNDLED_BINARY)
	@chmod +x $(BIN_DIR)/$(BUNDLED_BINARY) || true

build: bundle-indexer
	@npm run vscode:prepublish

package: build
	@npx --yes @vscode/vsce package -o $(VSIX)
	@echo "Packaged $(VSIX)"

install-to-cursor: package
	@if ! command -v $(CURSOR_BIN) >/dev/null 2>&1; then \
		echo "Cursor CLI not found. Set CURSOR_BIN or add cursor to PATH."; \
		exit 1; \
	fi
	@$(CURSOR_BIN) --install-extension $(VSIX) --force
	@echo "Installed $(VSIX) to Cursor"

clean-indexer:
	@rm -f $(BIN_DIR)/doeff-indexer-*
