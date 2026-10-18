"""Plain-text extraction for stored resume files.

This is the internal service that turns a stored ``.pdf`` or ``.docx`` resume
into normalised plain text. It is deliberately not an API endpoint: the caller
passes a filesystem path that the storage layer has already created, and gets
text back for later analysis.

Only the text a document actually carries is read. A PDF that contains scans
or photos of text has no text layer and therefore yields no extractable text;
optical character recognition is out of scope. The output is plain text only:
layout, styling, images and hyperlink targets are discarded.

Known DOCX limitations, matching what python-docx exposes:

* Body paragraphs and *top-level* tables are read, in document order.
* Headers, footers, footnotes, comments and text boxes are not read.
* Tables nested inside a table cell are not read; cell text stops at the
  first level.
* The legacy binary ``.doc`` format is not supported at all.

Failures are reported through the exception hierarchy defined here rather than
raw library errors, and every message is safe to show to a user: none of them
contain filesystem paths or internal details.
"""

import zipfile
from pathlib import Path

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PasswordType, PdfReader
from pypdf.errors import DependencyError, PdfReadError

# Text from two PDF pages is separated by a blank line, matching how
# paragraphs inside a DOCX are separated.
PAGE_SEPARATOR = "\n\n"
BLOCK_SEPARATOR = "\n"
CELL_SEPARATOR = "\t"


class TextExtractionError(Exception):
    """Base class for expected extraction failures.

    Messages are safe to show to a user, so callers never need to inspect or
    re-word them.
    """


class UnsupportedFormatError(TextExtractionError):
    """The file extension is not one this service can read."""


class UnreadableDocumentError(TextExtractionError):
    """The file cannot be parsed, or cannot be opened, so no text is available."""


class NoExtractableTextError(TextExtractionError):
    """The document was parsed successfully but holds no non-whitespace text."""


def extract_resume_text(path: Path) -> str:
    """Return the normalised plain text of the resume stored at ``path``.

    The format is chosen from the file extension, which the upload layer has
    already validated and stored. Raises the appropriate
    :class:`TextExtractionError` when the extension is unsupported, the
    document cannot be read, or the document contains no text.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        raw_text = _extract_pdf_text(path)
    elif suffix == ".docx":
        raw_text = _extract_docx_text(path)
    else:
        raise UnsupportedFormatError(
            "Unsupported file type. Allowed extensions: .pdf, .docx."
        )

    text = normalize_text(raw_text)
    if not text:
        raise NoExtractableTextError("The document contains no extractable text.")
    return text


def normalize_text(raw_text: str) -> str:
    """Reduce library-specific text output to a stable, comparable form.

    The rules are deliberately small and predictable:

    1. Line endings are normalised to ``\n``.
    2. Each line is stripped of surrounding whitespace. PDF extraction in
       particular pads lines with spaces taken from the page layout.
    3. Runs of blank lines collapse to a single blank line, which keeps
       paragraph breaks without keeping pages of empty space.
    4. The result is stripped at the ends.

    These rules never remove content: the result is empty exactly when the
    input contains nothing but whitespace.
    """
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    collapsed: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped and collapsed and not collapsed[-1]:
            continue
        collapsed.append(stripped)
    return "\n".join(collapsed).strip()


def _extract_pdf_text(path: Path) -> str:
    """Read every page of a PDF and join the page texts with a blank line."""
    try:
        # Every read happens inside the ``with`` block: pypdf reads lazily from
        # the file object and would fail on a handle that has been closed.
        with path.open("rb") as handle:
            reader = PdfReader(handle)
            if (
                reader.is_encrypted
                and reader.decrypt("") == PasswordType.NOT_DECRYPTED
            ):
                # Encrypted with a real password. It can never be guessed, so
                # the file is reported as unreadable rather than silently
                # returning no text.
                raise UnreadableDocumentError(
                    "The PDF is password-protected, so its text cannot be extracted."
                )
            return PAGE_SEPARATOR.join(
                page.extract_text() for page in reader.pages
            )
    except OSError:
        # Missing file, permission problem, or a similar I/O failure.
        raise UnreadableDocumentError("The PDF file could not be read.") from None
    except DependencyError:
        # pypdf needs its optional cryptography extra to decrypt some PDFs
        # (for example AES-encrypted ones). It is not installed, so those
        # documents cannot be read here.
        raise UnreadableDocumentError(
            "The PDF is encrypted in a way that is not supported."
        ) from None
    except PdfReadError:
        # pypdf's read-error family: empty files, truncated or malformed
        # documents, and streams that could not be decrypted.
        raise UnreadableDocumentError("The PDF file could not be read.") from None


def _extract_docx_text(path: Path) -> str:
    """Read body paragraphs and top-level tables in document order."""
    try:
        with path.open("rb") as handle:
            document = Document(handle)
            blocks = [
                _block_text(block) for block in document.iter_inner_content()
            ]
    except OSError:
        raise UnreadableDocumentError(
            "The DOCX file could not be read."
        ) from None
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, SyntaxError):
        # python-docx and its XML parser surface a structurally broken package
        # as a bad ZIP container, a missing part or relationship, or malformed
        # XML. SyntaxError covers both lxml's XMLSyntaxError and the standard
        # library's ParseError, so corrupt XML never escapes as a raw error.
        raise UnreadableDocumentError(
            "The DOCX file could not be read."
        ) from None
    return BLOCK_SEPARATOR.join(blocks)


def _block_text(block: Paragraph | Table) -> str:
    """Render one top-level document block as plain text."""
    if isinstance(block, Paragraph):
        return block.text
    if isinstance(block, Table):
        # Keep one line per row and separate cells with tabs, which preserves
        # the table's structure in a plain-text form.
        return BLOCK_SEPARATOR.join(
            CELL_SEPARATOR.join(cell.text for cell in row.cells)
            for row in block.rows
        )
    # python-docx may expose other block types in future versions; ignoring
    # them is safer than guessing at a text representation.
    return ""
