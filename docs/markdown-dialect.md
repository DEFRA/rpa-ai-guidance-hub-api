# The Markdown dialect a parsed guide is written in

This is for whoever is building the renderer in the prototype kit, without a
real converted guide in front of them. It describes exactly what
`app/guidance/parsing` (this repo) can put in a `content.md`, so the renderer
can be built and tested against synthetic examples that are guaranteed to
match what a real guide produces — rather than a guess at "some Markdown".

The source of truth is the code, not this file: `app/guidance/parsing/*.py`,
each module named for the one thing it renders (`inline.py` for a run of
text, `lists.py`, `tables.py`, `colours.py`, `images.py`, `anchors.py`,
`borders.py`, `textboxes.py`), and `parser.py` for how they're assembled into
sections. If something here and the code disagree, the code is right and
this file is stale.

Nothing described here needs anything beyond a standard **GFM (GitHub
Flavored Markdown) parser with raw inline HTML allowed**, plus one
non-standard extension (Pandoc-style bracketed spans, for colour — see
below). If you're using `markdown-it` (or similar), that's `html: true` plus
a bracketed-span plugin, or a small post/pre-processing step for the one
custom syntax.

## Document shape

A guide is one Markdown file:

```
# {title}

{section 1}

{section 2}

...
```

Sections are a **flat list in document order**, not literally nested text —
nesting is expressed only through heading depth, exactly like any ordinary
Markdown document with h1/h2/h3s. A section's heading is:

```
{"#" repeated (level + 1) times} {rendered heading}
```

`level` is **relative**, not Word's own absolute heading level: whatever the
first heading in the document is styled as (Heading 1, Heading 2, whatever),
it is level 1 and renders as `##` — `#` is reserved for the document title,
so a top-level section is always two hashes, never one. A heading nested one
further is level 2 (`###`), and so on. A heading that skips a Word level
(Heading 1 straight to Heading 3) still only nests one deep in the output —
there is no gap in the rendered numbers or the hash count.

`{rendered heading}` is:

