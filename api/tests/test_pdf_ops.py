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


def test_existing_destination_and_source_are_never_overwritten(tmp_path):
    source = tmp_path / 'source.pdf'
    make_pdf(source, ['immutable'])
    original = source.read_bytes()
    with pytest.raises(PdfError, match='overwritten'):
        transform([source], source, 'rotate', {})
    existing = tmp_path / 'existing.pdf'
    existing.write_bytes(b'keep this output')
    with pytest.raises(PdfError, match='overwritten'):
        transform([source], existing, 'compress', {})
    assert existing.read_bytes() == b'keep this output'
    assert source.read_bytes() == original


def test_passwordless_encryption_is_rejected(tmp_path):
    import pikepdf
    plain, encrypted = tmp_path / 'source.pdf', tmp_path / 'encrypted.pdf'
    make_pdf(plain, ['restricted'])
    with pikepdf.open(plain) as pdf:
        pdf.save(encrypted, encryption=pikepdf.Encryption(owner='owner-secret', user=''))
    with pytest.raises(PdfError, match='encrypted'):
        inspect_pdf(encrypted)


def test_annotation_and_rotated_redaction_match_visible_coordinates(tmp_path):
    source, rotated = tmp_path / 'source.pdf', tmp_path / 'rotated.pdf'
    make_pdf(source, ['SECRET'])
    transform([source], rotated, 'rotate', {'degrees': 90})
    with fitz.open(rotated) as doc:
        rect = doc[0].search_for('SECRET')[0] * doc[0].rotation_matrix
    out = tmp_path / 'redacted.pdf'
    transform([rotated], out, 'redact', dict(page=1, x0=rect.x0-2, y0=rect.y0-2, x1=rect.x1+2, y1=rect.y1+2))
    assert 'SECRET' not in text(out)[0]
    annotated = tmp_path / 'note.pdf'
    transform([source], annotated, 'annotate', dict(page=1, x0=10, y0=10, x1=250, y1=50, text='Review this'))
    with fitz.open(annotated) as pdf:
        assert next(pdf[0].annots()).info['content'] == 'Review this'


def test_form_fields_survive_compress_rotate_merge_and_split(tmp_path):
    import pikepdf
    paths = []
    for index in range(2):
        path = tmp_path / f'form{index}.pdf'
        with fitz.open() as doc:
            page = doc.new_page()
            widget = fitz.Widget()
            widget.field_name = 'customer'
            widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            widget.field_value = f'value-{index}'
            widget.rect = fitz.Rect(40, 40, 250, 70)
            page.add_widget(widget)
            doc.save(path)
        paths.append(path)
    for operation in ['compress', 'rotate']:
        out = tmp_path / f'{operation}.pdf'
        transform([paths[0]], out, operation, {})
        with pikepdf.open(out) as pdf:
            assert str(pdf.Root.AcroForm.Fields[0].V) == 'value-0'
        with fitz.open(out) as pdf:
            assert list(pdf[0].widgets())[0].field_value == 'value-0'
    merged = tmp_path / 'forms-merged.pdf'
    transform(paths, merged, 'merge', {})
    with pikepdf.open(merged) as pdf:
        fields = pdf.Root.AcroForm.Fields
        assert len(fields) == 2
        assert len({str(f.T) for f in fields}) == 2
    split = tmp_path / 'forms-split.pdf'
    transform([merged], split, 'split', {'start': 2, 'end': 2})
    with fitz.open(split) as pdf:
        assert list(pdf[0].widgets())[0].field_value == 'value-1'


def test_nonfinite_rectangle_and_bad_order_are_rejected(tmp_path):
    source = tmp_path / 'source.pdf'
    make_pdf(source, ['one', 'two'])
    with pytest.raises(PdfError):
        transform([source], tmp_path / 'bad.pdf', 'redact', {'x0': float('nan'), 'y0': 0, 'x1': 30, 'y1': 30})
    with pytest.raises(PdfError):
        transform([source], tmp_path / 'bad-order.pdf', 'reorder', {'order': '1,1'})


def test_unavailable_office_is_recoverable(tmp_path, monkeypatch):
    from app.pdf_ops import office_to_pdf
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('libreoffice')
    monkeypatch.setattr('app.pdf_ops.subprocess.run', unavailable)
    with pytest.raises(PdfError, match='unavailable'):
        office_to_pdf(tmp_path / 'source.docx', tmp_path / 'out.pdf')
