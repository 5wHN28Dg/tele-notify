## Real tesseract on small rendered images (needs tesseract-ocr + ara).

import std/[unittest, asyncdispatch, os, strutils, osproc, sequtils]
import telenotify/ocr

const fx = currentSourcePath.parentDir / "fixtures"

suite "ocr":
  test "English and Arabic text":
    let en = waitFor ocrFile(fx / "ocr_english.png")
    check en.ok
    check "Electrical Engineer" in en.text
    let ar = waitFor ocrBytes(readFile(fx / "ocr_arabic.png"))
    check ar.ok
    check "مهندس كهرباء" in ar.text

  test "a broken image is an error result, not a crash":
    let r = waitFor ocrBytes("this is not an image")
    check not r.ok

  test "issue #30: the bot keeps working while OCR runs":
    var ticks = 0
    proc ticker() {.async.} =
      for i in 0 ..< 200:
        inc ticks
        await sleepAsync(5)
    let t = ticker()
    let r = waitFor ocrFile(fx / "ocr_arabic.png")
    check r.ok
    check ticks > 3     # the ticker ran while tesseract was working
    waitFor t

suite "ocr: review pass 1 (stage 2) findings":
  test "two OCRs of the same image at the same time don't clash":
    let img = readFile(fx / "ocr_english.png")
    let a = ocrBytes(img)
    let b = ocrBytes(img)
    check (waitFor a).ok
    check (waitFor b).ok

  test "a timed-out tesseract is killed and reaped, temp files removed":
    let fake = getTempDir() / "tn-fake-bin"
    createDir(fake)
    writeFile(fake / "tesseract", "#!/bin/sh\nsleep 30\n")
    setFilePermissions(fake / "tesseract", {fpUserRead, fpUserWrite, fpUserExec})
    let oldPath = getEnv("PATH")
    putEnv("PATH", fake & ":" & oldPath)
    let tmpBefore = toSeq(walkDir(getTempDir())).len
    let r = waitFor ocrFile(fx / "ocr_english.png", timeoutMs = 300)
    putEnv("PATH", oldPath)
    check not r.ok and "timed out" in r.error
    let ps = execProcess("ps", args = ["-o", "stat=,comm=", "--ppid", $getCurrentProcessId()], options = {poUsePath})
    check "Z" notin ps
    check toSeq(walkDir(getTempDir())).len <= tmpBefore + 1  # fake-bin dir only
    removeDir(fake)
