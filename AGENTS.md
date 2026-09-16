# AGENTS.md

talk-studio: agents propose edits to a recorded talk using several tools; a human
picks what feels right from rendered samples. Design:
[docs/plans/ideas/talk-studio-design.md](docs/plans/ideas/talk-studio-design.md).

- Run tests with `uv run pytest -m "not gpu and not slow"`.
- To use the tool on a recording, follow [.agents/skills/talk-studio/SKILL.md](.agents/skills/talk-studio/SKILL.md).
- Plans follow `docs/plans/{ideas,ready,ongoing}/`, no date prefixes.
- A tool is an adapter in `src/talk_studio/tools/`: declare `params`, `requires()` and
  `process()`; heavy models run in pinned `uvx` environments.
- No project material in this repo: recordings, transcripts and project folders live
  beside the material they describe.
