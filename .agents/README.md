# Project agent setup

Claude Code and Codex share `../AGENTS.md` and `skills/`.
`.claude/skills` is a relative symlink to `.agents/skills`; Git stores the link,
not a second copy of the skills. Preserve symlinks when cloning on Windows.

Claude Code must be 2.1.281 or newer. Its default AGENTS.md fallback requires
that no parent/project `CLAUDE.md` or `CLAUDE.local.md` shadows it. If you keep
local Claude instructions, select `claude-md-and-agents-md` in `/config`.
Tool-specific permissions, hooks and MCP configuration remain tool-specific.

References: [Claude instructions](https://code.claude.com/docs/en/memory#read-agentsmd),
[Claude skills](https://code.claude.com/docs/en/skills),
[Codex skills](https://learn.chatgpt.com/docs/build-skills).
