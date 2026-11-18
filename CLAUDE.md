# tele-notify

Project kind: passion project. On 2026-11-17 the owner delegated the Nim rewrite to the agent with the full workflow (feature branch, PR, two review passes, merge to main when CI is green) and asked for the GitHub wiki (`tele-notify.wiki`) to be kept in sync with every change.

- Rewrite plan and rationale: `docs/problems-report.md` section 8; progress and how the code works: `docs/rewrite-guide.md`.
- The Python version is retired in `legacy/` (reference only, no fixes); the Nim bot runs in production.
- Never commit Telegram sessions, real config.json values, or the private Saved Messages corpus (`~/.local/share/tele-notify-corpus/`).
- Before merging a filter change, run `telenotify eval` against the private corpus and put the numbers in the PR.
