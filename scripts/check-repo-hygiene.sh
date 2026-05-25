#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

tracked_artifacts="$(
	git ls-files -- \
		'None' \
		'Untitled' \
		'server.js' \
		'.claude/settings.local.json' \
		':(glob).agent-home/**' \
		':(glob).playwright-mcp/**' \
		':(glob).claude/skills/install-vscode-plugin/**' \
		':(glob).agents/skills/install-vscode-plugin/**' \
		'*.db' \
		'*.db-journal' \
		'*.db-shm' \
		'*.db-wal' \
		'*.sqlite' \
		'*.sqlite-journal' \
		'*.sqlite3' \
		'*.sqlite3-journal' \
		'*.sqlite3-shm' \
		'*.sqlite3-wal' \
		'*.jsonl'
)"

if [ -z "$tracked_artifacts" ]; then
	exit 0
fi

unexpected_artifacts=""
while IFS= read -r artifact_path; do
	case "$artifact_path" in
		# Add intentional fixture paths here, with the README or test that uses them.
		"")
			;;
		*)
			unexpected_artifacts="${unexpected_artifacts}${artifact_path}"$'\n'
			;;
	esac
done <<< "$tracked_artifacts"

if [ -n "$unexpected_artifacts" ]; then
	printf 'Tracked generated artifacts were found:\n' >&2
	printf '%s' "$unexpected_artifacts" >&2
	printf '\nMove intentional fixtures to an explicit fixture path and allowlist them here.\n' >&2
	exit 1
fi
