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
## * only real pictures go to tesseract, and not huge ones. Given text
##   instead of a picture, tesseract treats it as a *list of files to read*,
##   so a crafted "image" could make it read local files; and a tiny PNG
##   claiming 30000×30000 pixels made it use 1.8 GB. `imageSize` reads the
##   size from the file header (PNG, JPEG, WebP, GIF) without decoding it.
## * tesseract and its image library print warnings straight to the
##   terminal; they're sent to /dev/null through a one-line `sh` wrapper
##   (the only non-Nim line here: doing the same redirect in Nim needs
##   low-level process code).
##
## Needs the `tesseract` program with the `ara` and `eng` languages
## (Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-ara`).

import std/[asyncdispatch, osproc, os, strutils, times, tempfiles]

const
  maxSide = 12_000          ## pixels; phone photos are ~4,000
  maxPixels = 40_000_000

type
  OcrResult* = object
    ok*: bool
    text*: string
    error*: string

proc u16be(s: string, i: int): int = (s[i].ord shl 8) or s[i + 1].ord
proc u16le(s: string, i: int): int = s[i].ord or (s[i + 1].ord shl 8)
proc u24le(s: string, i: int): int = s[i].ord or (s[i + 1].ord shl 8) or (s[i + 2].ord shl 16)
proc u32be(s: string, i: int): int =
  (s[i].ord shl 24) or (s[i + 1].ord shl 16) or (s[i + 2].ord shl 8) or s[i + 3].ord

proc imageSize*(data: string): (int, int) =
  ## (width, height) from the header of a PNG, JPEG, WebP or GIF file;
  ## (-1, -1) when the data isn't one of those.
  let n = data.len
  if n >= 24 and data.startsWith("\x89PNG\r\n\x1a\n") and data[12 .. 15] == "IHDR":
    return (u32be(data, 16), u32be(data, 20))
  if n >= 13 and (data.startsWith("GIF87a") or data.startsWith("GIF89a")):
    # The header's "screen" size can lie; the first image's own size is
    # what gets decoded, so report the larger of the two.
    var (w, h) = (u16le(data, 6), u16le(data, 8))
    var i = 13
    if (data[10].ord and 0x80) != 0: i += 3 * (1 shl ((data[10].ord and 7) + 1))  # colour table
    while i < n:
      case data[i].ord
      of 0x21:                       # extension: skip its sub-blocks
        i += 2
        while i < n and data[i].ord != 0: i += data[i].ord + 1
        inc i
      of 0x2C:                       # image descriptor
        if i + 9 <= n: return (max(w, u16le(data, i + 5)), max(h, u16le(data, i + 7)))
        return (-1, -1)
      else: return (-1, -1)
    return (w, h)
  if n >= 30 and data.startsWith("RIFF") and data[8 .. 11] == "WEBP":
    case data[12 .. 15]
    of "VP8 ": return (u16le(data, 26) and 0x3FFF, u16le(data, 28) and 0x3FFF)
    of "VP8L":
      let b = [data[21].ord, data[22].ord, data[23].ord, data[24].ord]
      return (1 + (b[0] or ((b[1] and 0x3F) shl 8)),
              1 + ((b[1] shr 6) or (b[2] shl 2) or ((b[3] and 0x0F) shl 10)))
    of "VP8X": return (1 + u24le(data, 24), 1 + u24le(data, 27))
    else: return (-1, -1)
  if n >= 4 and data[0].ord == 0xFF and data[1].ord == 0xD8:
    var i = 2
    while i + 9 < n:
      if data[i].ord != 0xFF: return (-1, -1)
      let marker = data[i + 1].ord
      if marker == 0xFF: inc i; continue                       # fill byte
      if marker in 0xD0 .. 0xD9 or marker == 0x01: i += 2; continue
      if marker in 0xC0 .. 0xCF and marker notin [0xC4, 0xC8, 0xCC]:
        return (u16be(data, i + 7), u16be(data, i + 5))         # SOF: height, then width
      i += 2 + u16be(data, i + 2)
  (-1, -1)

proc checkImage(data: string): string =
  ## Why `data` must not go to tesseract, or "".
  let (w, h) = imageSize(data)
  if w <= 0 or h <= 0: return "not an image (PNG, JPEG, WebP or GIF expected)"
  if w > maxSide or h > maxSide or w * h > maxPixels:
    return "image too large (" & $w & "×" & $h & " pixels)"
  ""

proc runTesseract(imagePath, dir, langs: string, timeoutMs: int): Future[OcrResult] {.async.} =
  let base = dir / "out"
  let imagePath = absolutePath(imagePath)   # "-" would mean "read standard input"
  var data = ""
  try:
    if getFileSize(imagePath) > 30_000_000:
      return OcrResult(ok: false, error: "image file too large")
    data = readFile(imagePath)
  except IOError, OSError:
    return OcrResult(ok: false, error: "cannot read image " & imagePath)
  let bad = checkImage(data)
  if bad != "": return OcrResult(ok: false, error: bad)
  var p: Process
  try:
    # sh is only used for the "2>/dev/null"; the file names are passed as
    # separate arguments ($1, $2...), never pasted into the command text.
    # `ulimit -v` caps tesseract's memory at ~2 GB, a backstop in case an
    # image header lies about the picture's size.
    p = startProcess("/bin/sh", args = ["-c", "ulimit -v 2000000; exec tesseract \"$1\" \"$2\" -l \"$3\" -c debug_file=/dev/null >/dev/null 2>&1",
                                        "sh", imagePath, base, langs],
                     options = {poParentStreams})
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
