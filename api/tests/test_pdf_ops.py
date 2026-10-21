from pathlib import Path

import fitz
import pytest

from app.pdf_ops import PdfError, inspect_pdf, transform


def make_pdf(path: Path, labels: list[str]):
    doc = fitz.open()
    for label in labels:
        page = doc.new_page()
        page.insert_text((70, 90), label)
    doc.save(path)
    doc.close()


def text(path: Path) -> list[str]:
    with fitz.open(path) as doc:
        return [page.get_text() for page in doc]


def test_merge_split_reorder_preserve_originals(tmp_path):
    first, second = tmp_path / "first.pdf", tmp_path / "second.pdf"
    make_pdf(first, ["alpha", "bravo"])
    make_pdf(second, ["charlie"])
    original = first.read_bytes()
    merged = tmp_path / "merged.pdf"
    assert transform([first, second], merged, "merge", {})["pages"] == 3
    assert ["alpha", "bravo", "charlie"] == [s.strip() for s in text(merged)]
    split = tmp_path / "split.pdf"
    assert transform([merged], split, "split", {"start": 2, "end": 3})["pages"] == 2
    reordered = tmp_path / "reordered.pdf"
    transform([split], reordered, "reorder", {"order": "2,1"})
    assert ["charlie", "bravo"] == [s.strip() for s in text(reordered)]
    assert first.read_bytes() == original


def test_redact_removes_text_and_invalid_range_is_rejected(tmp_path):
    source, out = tmp_path / "source.pdf", tmp_path / "redacted.pdf"
    make_pdf(source, ["SECRET VALUE"])
    transform([source], out, "redact", {"page": 1, "x0": 60, "y0": 70, "x1": 230, "y1": 110})
    assert "SECRET" not in text(out)[0]
    assert "SECRET" in text(source)[0]
    with pytest.raises(PdfError):
        transform([source], tmp_path / "bad.pdf", "split", {"start": 2, "end": 3})


def test_rotate_watermark_and_compress(tmp_path):
    source = tmp_path / "source.pdf"
    make_pdf(source, ["hello"])
    rotated = tmp_path / "rotated.pdf"
    transform([source], rotated, "rotate", {"degrees": 90})
    with fitz.open(rotated) as pdf:
        assert pdf[0].rotation == 90
    marked = tmp_path / "marked.pdf"
    transform([source], marked, "watermark", {"text": "DRAFT"})
    assert inspect_pdf(marked)["pages"] == 1
    compressed = tmp_path / "compressed.pdf"
    transform([source], compressed, "compress", {})
    assert inspect_pdf(compressed)["pages"] == 1


def test_ocr_makes_a_scanned_page_searchable(tmp_path):
    printed = tmp_path / "printed.pdf"
    make_pdf(printed, ["SCAN SAMPLE 123"])
    with fitz.open(printed) as source:
        image = source[0].get_pixmap(matrix=fitz.Matrix(3, 3)).tobytes("png")
    scan = fitz.open()
    scan.new_page().insert_image(fitz.Rect(0, 0, 595, 842), stream=image)
    scanned = tmp_path / "scanned.pdf"
    scan.save(scanned)
    scan.close()
    assert "SCAN" not in text(scanned)[0]
    searchable = tmp_path / "searchable.pdf"
    transform([scanned], searchable, "ocr", {})
    assert "SCAN SAMPLE" in text(searchable)[0].upper()
