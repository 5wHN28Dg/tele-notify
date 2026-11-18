"""Terminal screen for tele-notify (issue #10).

The Nim bot does the work; this app only reads and writes what the bot
already shares on disk, so it can be opened and closed at any time while
the bot runs:

* the bot's SQLite state (``state.sqlite3``, or ``state-dryrun.sqlite3``):
  what was judged, the review queue, alerts, the chat list, status values;
* ``config.json``: keywords, places, watched chats;
* the ``telenotify`` binary, for anything that needs the filter itself
  (explain, re-judge, corpus test), so there is only one filter.
"""
