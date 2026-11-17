## Command-line entry point.
##
##   telenotify check [--config:FILE] [POSTFILE]
##       Classify one post (read from POSTFILE, or stdin) and explain why.
##
##   telenotify eval [--config:FILE] [--show:fp|fn|wrong|all] CORPUS.jsonl
##       Run the filter over a labelled corpus (one JSON object per line with
##       "id", "text" and "label": TRUE/FALSE/UNSURE) and print how well it did.
##
## The Telegram listener will be added as `telenotify run` in a later stage.

import std/[parseopt, json, strutils, strformat, tables, os, math]
import telenotify/[config, classify]

const usage = """
usage:
  telenotify check [--config:FILE] [POSTFILE]
  telenotify eval  [--config:FILE] [--show:fp|fn|wrong|all] CORPUS.jsonl
"""

proc printDecision(d: Decision) =
  echo "verdict: ", d.verdict
  for r in d.reasons: echo "  - ", r

proc cmdCheck(cfg: Config, path: string) =
  let text = if path == "" or path == "-": stdin.readAll else: readFile(path)
  printDecision(classify(text, cfg))

proc cmdEval(cfg: Config, path, show: string) =
  ## Counts per (label, verdict) and the precision of what would be published.
  var counts = initCountTable[string]()
  var total = 0
  for line in lines(path):
    if line.strip.len == 0: continue
    let j = parseJson(line)
    let label = j["label"].getStr
    let d = classify(j["text"].getStr, cfg)
    counts.inc label & " -> " & $d.verdict
    inc total
    let wrong = (label == "FALSE" and d.verdict == vPublish) or
                (label == "TRUE" and d.verdict == vReject)
    let pick = case show
      of "all": true
      of "fp": label == "FALSE" and d.verdict == vPublish
      of "fn": label == "TRUE" and d.verdict != vPublish
      of "wrong": wrong
      else: false
    if pick:
      echo "=== id ", j{"id"}.getInt, "  label ", label, "  ", j{"note"}.getStr
      echo j["text"].getStr.substr(0, 600)
      printDecision(d)
      echo ""
  echo &"{total} posts"
  for label in ["TRUE", "UNSURE", "FALSE"]:
    var row = &"  {label:<6}"
    for v in [vPublish, vReview, vReject]:
      row.add &"  {v}: {counts.getOrDefault(label & \" -> \" & $v):>3}"
    echo row
  let tp = counts.getOrDefault("TRUE -> publish")
  let fp = counts.getOrDefault("FALSE -> publish")
  let pubAll = tp + fp + counts.getOrDefault("UNSURE -> publish")
  let trueAll = tp + counts.getOrDefault("TRUE -> review") + counts.getOrDefault("TRUE -> reject")
  if pubAll > 0:
    echo &"published: {pubAll}, of which wrong: {fp} (precision on labelled: {int(round(tp.float / max(1, tp + fp).float * 100))}%)"
  if trueAll > 0:
    echo &"true matches published: {tp}/{trueAll}, sent to review: {counts.getOrDefault(\"TRUE -> review\")}, lost: {counts.getOrDefault(\"TRUE -> reject\")}"

proc main() =
  var cmd, configPath, show, arg = ""
  configPath = "config.json"
  for kind, key, val in getopt():
    case kind
    of cmdArgument:
      if cmd == "": cmd = key else: arg = key
    of cmdLongOption, cmdShortOption:
      case key
      of "config", "c": configPath = val
      of "show": show = val
      of "help", "h": echo usage; quit 0
      else: quit "unknown option --" & key & "\n" & usage
    of cmdEnd: discard
  let cfg = try: loadConfig(configPath)
            except ConfigError as e: quit "config error: " & e.msg
  case cmd
  of "check": cmdCheck(cfg, arg)
  of "eval":
    if arg == "" or not fileExists(arg): quit "eval needs a corpus file\n" & usage
    cmdEval(cfg, arg, show)
  else: quit usage

when isMainModule: main()
