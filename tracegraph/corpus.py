"""Deterministic ingestion and passage segmentation for Tier 1 source PDFs.

Every passage is addressable by ``document_id`` + 1-based PDF page + a
character span in the document's canonical text, so baselines and gold rows
can be compared on the same coordinates.
"""

from dataclasses import dataclass, field
from hashlib import sha256
import logging
from pathlib import Path
import re

from pypdf import PdfReader


# The RFA PDF has duplicate /Info keys; pypdf recovers but logs each one.
logging.getLogger("pypdf").setLevel(logging.ERROR)


TEXT_EXTRACTION_METHOD = "pypdf.extract_text+layout_blocks_v1"

# Gold-set document IDs for the Tier 1 anchor files. Other files get an ID
# derived from their name so that they can still be processed.
DOCUMENT_IDS = {
    "12.01.25.gflbselectiondocument1.pdf": "GFLB-SEL-2025-12-01",
    "01.03.23.rfa_updatednysupplement_0.pdf": "RFA-2023-01-03",
    "rwnyc-license-document.pdf": "NYSGC-RWNYC-LIC-2025-12-19",
}

_BULLET = re.compile(r"^(?:[•●▪◦\-–]\s*|\(?[a-zA-Z0-9]{1,3}[.)]\s+|\([ivxlc]+\)\s+)")
_TERMINAL = (".", ";", ":", "?", "!", "”", '"', ")")
_ABBREVIATIONS = re.compile(
    r"(?:\b(?:[A-Z]|No|Nos|Inc|Corp|Co|Ltd|LLC|U\.S|N\.Y|St|Mr|Ms|Dr|Jr|Sr|vs|e\.g|i\.e|etc|"
    r"approx|Sec|Art|p|pp|Ch|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.)$"
)
_SENTENCE_END = re.compile(r"[.!?][”\"')\]]?\s+(?=[“\"(\[]?[A-Z0-9])")


@dataclass
class Passage:
    passage_id: str
    document_id: str
    page: int  # 1-based PDF page where the passage starts
    end_page: int
    char_start: int  # span in Document.text
    char_end: int
    text: str
    block_id: int
    heading: str = ""


@dataclass
class Document:
    document_id: str
    path: str
    file_hash: str
    page_count: int
    text: str
    page_starts: list = field(default_factory=list)  # char offset of each page
    passages: list = field(default_factory=list)
    headings: list = field(default_factory=list)  # (char_offset, heading_text)

    def page_at(self, offset):
        page = 1
        for index, start in enumerate(self.page_starts):
            if start <= offset:
                page = index + 1
            else:
                break
        return page


def document_id_for(path):
    name = Path(path).name
    if name in DOCUMENT_IDS:
        return DOCUMENT_IDS[name]
    stem = re.sub(r"[^A-Za-z0-9]+", "-", Path(path).stem).strip("-")
    return stem.upper()


def file_sha256(path):
    digest = sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_space(text):
    return re.sub(r"\s+", " ", text).strip()


def _is_heading(line):
    if len(line) > 80 or line.endswith(_TERMINAL) or line.endswith(","):
        return False
    if re.fullmatch(r"\d{1,3}", line):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z’'&\-]*", line)
    if not words or len(words) > 10:
        return False
    capitalized = sum(1 for word in words if word[0].isupper() or word.lower() in
                      {"and", "of", "the", "for", "in", "to", "a", "on", "&"})
    return capitalized / len(words) >= 0.8


def _blocks(page_text):
    """Group raw PDF lines into layout blocks (paragraphs, list items, headings)."""
    lines = [line.strip() for line in page_text.replace("\r", "\n").split("\n")]
    blocks, current = [], []
    previous_heading = False
    for line in lines:
        if not line:
            if current:
                blocks.append(("text", current))
                current = []
            continue
        if re.fullmatch(r"\d{1,3}", line):  # page number
            continue
        heading = _is_heading(line)
        starts_item = bool(_BULLET.match(line))
        if heading:
            if current:
                blocks.append(("text", current))
                current = []
            blocks.append(("heading", [line]))
        else:
            if current and (starts_item or previous_heading):
                blocks.append(("text", current))
                current = []
            current.append(line)
        previous_heading = heading
    if current:
        blocks.append(("text", current))
    return blocks


