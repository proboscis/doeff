#!/usr/bin/env bash

set -euo pipefail

SOURCE_OVERRIDE="${SPEC_AUDIT_SOURCE_ROOT:-}"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
OPENCODE_CONFIG_DIR="${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}"

TARGET_SKILLS_DIR="$OPENCODE_CONFIG_DIR/skills"
TARGET_COMMANDS_DIR="$OPENCODE_CONFIG_DIR/commands"

find_source_root() {
    local candidate
    local cache_base

    if [ -n "$SOURCE_OVERRIDE" ]; then
        if [ -f "$SOURCE_OVERRIDE/skills/spec-gap-tdd/SKILL.md" ] && [ -d "$SOURCE_OVERRIDE/commands" ]; then
            printf '%s\n' "$SOURCE_OVERRIDE"
            return 0
        fi
        echo "SPEC_AUDIT_SOURCE_ROOT is set but invalid: $SOURCE_OVERRIDE" >&2
        return 1
    fi

    for candidate in \
        "$HOME/repos/skills/spec-audit" \
        "$CLAUDE_HOME/plugins/spec-audit" \
        "$CLAUDE_HOME/plugins/local-marketplace/plugins/spec-audit"
    do
        if [ -f "$candidate/skills/spec-gap-tdd/SKILL.md" ] && [ -d "$candidate/commands" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    cache_base="$CLAUDE_HOME/plugins/cache/local/spec-audit"
    if [ -d "$cache_base" ]; then
        for candidate in "$cache_base"/*; do
            if [ -f "$candidate/skills/spec-gap-tdd/SKILL.md" ] && [ -d "$candidate/commands" ]; then
                printf '%s\n' "$candidate"
                return 0
            fi
        done
    fi

    return 1
}

backup_if_needed() {
    local target="$1"
    local stamp
    stamp="$(date +%Y%m%d%H%M%S)"

    if [ -L "$target" ]; then
        rm -f "$target"
        return 0
    fi

    if [ -f "$target" ]; then
        mv "$target" "$target.backup.$stamp"
        return 0
    fi

    if [ -d "$target" ]; then
        mv "$target" "$target.backup.$stamp"
    fi
}

link_path() {
    local src="$1"
    local dst="$2"

    backup_if_needed "$dst"
    ln -s "$src" "$dst"
    echo "linked: $dst -> $src"
}

main() {
    local source_root
    local skill_src
    local command_src
    local command_name
    local installed_count

    if ! source_root="$(find_source_root)"; then
        echo "Could not locate spec-audit plugin source." >&2
        echo "Searched under: $CLAUDE_HOME/plugins" >&2
        echo "Set SPEC_AUDIT_SOURCE_ROOT to override." >&2
        exit 1
    fi

    skill_src="$source_root/skills/spec-gap-tdd"
    command_src="$source_root/commands"

    mkdir -p "$TARGET_SKILLS_DIR" "$TARGET_COMMANDS_DIR"

    link_path "$skill_src" "$TARGET_SKILLS_DIR/spec-gap-tdd"

    installed_count=0
    for command_name in spec-audit.md spec-audit-team.md spec-audit-subagent.md; do
        if [ -f "$command_src/$command_name" ]; then
            link_path "$command_src/$command_name" "$TARGET_COMMANDS_DIR/$command_name"
            installed_count=$((installed_count + 1))
        fi
    done

    if [ "$installed_count" -eq 0 ]; then
        echo "No command markdown files found in: $command_src" >&2
        exit 1
    fi

    echo ""
    echo "Installed OpenCode symlinks:"
    echo "  skill:    $TARGET_SKILLS_DIR/spec-gap-tdd"
    echo "  commands: $TARGET_COMMANDS_DIR/spec-audit*.md"
    echo ""
    echo "Verify in OpenCode by running:"
    echo "  opencode --version"
    echo "  opencode"
    echo "Then check slash commands and run /spec-audit <spec-file> <impl-dir>."
}

main "$@"
