# Point a project at its video again after it moved

**Priority:** medium — the one real project cannot be opened on this machine: its `project.toml` still names the video where it sat on the previous (WSL) machine, and no command changes that
**Gate:** decision:Add `talk-studio relink <video>`, which points an existing project at its video's new location and keeps every pick when the file is the same one?

**Goal:** A project whose video was moved, or that was checked out on a new machine, is usable again with one command and keeps its picks, instead of failing every command with "source … not found".

## Context

As of `c9b2fec` (2026-11-21).

- `project.py` `Project.save` writes `source.path` as an absolute path
  (`str(self.source)`, resolved in `Project.init`). The word timings beside it are
  stored relative to the project (`os.path.relpath(self.words, self.root)`), so the two
  halves of `[source]` already disagree on this.
- `Project.check_source` raises `ProjectError("source … not found")` when the path is
  gone. `cli.py` `command_status`, `sampling.render_round` and `final.render` all call it
  before doing any work, so `status`, `sample` and `render` stop there; `status` cannot
  even list the decisions. `tests/test_project.py` `test_a_missing_source_is_an_error` pins this.
- Nothing rewrites the path. `Project.init` refuses a folder that already has a
  `project.toml` ("already exists"), and the CLI has no other command that touches
  `source`. The only way out today is editing `project.toml` by hand.
- The repo rule (`AGENTS.md`) is that project folders live beside the material they
  describe, not in this repo. In practice the project is committed in the owner's repo
  while the recording is not (the UPM master is 4.3 GB). So the text of a
  project routinely outlives the path of its video. The one real project (the UPM talk,
  three fixed decisions and six clips, all picked) still stores a `/mnt/c/Users/…/Downloads/…`
  path from the WSL setup that `0e639bc` (docs(plans): talk-studio paths and review page
  are native Linux, not WSL) retired from the design; that path does not exist on the
  current machine.
- `media.fingerprint` (size, duration, first and last 8 MB) already tells whether a file
  at a new path is the same recording.

Dependency category: in-process (plus ffprobe, which the tests already use on generated
media).

## Design

**Chosen:** a `relink` command over one new `Project` method.

```python
# illustrative
def relink(self, video: Path, *, accept_changed: bool = False) -> bool:
    """Point the project at `video`. True when it is the same recording."""
```

- Same fingerprint: only `source.path` changes; statuses, picks and excerpts are kept.
- Different fingerprint: refused with a message saying the file differs, unless
  `--changed` is passed; then it does what `check_source` does for a changed source
  (picked decisions become `stale`, fingerprint and duration are replaced).
- `check_source`'s "not found" message names the command:
  ``run `talk-studio relink <video>` ``.
- `command_status` reports a missing source as a line (and `"source_found": false` in
  `--json`) and still lists the decisions, so an agent can see what the project holds
  before the video is found.
- `.agents/skills/talk-studio/SKILL.md` gets one rule: a project opened on another
  machine starts with `relink`.

**Alternatives considered:** store `source.path` relative to the project, like `words` —
lost: the recording is deliberately kept outside the repo that holds the project, so a
relative path breaks on the same moves. A `--source` override on every command — lost:
every call must repeat it and the stale path stays in the file. Let `init` overwrite —
lost: it would reset decisions and picks.

**Testing strategy:** in `tests/test_project.py`, on the generated `tiny_video`: move the
file and relink (picks kept, path updated, round-trips through `load`); relink to a
different file (refused; with `accept_changed` the picks go stale). In
`tests/test_studio_cli.py`: `status` with a missing source exits 0 and lists decisions;
`relink` end to end. `test_a_missing_source_is_an_error` stays, with the new message.

## Paths

As of 2026-11-21:

- `src/talk_studio/project.py`
- `src/talk_studio/cli.py`
- `tests/test_project.py`
- `tests/test_studio_cli.py`
- `.agents/skills/talk-studio/SKILL.md`
- `README.md` (one line)

## Approval

**Needs your yes** — the `Gate` question; recommended answer: yes, because the repo has no
`## Approval` section, the change adds one command and loosens one error, and without it
the existing project can only be revived by hand-editing `project.toml`.

## Out of scope

- Rebuilding the render cache on a new machine: `cache.project_dir` is keyed by the
  project folder's path, so samples and renders are re-made on demand (see
  `clips-follow-the-final` for the one case where that is not possible today).
- Deciding where masters are stored long term.
- Changing how `words` is stored.
