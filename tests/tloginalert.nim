## When Telegram logs the account out, the terminal screen must say what to do (UX policy U1):
## the bot records one alert with the commands, and the connection as "logged out".

import std/[unittest, os, strutils]
import telenotify/[state, commands]

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
    # the steps first; `telenotify login` can't share TDLib's folder with a running bot
    check alerts[0].startsWith("Run `systemctl --user stop tele-notify`, then `telenotify login`, " &
                               "then `systemctl --user start tele-notify`:")
    check "authorizationState" notin alerts[0]                # plain words
    check "restart" notin alerts[0]
    check st.getMeta("connection") == "logged out"

  test "the dry run's service is named":
    let st = openState(dir / "state-dryrun.sqlite3")
    defer: st.close()
    recordLoginNeeded(st, "authorizationStateLoggingOut", dryRun = true)
    check st.alertTexts()[0].startsWith("Run `systemctl --user stop tele-notify-dryrun`, then `telenotify login`, " &
                                        "then `systemctl --user start tele-notify-dryrun`:")

  test "after logging in again, a later log-out alerts again":
    let st = openState(dir / "state2.sqlite3")
    defer: st.close()
    recordLoginNeeded(st, "authorizationStateWaitPhoneNumber", dryRun = false)
    clearLoginNeeded(st)
    recordLoginNeeded(st, "authorizationStateLoggingOut", dryRun = false)
    check st.alertTexts().len == 2


  test "plain causes: never logged in is not called a log-out":
    check "logged out" notin loginAlert("not logged in", dryRun = false)
    check "logged this account out" in loginAlert("authorizationStateLoggingOut", dryRun = false)

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
    check told[0].startsWith("⚠️ tele-notify: Run `systemctl --user stop tele-notify`")
    clearLoginNeeded(st)
    recordLoginNeeded(st, "authorizationStateLoggingOut", dryRun = true, tell)
    check told.len == 2 and told[1].startsWith("⚠️ tele-notify [dry run]: Run")
