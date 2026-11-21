## `telenotify alert TEXT` (#123): an alert from outside the bot (the nightly
## self-update) is kept for the terminal screen and sent through your own bot.

import std/[unittest, os, asyncdispatch, uri, strutils]
import telenotify/[state, commands, notify, config, fetch]

let dir = getTempDir() / "tn-alert-test"
removeDir(dir)
createDir(dir)

proc cfgIn(sub: string, token = "", alerts = true): Config =
  result = Config(alerts: alerts)
  result.dataDir = dir / sub
  createDir(result.dataDir)
  result.notifyBotToken = token

proc recorder(urls: ref seq[string]): Fetcher =
  result = proc (url: string): Future[Fetched] {.async, closure, gcsafe.} =
    {.cast(gcsafe).}: urls[].add url
    return Fetched(ok: true, status: 200, body: """{"ok":true}""")

proc textOf(url: string): string =
  for (k, v) in decodeQuery(parseUri(url).query):
    if k == "text": return v

suite "telenotify alert":
  test "kept for the terminal screen and sent to the user id the bot kept":
    let cfg = cfgIn("a", token = "123:ABC")
    block:
      let st = openState(statePath(cfg, false))
      st.setMeta(chatIdKey, "42")
      st.close()
    let urls = new seq[string]
    waitFor cmdAlert(cfg, "Self-update: could not download v4.1.0", recorder(urls))
    let st = openState(statePath(cfg, false))
    defer: st.close()
    check st.alertTexts() == @["Self-update: could not download v4.1.0"]
    check urls[].len == 1
    check "chat_id=42" in urls[0]
    check textOf(urls[0]) == "⚠️ tele-notify: Self-update: could not download v4.1.0"

  test "without a bot, or with alerts off, only the screen gets it":
    for (sub, token, alerts) in [("b", "", true), ("c", "123:ABC", false)]:
      let cfg = cfgIn(sub, token, alerts)
      let urls = new seq[string]
      waitFor cmdAlert(cfg, "x", recorder(urls))
      let st = openState(statePath(cfg, false))
      check st.alertTexts() == @["x"]
      check urls[].len == 0
      st.close()

  test "a user id not known yet doesn't stop the alert reaching the screen":
    let cfg = cfgIn("d", token = "123:ABC")
    let urls = new seq[string]
    waitFor cmdAlert(cfg, "y", recorder(urls))       # must not raise
    let st = openState(statePath(cfg, false))
    defer: st.close()
    check st.alertTexts() == @["y"]
    check urls[].len == 0
