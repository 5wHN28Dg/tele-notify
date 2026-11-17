## Reading text from images with tesseract, without freezing (issue #30).
##
## The old version called pytesseract directly, so the whole bot stopped for
## a second or more per image. Here tesseract runs as a separate process,
## and we `await` it, checking every 50 ms, so other work goes on meanwhile.
##
## Details that matter (several found by review):
## * tesseract writes its result to a *file*, and its progress messages go
##   to /dev/null (`-c debug_file=/dev/null`). Nothing goes into a pipe we
##   don't read: a full pipe would make tesseract wait forever.
## * every run gets its own new temporary folder (`createTempDir`), so two
##   OCRs of the same picture at the same moment (a post cross-posted in two
##   channels) can't overwrite each other's files, and names can't be
##   guessed. The folder is always removed afterwards.
## * on timeout the process is killed *and waited for*; otherwise it stays
##   behind as a zombie process.
##
## Needs the `tesseract` program with the `ara` and `eng` languages
## (Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-ara`).

import std/[asyncdispatch, osproc, os, strutils, times, tempfiles]

type
  OcrResult* = object
    ok*: bool
    text*: string
    error*: string

proc runTesseract(imagePath, dir, langs: string, timeoutMs: int): Future[OcrResult] {.async.} =
  let base = dir / "out"
  var p: Process
  try:
    p = startProcess("tesseract", args = [imagePath, base, "-l", langs, "-c", "debug_file=/dev/null"],
                     options = {poUsePath, poParentStreams})
  except OSError as e:
    return OcrResult(ok: false, error: "cannot start tesseract: " & e.msg.splitLines[0])
  let deadline = epochTime() + timeoutMs / 1000
  while p.running:
    if epochTime() > deadline:
      p.kill
      discard p.waitForExit   # reap it, or it stays as a zombie process
      p.close
      return OcrResult(ok: false, error: "tesseract timed out")
    await sleepAsync(50)
  let code = p.peekExitCode
  p.close
  if code != 0 or not fileExists(base & ".txt"):
    return OcrResult(ok: false, error: "tesseract failed (exit code " & $code & ")")
  try:
    return OcrResult(ok: true, text: readFile(base & ".txt").strip)
  except IOError as e:
    return OcrResult(ok: false, error: "cannot read tesseract output: " & e.msg)

proc withTempDir(body: proc (dir: string): Future[OcrResult] {.closure, gcsafe.}): Future[OcrResult] {.async.} =
  var dir = ""
  try:
    dir = createTempDir("telenotify-ocr-", "")
  except OSError as e:
    return OcrResult(ok: false, error: "cannot create temp folder: " & e.msg)
  try:
    result = await body(dir)
  finally:
    try: removeDir(dir)
    except OSError: discard

proc ocrFile*(imagePath: string, langs = "ara+eng", timeoutMs = 60_000): Future[OcrResult] {.async.} =
  ## Text in the image at `imagePath`. Never raises; check `ok`.
  result = await withTempDir(proc (dir: string): Future[OcrResult] {.closure, gcsafe.} =
    runTesseract(imagePath, dir, langs, timeoutMs))

proc ocrBytes*(image: string, langs = "ara+eng", timeoutMs = 60_000): Future[OcrResult] {.async.} =
  ## Same, for an image held in memory (downloaded from Telegram).
  result = await withTempDir(proc (dir: string): Future[OcrResult] {.closure, gcsafe.} =
    let path = dir / "image"
    try:
      writeFile(path, image)
    except IOError as e:
      var f = newFuture[OcrResult]("ocrBytes")
      f.complete OcrResult(ok: false, error: "cannot write temp image: " & e.msg)
      return f
    runTesseract(path, dir, langs, timeoutMs))
