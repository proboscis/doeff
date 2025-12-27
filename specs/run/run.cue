// Run v0 Schema
// Reproducible execution records for arbitrary command execution.
//
// A Run captures exactly what is needed to reproduce a command execution:
// - exec: What to execute and how (argv, cwd, env, timeout)
// - code_state: Which code state to use (repo, commit, optional patch)
//
// Design principles:
// - Purity: Run only contains execution info, no metadata (tags, notes go to Experiment layer)
// - Reproducibility: All fields are resolved/determined (no templates, no interactive input)

package run

// Run is the top-level record for a reproducible execution.
#Run: {
	run_version: 0
	run_id:      =~"^run_[0-9A-Z]{26}$" // ULID format with run_ prefix
	exec:        #Exec
	code_state:  #CodeState
}

// Exec describes what to execute and how.
#Exec: {
	argv:        [...string] & [_, ...] // non-empty array of strings
	cwd:         string                  // relative to repo root
	env:         [string]: string        // environment variables (all string values)
	timeout_sec: int & >=0 | *0          // 0 = unlimited
}

// CodeState identifies the exact code state for reproduction.
#CodeState: {
	repo_url:    string               // clone-able URL (git@..., https://...)
	base_commit: =~"^[a-f0-9]{40}$"   // full SHA (40 chars)
	patch?:      #Patch               // optional patch for uncommitted changes
}

// Patch references a diff stored as a git blob.
#Patch: {
	ref:    =~"^refs/patches/"        // git ref in refs/patches/ namespace
	sha256: =~"^[a-f0-9]{64}$"        // SHA-256 hash of patch content
}
