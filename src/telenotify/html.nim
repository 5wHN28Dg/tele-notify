## Small HTML helpers for the scrapers.
##
## Uses the `htmlparser` package (it moved out of Nim's standard library;
## lenient enough for LinkedIn and
## Yadanoo pages; parsing a 250 KB page takes a few milliseconds).
## Two things it doesn't do the way we need, done here instead:
## * find elements by one *word* of their class attribute. Sites keep adding
##   utility classes ("description__text description__text--rich mt-2"), so
##   comparing the whole class string, like the old Python code did, breaks.
## * turn HTML into text *with line breaks* at paragraphs, list items and
##   `<br>`, because the filter works line by line. `innerText` glues
##   "Job Summary" and "A leading..." into "Job SummaryA leading...".

import std/[xmltree, strutils]
import pkg/htmlparser

proc hasClass*(n: XmlNode, cls: string): bool =
  n.kind == xnElement and n.attrs != nil and cls in n.attr("class").splitWhitespace

# The walks below use an explicit stack instead of recursion: htmlparser
# nests unclosed tags (<p>, <li>...) inside each other, so real pages give
# trees thousands of levels deep, which overflowed the call stack.

proc findAllByClass*(n: XmlNode, cls: string): seq[XmlNode] =
  ## Every element having `cls` among its class words, in document order.
  var stack = @[n]
  while stack.len > 0:
    let x = stack.pop
    if x == nil or x.kind != xnElement: continue
    if x.hasClass(cls): result.add x
    for i in countdown(x.len - 1, 0): stack.add x[i]

proc findTag*(n: XmlNode, tag: string): XmlNode =
  ## First element called `tag` (document order), or nil.
  var stack = @[n]
  while stack.len > 0:
    let x = stack.pop
    if x == nil or x.kind != xnElement: continue
    if x.tag == tag: return x
    for i in countdown(x.len - 1, 0): stack.add x[i]
  nil

const blockTags = ["p", "div", "li", "ul", "ol", "br", "h1", "h2", "h3", "h4",
                   "h5", "h6", "tr", "section", "article", "header", "footer"]
const skipTags = ["script", "style", "noscript", "button", "svg"]

proc addText(root: XmlNode, s: var string) =
  # Each stack entry is a node plus "am I leaving it?": block elements
  # need a line break both when entering and when leaving.
  var stack = @[(root, false)]
  while stack.len > 0:
    let (n, leaving) = stack.pop
    case n.kind
    of xnText, xnCData, xnEntity:
      s.add n.text
    of xnElement:
      let isBlock = n.tag in blockTags
      if leaving:
        if isBlock and s.len > 0 and s[^1] != '\n': s.add '\n'
        continue
      if n.tag in skipTags: continue
      if isBlock and s.len > 0 and s[^1] != '\n': s.add '\n'
      if n.tag == "li": s.add "- "
      stack.add (n, true)
      for i in countdown(n.len - 1, 0): stack.add (n[i], false)
    else: discard

proc toText*(n: XmlNode): string =
  ## Readable text of an element: block elements become separate lines,
  ## runs of blank lines are squeezed, every line is trimmed.
  var raw = ""
  if n != nil: addText(n, raw)
  var lastBlank = true
  for line in raw.splitLines:
    let l = line.strip
    if l.len == 0:
      if not lastBlank: result.add "\n"
      lastBlank = true
    else:
      result.add l & "\n"
      lastBlank = false
  result = result.strip

const maxTags* = 15_000
  ## Real job pages have at most ~3,000 tags. The parser is recursive, and a
  ## hostile page of 50,000 nested tags crashed the whole process (a stack
  ## overflow can't be caught), so pages with more tags are refused.

proc tooManyTags(s: string): bool = s.count('<') > maxTags

proc htmlFragmentToText*(fragment: string): string =
  ## For JSON APIs that return the description as an HTML string.
  if '<' notin fragment: return fragment.strip
  if fragment.tooManyTags: return ""
  parseHtml("<div>" & fragment & "</div>").toText

proc pageTitle*(doc: XmlNode): string =
  let t = doc.findTag("title")
  if t != nil: t.innerText.strip else: ""

proc parsePage*(body: string): XmlNode =
  ## The parsed page, or an empty `<html>` when it has too many tags.
  if body.tooManyTags: newElement("html") else: parseHtml(body)
