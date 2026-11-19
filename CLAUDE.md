# tele-notify

Project kind: passion project. On 2026-11-17 the owner delegated the Nim rewrite to the agent with the full workflow (feature branch, PR, two review passes, merge to main when CI is green) and asked for the GitHub wiki (`tele-notify.wiki`) to be kept in sync with every change.

- Rewrite plan and rationale: `docs/problems-report.md` section 8; progress and how the code works: `docs/rewrite-guide.md`.
- The Python version is retired in `legacy/` (reference only, no fixes); the Nim bot runs in production.
- Never commit Telegram sessions, real config.json values, or the private Saved Messages corpus (`~/.local/share/tele-notify-corpus/`).
- Before merging a filter change, run `telenotify eval` against the private corpus and put the numbers in the PR.

## UI and UX

On 2026-11-19 the owner adopted their UI and UX policy v4.13 for this project ("let's adopt it and start working").
The policy is `global/UI-UX-POLICY.md` in the owner's prompts repo (locally
`~/Documents/GitHub/Personal-LLM-prompts/global/UI-UX-POLICY.md`). This project's profile is `docs/ux-profile.md`, and
its review brief is `docs/ux-review-brief.md`.

For any change a user can see, hear or touch, follow that policy (its section 0.2 says how it fits the
steps above): its evidence is part of step 2, its blockers (section 9) are part of step 5's "merge when CI is green",
and in step 6, don't deploy a tier-2 or tier-3 UI change that is still waiting for my check (its section 8.5), unless
it is merged switched off; report it as waiting instead. A parent agent that delegates UI work to a subagent runs
this policy's evidence and passes on the result itself.

The terminal screen (`tui/`) is inside the delegation above, together with the bot, its command line and its
messages. Asked on 2026-11-19 "Is the terminal screen (tui/) part of the work you've delegated to me, and should I add
the policy's hook-up lines to the project's CLAUDE.md?", the owner answered "Yes, screen + hook-up".
