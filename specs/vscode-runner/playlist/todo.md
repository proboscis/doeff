# Implementation Checklist: VS Code doeff-runner Playlists

> **Status**: PLANNING — update this file as milestones complete.

## Phase 0: Spec & UX agreement
- [ ] Confirm playlist file format (`.vscode/doeff-runner.playlists.json`) and schema `v1`
- [ ] Confirm UX entrypoints (CodeLens, Programs tree, Playlists view, commands)
- [ ] Confirm single-root workspace assumption (multi-root out of scope for v1)

## Phase 1: Data model + storage
- [ ] Add `Playlist`, `PlaylistItem` types and schema validation
- [ ] Implement read/write helpers with deterministic JSON formatting
- [ ] Add file watcher to refresh UI when the playlist file changes

## Phase 2: Commands
- [ ] Add `addToPlaylist` flow (select playlist, tools, name)
- [ ] Add `runPlaylistItem` (quick pick across all items)
- [ ] Add `removePlaylistItem`
- [ ] Add `createPlaylist`, `deletePlaylist`
- [ ] Add `openPlaylistsFile`
- [ ] Implement automatic interpreter resolution (entrypoint location → closest interpreter)

## Phase 3: UI integration
- [ ] Add new view `doeff-playlists` under the doeff activity container
- [ ] Tree items for playlists + items (run/remove actions)
- [ ] Entry-point CodeLens `[+]` action `Add to Playlist`
- [ ] Programs tree inline `[+]` action `Add to Playlist`

## Phase 4: Validation
- [ ] Add unit tests for parsing + arg mapping
- [ ] Manual test on macOS/Linux/Windows (quoting + python discovery)
