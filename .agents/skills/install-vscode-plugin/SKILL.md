---
name: Install VSCode Plugin
description: This skill should be used when the user asks to "install the vscode plugin", "install doeff-runner to cursor", "install extension to cursor", "build and install vscode extension", "install plugin to cursor", "deploy extension", or mentions installing, building, or packaging the doeff VSCode/Cursor extension.
---

# Installing the doeff-runner VSCode/Cursor Plugin

This skill provides guidance for building and installing the doeff-runner VSCode extension to Cursor or VSCode.

## Quick Install

To install the plugin to Cursor, run:

```bash
cd ide-plugins/vscode/doeff-runner && make install-to-cursor
```

## Build Process Overview

The Makefile automates the entire build and installation process:

1. **Bundle indexer** - Build the doeff-indexer Rust binary and copy to `bin/`
2. **Compile TypeScript** - Run `tsc` to compile extension source
3. **Package** - Create `.vsix` extension package
4. **Install** - Use Cursor CLI to install the packaged extension

## Available Makefile Targets

| Target | Description |
|--------|-------------|
| `bundle-indexer` | Build and bundle the doeff-indexer binary from Rust source |
| `build` | Bundle indexer and compile TypeScript |
| `package` | Create .vsix package (runs build first) |
| `install-to-cursor` | Full build, package, and install to Cursor |
| `clean-indexer` | Remove bundled indexer binaries |

## Prerequisites

Ensure the following tools are available:

- **Node.js** - For npm and TypeScript compilation
- **Rust toolchain** - For building doeff-indexer (`cargo`)
- **Cursor CLI** - Available in PATH, or set `CURSOR_BIN` environment variable

## Installing to VSCode

To install to VSCode instead of Cursor:

```bash
cd ide-plugins/vscode/doeff-runner
make package
code --install-extension doeff-runner-*.vsix --force
```

## Troubleshooting

### Cursor CLI not found

Set the `CURSOR_BIN` environment variable to the path of the Cursor executable:

```bash
CURSOR_BIN=/path/to/cursor make install-to-cursor
```

### Rust build fails

Ensure Rust toolchain is installed:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

### Node.js/npm issues

Verify Node.js is available and install dependencies:

```bash
cd ide-plugins/vscode/doeff-runner
npm install
```