- **`{number} {heading text}`** for an ordinary section — `## 1 Overview`,
  `### 1.1 Eligibility`, `#### 1.1.2 Evidence required`. The number is
  derived (each section's ordinal among its siblings, joined with the
  parent's), never read from Word — Word doesn't store it as text at all.
- **just `{heading text}`, unnumbered**, for a section whose Word *style*
  says it's an appendix/annex/schedule (matched by style name, nothing else)
  — `## Annex A`. The author already wrote "Annex A" as the heading text
  themselves; nothing is prefixed onto it. A *sub*-section underneath an
  appendix that isn't itself styled as one — the ordinary "Detail" heading
  under "Annex A" — **does** get a derived number, using the appendix's
  letter as its root: `### A.1 Detail`. Appendices are lettered A, B, C, …
  Z, AA, AB, … — counted separately from numbered sections, so an appendix
  after section 7 is "A", not "8".

## Anchors (for internal links / a table of contents)

A cross-reference in the source document (Word's "see section 3.2") is
resolved to a Markdown link at parse time: `[see section 3.2](#anchor)`. The
anchor is **GitHub's own heading-slug algorithm**, applied to the exact
rendered heading text (number included, for a numbered section):

1. Lowercase the whole rendered heading.
2. Drop every character that isn't a word character, a space, or a hyphen
   (so punctuation — the `.` in "3.2", a colon, a comma — is *removed*, not
   replaced: `## 1.1 Overview` → `11-overview`, not `1-1-overview`).
3. Replace spaces with hyphens.
4. If two headings render to the same slug (this only really happens with
   unnumbered appendix headings), the second is suffixed `-1`, the third
   `-2`, in document order.

If your renderer generates its own heading ids (e.g. `markdown-it-anchor`),
**it has to use this exact algorithm**, or a link like `[...](#31-overview)`
in the Markdown won't find the id your renderer generated for `### 3.1
Overview`. `app/guidance/parsing/anchors.py` is ~20 lines and is the
authoritative version if you want to port it directly rather than
re-implement it from this description.

A link to something outside the document (`https://example.com/...`) is an
ordinary Markdown link and needs nothing special — except that a target
containing whitespace, or unbalanced parentheses, is wrapped in angle
brackets: `[label](<file:///some path/with spaces.pdf>)`.

## Inline formatting

All plain text has already been escaped by the parser — `&`, `<`, `>` become
entities (`&amp;`, `&lt;`, `&gt;`), and any of `` \ ` * _ [ ] ~ `` the author
literally typed is backslash-escaped. **The only raw HTML that can ever
appear is `<u>`, `<sup>`, `<sub>` (and `<br>` inside table cells — see
below)** — nothing else, ever, because everything else went through that
escape. If your renderer's sanitizer allow-lists exactly those tags (and
strips/escapes anything else), it can safely turn `html: true` on.

| What Word had | What's in the Markdown |
|---|---|
| Bold | `**text**` |
| Italic | `*text*` |
| Bold *and* italic | `***text***` |
| Strikethrough | `~~text~~` |
| Underline | `<u>text</u>` |
| Superscript | `<sup>text</sup>` |
| Subscript | `<sub>text</sub>` |
| A link | `[text](url)` |
| Coloured text — red or blue only | `[text]{.red}` / `[text]{.blue}` |
| A hard line break (Shift+Enter in Word) | `text\` then a newline |

**Colour is the one non-standard bit**: `[text]{.red}` is a
[Pandoc-style bracketed span](https://pandoc.org/MANUAL.html#extension-bracketed_spans) —
not GFM, not CommonMark. Only two colour names ever appear, `red` and
`blue` (never a hex code, never a CSS class) — the whole of the palette this
convention uses. Nothing built-in to most JS Markdown parsers understands
this; you'll want either a small plugin or a regex pass that turns
`\[([^\]]*)\]\{\.(red|blue)\}` into a `<span class="...">`. Note the
contents of the span are themselves Markdown (can hold a bold word, a link,
etc.) and need to be re-parsed as such, not dropped in as literal text.

Marks nest in this order, innermost first: superscript/subscript, then
strikethrough, then underline, then italic, then bold, then colour, then
link. So a bold, red, linked phrase renders as
`[[**text**]{.red}](https://example.com)`.

Images are always `![](name)` with **no alt text, ever, on purpose** —
see the Assets section below for what `name` is and where it resolves to.

## Lists

Standard Markdown bullets (`- item`) and ordered lists (`1. item`), properly
nested by indentation — a nested item's text lines up under its parent's
first line of content, the normal CommonMark rule. A few things worth
knowing rather than being surprised by:

- **Lists are tight** (no blank line between items) *except*: if a bulleted
  list is immediately followed by a numbered list (or vice versa) **at the
  outermost level**, there's a blank line between them, because that's what
  the guidance editor itself would write, and matching it means a converted
  document isn't silently rewritten the first time someone opens and saves
  it in the editor. The same kind-change happening at a *nested* depth gets
  no blank line — CommonMark still reads it as two separate lists either
  way (a marker-kind change always ends a list), so this shouldn't need any
  special handling in a standards-compliant parser.
- Unbulleted prose that introduces a sub-list (an author typed a lead-in
  line, then indented a bulleted list under it, without bulleting the
  lead-in itself) is rendered as a **continuation paragraph of the item
  above it** — i.e. it's inside that list item's own content, at the same
  indent as the item's text, not a separate top-level paragraph. If your
  renderer treats it as a lazy-continuation line under the enclosing list
  item (standard CommonMark), this is already correct.
- A picture can be an item's content, exactly like a paragraph.

## Tables

An ordinary Word table (more than one cell) becomes a standard GFM pipe
table:

```
| Scheme                  | Minimum area | Payment window |
| ------------------------ | ------------ | --------------- |
| Basic Payment Scheme     | 5 hectares   | 6 weeks          |
| Countryside Stewardship  | None         | 12 weeks         |
```

Columns are padded to a consistent width (cosmetic — a compliant GFM parser
doesn't care, this is only there so the raw source is pleasant to diff).
Two things a cell can do that need a little care:

- **A cell can't contain a real newline** (a pipe-table row is one line), so
  a cell with more than one paragraph, or a list, or a hard line break, has
  those joined with a literal `<br>` instead. A list inside a cell
  therefore loses real list structure — it comes through as `<br>`-joined
  text with a leading `-`/`1.`-looking prefix that is **not** a real list
  marker as far as the surrounding table is concerned, just text. This is a
  known, accepted limitation of the format, not a bug to work around.
- A literal `|` the author typed inside a cell is escaped as `\|`.

## Boxes / callouts

Word has three different ways of drawing "a box round some text" — a
one-cell table, a text box, and a run of paragraphs the author bordered
directly — and **all three mean the same thing to a reader and all three
render identically**, as a Markdown blockquote:

```
> Dear [claimant name]
>
> We are writing to confirm receipt of your claim, reference [ref number].
>
> - Evidence has been received
> - Evidence is outstanding
```

A blockquote can hold several paragraphs (separated by a bare `>` line, as
above) and can hold a list, exactly like any other block content — it's
rendered through the exact same block-rendering path as the rest of a
section.

## Assets (images)

An image is written `![](name)` where `name` is **the SHA-256 digest of the
picture's own bytes, plus the original file extension** — e.g.
`a3f9c1...e2.png`. Content-addressed, deliberately, so:

- the same picture used twice in a document is one name, written once;
- re-parsing a document that keeps a picture never renames or duplicates it
  (which is also why versioning is safe — see
  `docs/guidance-document-storage.md`);
- there is no relationship between a picture's name and where it sits in
  the document, so re-ordering sections never touches an image reference.

**Alt text is always empty**, deliberately — Word's own auto-generated
`alt` guess is indistinguishable from one a person actually wrote, and
publishing a machine's guess as authored accessibility text is worse than
an honest gap an editor can fill in by hand later. A renderer should expect
`![]()` (no alt text) as normal, not a bug in the source.

What `name` resolves to depends entirely on the URL prefix whoever wrote the
file passed at render time — `content.md` itself only ever names the bare
file, never a full URL. See `docs/guidance-document-storage.md` for the
actual storage layout (`<document id>/assets/<name>`, shared across every
version of a document) and `scripts/parse_docx_for_s3.py`'s `manifest.json`
for how a particular document/version/asset id ties back together for the
prototype's manual-S3-sync workflow.

## A worked example

Synthetic, exercising most of the above in one document — safe to save as a
`.md` file and point a renderer at directly:

````markdown
# Claims Guide

## 1 Overview

This guide explains how to process a claim under the
**Basic Payment Scheme**. See [eligibility](#11-eligibility) before you
begin, and [payment windows](#a1-payment-windows) in the annex.

### 1.1 Eligibility

A claim is eligible where all of the following apply:

- the claimant holds a valid [SBI]
- the land meets the *minimum area* — see the table below
- no [linked case]{.red} is open against the claimant

| Scheme                  | Minimum area | Payment window |
| ------------------------ | ------------ | --------------- |
| Basic Payment Scheme     | 5 hectares   | 6 weeks          |
| Countryside Stewardship  | None         | 12 weeks         |

> Dear [claimant name]
>
> We are writing to confirm receipt of your claim, reference [ref number].

![](a3f9c157e2b8d4f0a1c3e5b7d9f1a2c4e6b8d0f2a4c6e8b0d2f4a6c8e0b2d4f6.png)

## 2 Escalation

Where a case cannot be resolved within the normal window, escalate as
follows:

1. Confirm the evidence is complete.
2. Raise a note on the case, marked <u>urgent</u>.
3. Notify the team lead<sup>1</sup>.

## Annex A

### A.1 Payment windows

The standard payment window is six weeks from a complete claim.
````

## Explicitly out of scope — don't build support for these

- Fenced code blocks, inline code spans, thematic breaks (`---` as a rule) —
  the parser never emits any of these.
- Any HTML tag other than `<u>`, `<sup>`, `<sub>`, `<br>`.
- A colour other than `red`/`blue`, or colour expressed as a hex/CSS value.
- Real nested lists or multi-paragraph content inside a table cell — only
  `<br>`-joined text, as above.
- Alt text on an image.
- Footnote syntax, definition lists, task-list checkboxes — none of these
  exist in the source Word documents this parser is built against, and
  nothing generates them.
