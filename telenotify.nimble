# Package

version       = "4.0.0"
author        = "5wHN28Dg"
description   = "Watches Telegram job channels and forwards entry-level jobs that match your filters"
license       = "AGPL-3.0-or-later"
srcDir        = "src"
bin           = @["telenotify"]

# Dependencies

requires "nim >= 2.0.0"
requires "regex >= 0.26.1"
requires "unicodedb >= 0.13.2"
requires "htmlparser >= 0.1.0"
requires "db_connector >= 0.1.0"
