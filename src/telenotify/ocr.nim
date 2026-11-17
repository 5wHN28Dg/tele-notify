## Reading text from images with tesseract, without freezing (issue #30).
##
## The old version called pytesseract directly, so the whole bot stopped for
## a second or more per image. Here tesseract runs as a separate process,
## and we `await` it, checking every 50 ms, so other work goes on meanwhile.
##
## tesseract writes its result to a file (not a pipe): a pipe has a small
## buffer, and a big result could make tesseract and us wait for each other
## forever.
##
## Needs the `tesseract` program with the `ara` and `eng` languages
## (Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-ara`).

import std/[asyncdispatch, osproc, os, strutils, times, hashes]

type
  OcrResult* = object
    ok*: bool
    text*: string
    error*: string

proc ocrFile*(imagePath: string, langs = "ara+eng", timeoutMs = 60_000): Future[OcrResult] {.async.} =
  ## Text in the image at `imagePath`. Never raises; check `ok`.
  let base = getTempDir() / ("telenotify-ocr-" & $getCurrentProcessId() & "-" & $epochTime().int64 &
                             "-" & $imagePath.hash)
  var p: Process
  try:
    p = startProcess("tesseract", args = [imagePath, base, "-l", langs],
                     options = {poUsePath, poStdErrToStdOut})
  except OSError as e:
    return OcrResult(ok: false, error: "cannot start tesseract: " & e.msg.splitLines[0])
  let deadline = epochTime() + timeoutMs / 1000
  while p.running:
    if epochTime() > deadline:
      p.kill
      p.close
      return OcrResult(ok: false, error: "tesseract timed out")
    await sleepAsync(50)
  let code = p.peekExitCode
  p.close
  let outFile = base & ".txt"
  if code != 0 or not fileExists(outFile):
    removeFile(outFile)
    return OcrResult(ok: false, error: "tesseract failed (exit code " & $code & ")")
  result = OcrResult(ok: true, text: readFile(outFile).strip)
  removeFile(outFile)

proc ocrBytes*(image: string, langs = "ara+eng"): Future[OcrResult] {.async.} =
  ## Same, for an image held in memory (downloaded from Telegram).
  let path = getTempDir() / ("telenotify-img-" & $getCurrentProcessId() & "-" & $epochTime().int64 & "-" & $image.hash)
  try:
    writeFile(path, image)
  except IOError as e:
    return OcrResult(ok: false, error: "cannot write temp image: " & e.msg)
  result = await ocrFile(path, langs)
  removeFile(path)
