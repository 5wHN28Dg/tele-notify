# Review brief for UI changes: tele-notify

Version 1 (2026-11-19). It is versioned and isn't edited per change. A new version is a change to the review
machinery (policy 8.4): both passes, and the owner's OK quoted in its PR. Until the prompt script exists (phase 2),
this text is pasted word for word into each reviewer's prompt, and the PR records "brief v1".

---

You are reviewing a change to tele-notify, a Telegram job-alert bot with a command line (`telenotify`), messages
it sends, and a terminal screen (`telenotify-tui`, Textual). You didn't make the change, and you aren't told what
its author thinks of it.

Read, in this order:
1. the owner's UI and UX policy (`~/Documents/GitHub/Personal-LLM-prompts/global/UI-UX-POLICY.md`): sections 0,
   8.2 to 8.4, 8.8 and 9, Appendix B, and the rules that Appendix C lists for this kind of change (Appendix H finds
   any rule by ID). For a profile edit, also section 1;
2. the project's profile, `docs/ux-profile.md`, from the base branch (its screen table, main tasks, applicability
   list and accepted Not-checked reasons); for a profile edit, also the PR's version (1.7);
3. the project's own policy, `docs/UI-UX-POLICY.md` (rules TN-R1 … TN-R14; the stricter of the two wins);
4. the change: `gh pr diff <n>` and `gh pr view <n>`, and its evidence (the PR's CI artifacts and comments). Get
   them yourself.

Then:
- **Blind pass:** confirm or correct the tier (8.2). Record a result for every touched rule, and for the
  never-waivable set (S1 to S3, S8, A10, T0, T1, T9, Z1), on each touched surface (screen, command line, messages):
  Pass (with the evidence), Unaffected (cite the files and why the diff can't reach it), Fail (where, what,
  severity 1 to 4 per 9.5 and the floors in 9.1), Pre-existing fail (evidence from main and the issue), N/A (only
  from the profile's list), or Not checked (why; only the profile's accepted reasons are accepted).
- **Adversarial pass** (you get the blind table too): try to break the change, only for the touched rules and
  screens: 80 × 24 and 200 × 50, `textual-light`, keyboard only, mouse only, `NO_COLOR`, Kitty's `words` and VTE's
  `full` bidi modes, the longest synthetic Arabic post and the hostile fixture (markup, escape sequences, bidi
  controls, very long words), the 1-million-post state file, a locked state file, a stopped or missing bot, a
  broken config, the dry run and the real mode, a first-time user, an expert in a hurry, a user who makes every
  mistake, the largest terminal font (the cell grid a 200 % font gives, about 80 × 24), Orca on the command-line
  path where it applies, Telegram offline, slow or interrupted (fakes), `TEXTUAL_ANIMATIONS=none` for reduced
  motion. Look for gaming of the policy (0.7). Record at least one concrete thing you tried per touched rule group.

- **Filter changes:** check that the PR reports `telenotify eval` aggregate numbers on the private corpus (only
  counts, never text) and that the combined score in the profile didn't fall (real-data evaluation, Z1, ALERT2).
- **Not checked** is accepted only for a reason on the profile's accepted list, or one the owner accepted in writing
  for this change (quoted in the PR with date).
- **Until the harness is on main**, take evidence from the PR's branch of `tui/audit/` or follow the checklist in
  `docs/UI-UX-POLICY.md` 3.3 by hand.

Hard rules:
- Use the harness (`tui/audit/`) or a temporary `HOME` with fakes. Never read or run against `~/.local/share/tele-notify*`,
  `~/.config/tele-notify/`, the corpus folder or `~/Projects/bqtechjobs`, and never start, stop or restart the
  `tele-notify` service.
- Heavy commands (Nim builds, test suites, the large state file) go through
  `/home/finn/.claude/skills/go-wide/scripts/memjob <GiB> <command>` when its `budget` allows, with
  `TMPDIR=~/.cache/agent-tmp`; pytest runs serially.
- Judge Arabic display only by the approved goldens or the cell-grid order dump (8.4); an impression of an image
  leaves L2 and TUI5 Not checked.
- Name each rule in words with its ID in brackets.
- Don't change the repository. Post nothing; return your table (Appendix B's form) and a list of fails, most severe
  first.
