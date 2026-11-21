## When Telegram logs the account out, the terminal screen must say what to do (UX policy U1):
## the bot records one alert with the commands, and the connection as "logged out".

import std/[unittest, os, strutils, asyncdispatch, json, times]
import telenotify/[state, commands, notify, listener, config, telegram, fetch, gather]

let dir = getTempDir() / "tn-login-alert-test"
removeDir(dir)
createDir(dir)

suite "login needed":
  test "one alert with the commands to run, however often the bot restarts":
    let st = openState(dir / "state.sqlite3")
    defer: st.close()
    for _ in 1 .. 3:                       # systemd restarts the bot again and again
      recordLoginNeeded(st, "authorizationStateWaitPhoneNumber", dryRun = false)
    let alerts = st.alertTexts()
    check alerts.len == 1
    # the cause first (a lock screen shows only the start), then the steps;
    # `telenotify login` can't share TDLib's folder with a running bot (review run 2)
    check alerts[0] == "Logged out of Telegram. To log in again: systemctl --user stop tele-notify; " &
                       "telenotify login; systemctl --user start tele-notify"
    check "`" notin alerts[0]                                 # plain text: no markup shown literally
    check "authorizationState" notin alerts[0]                # plain words
    check "restart" notin alerts[0]
    check st.getMeta("connection") == "logged out"

  test "the dry run's service is named":
    let st = openState(dir / "state-dryrun.sqlite3")
    defer: st.close()
    recordLoginNeeded(st, "authorizationStateLoggingOut", dryRun = true)
    check st.alertTexts()[0] == "Logged out of Telegram. To log in again: systemctl --user stop tele-notify-dryrun; " &
                                "telenotify login; systemctl --user start tele-notify-dryrun"

  test "after logging in again, a later log-out alerts again":
    let st = openState(dir / "state2.sqlite3")
    defer: st.close()
    recordLoginNeeded(st, "authorizationStateWaitPhoneNumber", dryRun = false)
    clearLoginNeeded(st)
    recordLoginNeeded(st, "authorizationStateLoggingOut", dryRun = false)
    check st.alertTexts().len == 2


  test "plain causes: never logged in is not called a log-out":
    check "logged out" notin loginAlert("not logged in", dryRun = false).toLowerAscii
    check loginAlert("not logged in", dryRun = false).startsWith("Not logged in to Telegram. To log in: systemctl")
    check loginAlert("authorizationStateLoggingOut", dryRun = false).startsWith("Logged out of Telegram.")

  test "TDLib closing by itself isn't a log-out":
    check not isLogOut("authorizationStateClosing")
    check isLogOut("authorizationStateLoggingOut")

  test "the log-out alert also goes through your bot, once per log-out (review run 1)":
    let st = openState(dir / "state3.sqlite3")
    defer: st.close()
    var told: seq[string]
    let tell = proc (text: string) {.closure, gcsafe.} =
      {.cast(gcsafe).}: told.add text
    for _ in 1 .. 3:                       # systemd restarts the bot again and again
      recordLoginNeeded(st, "authorizationStateWaitPhoneNumber", dryRun = false, tell)
    check told.len == 1
    check told[0].startsWith("⚠️ tele-notify: Logged out of Telegram. To log in again: systemctl --user stop tele-notify;")
    clearLoginNeeded(st)
    recordLoginNeeded(st, "authorizationStateLoggingOut", dryRun = true, tell)
    check told.len == 2 and told[1].startsWith("⚠️ tele-notify [dry run]: Logged out of Telegram.")

  test "with \"alerts\": false the log-out alert stays out of your bot; else it goes first (review run 2)":
    var sent: seq[string]
    let q = newNotifyQueue(proc (text: string): Future[string] {.async, closure, gcsafe.} =
      await sleepAsync(30)
      {.cast(gcsafe).}: sent.add text
      return "", proc (line: string) {.closure, gcsafe.} = discard)
    check loginTeller(q, alerts = false) == nil
    check loginTeller(nil, alerts = true) == nil
    let tell = loginTeller(q, alerts = true)
    for t in ["1", "2", "3"]: q.push t
    tell("logged out")
    waitFor q.flush()
    check sent == @["1", "logged out", "2", "3"]

  test "at a stop, what's queued for your bot (the log-out alert first) is sent before closing (review run 2)":
    let st = openState(dir / "state4.sqlite3")
    defer: st.close()
    var j = parseFile(currentSourcePath.parentDir / "keywords.json")
    j["target_chat"] = %(-999)
    let l = newListener(parseConfig(j), Telegram(), st, defaultFetcher(), defaultOcr(), false)
    l.log = proc (line: string) {.closure, gcsafe.} = discard
    var sent: seq[string]
    l.outbox = newNotifyQueue(proc (text: string): Future[string] {.async, closure, gcsafe.} =
      await sleepAsync(200)
      {.cast(gcsafe).}: sent.add text
      return "", l.log)
    l.outbox.push "a post"
    l.outbox.push "another post"
    let t0 = epochTime()
    waitFor finishNotifying(l, st, "authorizationStateLoggingOut", dryRun = false,
                            loginTeller(l.outbox, alerts = true))
    check sent.len == 3 and sent[1].startsWith("⚠️ tele-notify: Logged out of Telegram.")
    check epochTime() - t0 < 2
    check st.getMeta("login_needed") == "1"
    sent.setLen 0
    l.outbox.push "last"
    waitFor finishNotifying(l, st, "", dryRun = false, nil)     # a normal stop: flushed too
    check sent == @["last"]