def _join_lines(lines):
    text = ""
    for line in lines:
        if not text:
            text = line
        elif re.search(r"[a-z]-$", text) and line[:1].islower():
            text = text[:-1] + line  # re-join a hyphenated line break
        else:
            text += " " + line
    return normalize_space(text)


_CLAUSE_BREAK = re.compile(r";\s+|,\s+but\s+(?=[a-z])")
MIN_CLAUSE_WORDS = 6


def _split_clauses(text, start, end):
    """Split one sentence at '; ' and ', but ' when both sides are substantial."""
    spans, cursor = [], start
    for match in _CLAUSE_BREAK.finditer(text, start, end):
        left, right = text[cursor:match.start() + 1], text[match.end():end]
        if len(left.split()) >= MIN_CLAUSE_WORDS and len(right.split()) >= MIN_CLAUSE_WORDS:
            spans.append((cursor, match.start() + (1 if text[match.start()] == ";" else 0)))
            cursor = match.end()
    spans.append((cursor, end))
    return spans


def split_sentences(text, clauses=True):
    """Return (start, end) spans of sentences (and long clauses) in ``text``."""
    spans, start = [], 0
    for match in _SENTENCE_END.finditer(text):
        end = match.start() + 1
        while end < len(text) and text[end] in "”\"')]":
            end += 1
        if _ABBREVIATIONS.search(text[start:end]):
            continue
        if text[start:end].strip():
            spans.append((start, end))
        start = match.end()
    if text[start:].strip():
        spans.append((start, len(text.rstrip())))
    if not clauses:
        return spans
    return [part for s, e in spans for part in _split_clauses(text, s, e)]


def _page_blocks(reader):
    """All (page, kind, text) blocks, with sentences rejoined across page breaks."""
    blocks = []
    for page_number, page in enumerate(reader.pages, start=1):
        first_text = True
        for kind, lines in _blocks(page.extract_text() or ""):
            text = _join_lines(lines)
            if not text:
                continue
            if kind == "text" and first_text and blocks:
                previous = next((b for b in reversed(blocks) if b[1] == "text"), None)
                if (previous and previous[0] == page_number - 1 and text[:1].islower()
                        and not previous[2].endswith((".", ":", "?", "!"))):
                    previous[2] = _join_lines([previous[2], text])
                    previous[3] = page_number
                    first_text = False
                    continue
            if kind == "text":
                first_text = False
            blocks.append([page_number, kind, text, page_number])
    return blocks


def load_document(path):
    """Read one PDF into canonical text with page offsets and sentence passages."""
    path = Path(path)
    document_id = document_id_for(path)
    reader = PdfReader(str(path))
    pieces, page_starts, passages, headings = [], [], [], []
    offset, block_id = 0, 0
    current_heading = ""
    for page_number, kind, block, end_page in _page_blocks(reader):
        while len(page_starts) < page_number:
            page_starts.append(offset)
        block_start = offset
        pieces.append(block + "\n")
        offset += len(block) + 1
        block_id += 1
        if kind == "heading":
            current_heading = block
            headings.append((block_start, block))
            continue
        for s_start, s_end in split_sentences(block):
            sentence = block[s_start:s_end]
            passages.append(Passage(
                passage_id=f"{document_id}:p{page_number}:c{block_start + s_start}",
                document_id=document_id,
                page=page_number,
                end_page=end_page,
                char_start=block_start + s_start,
                char_end=block_start + s_end,
                text=sentence,
                block_id=block_id,
                heading=current_heading,
            ))
    while len(page_starts) < len(reader.pages):
        page_starts.append(offset)
    return Document(
        document_id=document_id,
        path=str(path),
        file_hash=file_sha256(path),
        page_count=len(reader.pages),
        text="".join(pieces),
        page_starts=page_starts,
        passages=passages,
        headings=headings,
    )


def load_corpus(directory):
    """Load every PDF in ``directory`` in a stable (sorted) order."""
    return [load_document(path) for path in sorted(Path(directory).glob("*.pdf"))]
