"""The isolated audit harness for the terminal screen (docs/UI-UX-POLICY.md, section 3.3).

Every run works out the real home folder first (from the password database,
not $HOME), refuses to touch the owner's bot data, corpus or deployment,
then runs the screen in a sandbox: a temporary home, a fixed tiny config at
/tmp/tn-audit/config.json, synthetic data only, and fakes for systemctl,
xdg-open, gio, the browser and (by default) the telenotify program.

Collectors gather evidence; they only judge what is mechanical (contrast
ratios, stall times, the leak scan). Run `python -m audit --help` in tui/.

Nothing in this package may import telenotify_tui at module level: the
screen's modules read the environment when imported, so they are imported
only after the sandbox's environment is in place (see __main__.py).
"""
