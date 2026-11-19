# The audit harness

Evidence for the owner's UI and UX policy and this product's [profile](../../docs/ux-profile.md) (the project policy's [section 3.3](../../docs/UI-UX-POLICY.md)), one command per job, never touching your bot's data, services, desktop or browser. Run it from `tui/` with the screen's venv:

```bash
cd tui
.venv/bin/python -m audit <job> --out DIR [options]
```

Every run first reads your real home from the password database and refuses to start if any path it will use (symlinks followed, `--out` included) is in `~/.local/share/tele-notify*`, the corpus folder, `~/.config/tele-notify`, `~/.local/lib/tele-notify`, the installed `~/.local/bin/telenotify` and `telenotify-tui`, `~/tele-notify` or `~/Projects/bqtechjobs`. It then works in its own new folder `/tmp/tn-audit/<run>/` (deleted at the end): a temporary `HOME`, a tiny synthetic `config.json` (no `api_id` / `api_hash`), `data_dir`, labels and corpus inside it, `PATH` = the fakes folder + `/usr/bin:/bin`, and an environment built from scratch (no `DISPLAY`, `WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`, `XDG_RUNTIME_DIR`). Fakes stand in for `systemctl` (answers per service: active, inactive, failed or missing; logs every call), `xdg-open`, `gio`, the browser (`$BROWSER`) and `telenotify` (`--bin repo` uses the repository's build instead). The fakes' call logs are copied to `DIR/fake-calls/`. Data is synthetic only (`audit/data.py`). The test suite's autouse fixtures are not used: `DECIDE_DELAY` is real.

Every job ends with a leak scan (`DIR/leaks.json`): any file holding your login name or real home path makes the command exit 3. `DIR/run-<job>.json` records the screen's commit and the machine's load.

| Job | Evidence (in DIR) | Options |
|---|---|---|
| `isolation` | `isolation.json`: environment, where each program resolves, what the fakes logged when `o` and Stop / Start were pressed, the guard refusing the real places | |
| `screenshots` | `screenshots/<mode>/<theme>/bidi-<mode>/<size>/*.svg` and `.txt`: every tab, view, dialog, drop-down, the keys panel, palette and a toast; `index.json` | `--sizes 80x24,120x40` `--bidi full,words` `--themes dark,light` `--modes dry-run,real` `--only <name prefixes>` |
| `visible` | `visible/<mode>/<size>/items.md`: each tab-table item, in the state its row names, visible or why not (R2.1) | `--sizes` `--modes` |
| `keys` | `keys/<size>.json` / `.md`: the footer and `app.active_bindings` at every stop of each focus chain, per tab, section and dialog (R1, R8.1) | `--sizes` |
| `colours` | `colours/<theme>/`: SVG, greyscale copy and fg / bg pairs with contrast for each R6 state; `summary.md` | `--themes` |
| `timing` | `timing/summary.md` / `.json`: longest event-loop stall per run (asyncio debug), median, first-tab time, load; with no lock, a write lock and an exclusive lock held by another process (R7) | `--runs 3` `--locks none,write,exclusive` `--posts N` `--compare <other checkout>/tui` |
| `hostile` | `hostile/bidi-<mode>/steps.json`: the real screen in a pty with markup and escape payloads everywhere; payload sequences found and markers seen per step (R9) | `--bidi` |
| `failures` | `failures/<condition>/`: each tab, and one action per tab, under each R10 condition | `--only <conditions>` |
| `tasks` | `tasks/costs.md`: keys and clicks of each Search and Settings task script (`audit/tasks/`), keyboard and mouse, and whether it reached its end state (R13) | `--only <task words>` |
| `styles` | `styles/summary.md`: literal colours and off-scale spacing in `tui/` (R14.1, R14.2), primary buttons / `$primary` text per tab view (R14.3) | |
| `leaks` | scans an existing DIR again | |
| `big-state` | builds the large master state file (1 000 000 posts, 2 000 reviews, 300 chats) under `~/.cache/agent-tmp/tn-audit/` | `--posts N` `--rebuild` |

The large master file is built once (about 10 s) and never opened for writing: each timing or interactive run copies it into its own scratch folder. It is rebuilt when its manifest no longer matches (size, schema version). On a shared machine run heavy jobs (`timing`, `big-state`) through the memory guard you use for builds.

**Another checkout.** `--app PATH/tui` audits that checkout's screen with this harness, so task costs and timings can be taken on main and on a branch: `git worktree add /tmp/main-wt main`, then `python -m audit tasks --app /tmp/main-wt/tui --out A` and again without `--app`, and diff the tables. `timing --compare /tmp/main-wt/tui` interleaves the two checkouts' runs.

**Interactive mode** (the owner's sign-off): `python -m audit interactive [--bidi full|words] [--size-check] [--real] [--big]` starts the real screen in your terminal on the same kind of run folder, with every isolation above (Start / Stop / Restart reach only the fake `systemctl`), on the plain synthetic data. Only the terminal's own variables (`TERM`, `COLORTERM`, `KITTY_WINDOW_ID`...) are kept so it draws as it would for you. Without `--bidi` the screen picks the mode for your terminal.

Tests for the harness itself: `tests/test_audit.py`.
