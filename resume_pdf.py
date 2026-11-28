"""Export reviewed résumé text into the uploaded PDF, or an explicitly requested new layout."""
import ctypes
import io
import threading
from collections import Counter
from contextlib import closing
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader

MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_TEXT = 120_000
# PDFium is process-global and must not be called concurrently by Gunicorn threads.
_pdf_lock = threading.Lock()


class LayoutError(ValueError):
    """The approved text cannot be safely placed into the existing page objects."""


def _lines(text):
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def _read_text(data):
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or len(reader.pages) > 100:
        raise LayoutError("This PDF cannot be edited in its original layout.")
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _changed_run(old, new, document_text):
    """Find the changed words in a mixed-font or multi-column line, when unambiguous."""
    a, b = old.split(), new.split()
    while len(a) > 1 and len(b) > 1 and a[-1] == b[-1]:
        a.pop()
        b.pop()
    start = 0
    while start + 1 < min(len(a), len(b)) and a[start] == b[start]:
        start += 1
    part = " " + " ".join(a[start:]) + " "
    if document_text.count(part) == 1:
        return part, " " + " ".join(b[start:]) + " "
    return None


def _edit_page(page, changes, runs):
    import pypdfium2 as pdf
    import pypdfium2.raw as raw

    with closing(page.get_textpage()) as textpage:
        objects = list(page.get_objects(max_depth=1, textpage=textpage))
        bounds = [obj.get_bounds() for obj in objects]
        modified = False
        for i, obj in enumerate(objects):
            if not isinstance(obj, pdf.PdfTextObj):
                continue
            text = " ".join(obj.extract().split())
            old = text
            if old in changes:
                replacement = changes.pop(old)
            else:
                padded = f" {text} "
                matching = [line for line in changes if runs[line] and runs[line][0] in padded]
                if len(matching) != 1:
                    continue
                old = matching[0]
                replacement = padded.replace(*runs[old], 1).strip()
                del changes[old]
            matrix = obj.get_matrix()
            if matrix.b or matrix.c or matrix.a <= 0 or matrix.d <= 0:
                raise LayoutError("An edited line uses rotated or transformed text.")
            left, bottom, right, top = bounds[i]
            # Only extend into unused space on this same line, inside the page or column.
            right_limit = page.get_bbox()[2]
            for j, (x0, y0, x1, y1) in enumerate(bounds):
                if j != i and x1 > right and y0 < top and y1 > bottom and x0 >= left:
                    right_limit = min(right_limit, max(right, x0 - 1))
            encoded = replacement.encode("utf-16-le") + b"\x00\x00"
            wide = (ctypes.c_ushort * (len(encoded) // 2)).from_buffer_copy(encoded)
            if not raw.FPDFText_SetText(obj, wide):
                raise LayoutError("The original font cannot display the edited text.")
            if obj.get_bounds()[2] > right_limit + 0.25:
                raise LayoutError("An edited line needs more space than its original column.")
            modified = True
    if modified:
        page.gen_content()


def original_layout(source, edited_text):
    import pypdfium2 as pdf
    import pypdfium2.raw as raw

    if not source or len(source) > MAX_SOURCE_BYTES:
        raise LayoutError("An editable source PDF is unavailable.")
    try:
        before, after = _lines(_read_text(source)), _lines(edited_text)
        if before and " ".join(before) == " ".join(after):
            return source
        if len(before) != len(after) or not before:
            raise LayoutError("The edits change the number of lines in the original layout.")
        counts = Counter(before)
        changes = {a: b for a, b in zip(before, after) if a != b}
        if any(counts[line] != 1 for line in changes):
            raise LayoutError("An edited line occurs more than once in the original PDF.")
        document_text = " " + " ".join(before) + " "
        runs = {line: _changed_run(line, replacement, document_text) for line, replacement in changes.items()}
        with _pdf_lock, pdf.PdfDocument(source) as doc:
            if raw.FPDF_GetSignatureCount(doc):
                raise LayoutError("This PDF is digitally signed.")
            for index in range(len(doc)):
                if not changes:
                    break
                with closing(doc[index]) as page:
                    _edit_page(page, changes, runs)
            if changes:
                raise LayoutError("Some edited lines span multiple text objects or columns.")
            result = io.BytesIO()
            doc.save(result, flags=raw.FPDF_NO_INCREMENTAL)
        output = result.getvalue()
        if " ".join(_read_text(output).split()) != " ".join(edited_text.split()):
            raise LayoutError("The original PDF font or reading order cannot reproduce all the edited text.")
        return output
    except LayoutError:
        raise
    except Exception as exc:
        raise LayoutError("This PDF's layout could not be edited reliably.") from exc


def reflow(edited_text):
    """Typeset the reviewed text with embedded fonts and standard list markers."""
    import reportlab
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    fonts = Path(reportlab.__file__).parent / "fonts"
    with _pdf_lock:
        for name, file in (("ResumeBody", "Vera.ttf"), ("ResumeBold", "VeraBd.ttf")):
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(fonts / file)))
    body = ParagraphStyle("body", fontName="ResumeBody", fontSize=10, leading=14, spaceAfter=4)
    heading = ParagraphStyle("heading", parent=body, fontName="ResumeBold", fontSize=12,
                             leading=16, spaceBefore=6, keepWithNext=True)
    title = ParagraphStyle("title", parent=heading, fontSize=17, leading=22)
    story = []
    for i, line in enumerate(_lines(edited_text)):
        # Word's Symbol font stores its list bullet in a private-use slot.
        if line == "\uf0b7" or line.startswith("\uf0b7 "):
            line = "\u2022" + line[1:]
        style = title if i == 0 and len(line) < 70 else heading if line.isupper() and len(line) < 65 else body
        glyphs = pdfmetrics.getFont(style.fontName).face.charToGlyph
        for ch in line:
            if ord(ch) not in glyphs:
                raise ValueError(f"The standard PDF font cannot display U+{ord(ch):04X} on text line {i + 1}. "
                                 "Edit that character in the reviewed text or use the original file.")
        story.append(Paragraph(escape(line), style))
    output = io.BytesIO()
    SimpleDocTemplate(output, pagesize=A4, leftMargin=48, rightMargin=48,
                      topMargin=44, bottomMargin=44, title="Resume").build(story)
    return output.getvalue()
