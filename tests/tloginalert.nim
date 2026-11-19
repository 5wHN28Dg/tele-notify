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
    check alerts[0].startsWith("Run `telenotify login`")   # the step first
    check "authorizationState" notin alerts[0]                # plain words
    check "systemctl --user restart tele-notify" in alerts[0]
    check st.getMeta("connection") == "logged out"

  test "the dry run's service is named":
    let st = openState(dir / "state-dryrun.sqlite3")
    defer: st.close()
    recordLoginNeeded(st, "authorizationStateLoggingOut", dryRun = true)
    check "systemctl --user restart tele-notify-dryrun" in st.alertTexts()[0]

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
