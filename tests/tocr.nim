## Real tesseract on small rendered images (needs tesseract-ocr + ara).

import std/[unittest, asyncdispatch, os, strutils]
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
