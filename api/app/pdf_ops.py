"""All PDF transforms read a source and write a separate destination."""
from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

import fitz
import pikepdf
from pikepdf import Pdf
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas


class PdfError(ValueError):
    pass


def inspect_pdf(source: Path) -> dict:
    try:
        with Pdf.open(source) as pdf:
            root = pdf.Root
            return {
                "pages": len(pdf.pages),
                "encrypted": bool(pdf.is_encrypted),
                "forms": "/AcroForm" in root,
                "signatures": "/AcroForm" in root and "/SigFlags" in root.AcroForm,
                "warnings": [
                    "Fonts or appearance may change during conversion or annotation.",
                    "Existing forms and signatures may be invalidated by edits."
                ],
            }
    except pikepdf.PasswordError as exc:
        raise PdfError("This PDF is encrypted. Unlock it before editing.") from exc
    except pikepdf.PdfError as exc:
        raise PdfError("The file is not a readable PDF.") from exc


def transform(sources: list[Path], destination: Path, operation: str, options: dict) -> dict:
    if not sources:
        raise PdfError("Select at least one PDF.")
    if operation == "ocr":
        with tempfile.TemporaryDirectory() as temp:
            doc = fitz.open(sources[0])
            result = fitz.open()
            for index, page in enumerate(doc):
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                image_path = Path(temp) / f"page-{index}.png"
                pdf_path = Path(temp) / f"page-{index}"
                image_path.write_bytes(pix.tobytes("png"))
                try:
                    subprocess.run(["tesseract", str(image_path), str(pdf_path), "pdf"], check=True, timeout=120, capture_output=True)
                except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    raise PdfError("OCR is unavailable or timed out. Check that Tesseract is installed.") from exc
                with fitz.open(str(pdf_path) + ".pdf") as ocr_page:
                    result.insert_pdf(ocr_page)
            result.save(destination)
            result.close()
            doc.close()
    elif operation in {"redact", "annotate"}:
        doc = fitz.open(sources[0])
        page_number = int(options.get("page", 1)) - 1
        if not 0 <= page_number < len(doc):
            raise PdfError("Page number is out of range.")
        page = doc[page_number]
        rect = fitz.Rect(*[float(options.get(k, 0)) for k in ("x0", "y0", "x1", "y1")])
        if rect.is_empty or rect.is_infinite or not page.rect.contains(rect):
            raise PdfError("Choose a rectangle within the page.")
        if operation == "redact":
            page.add_redact_annot(rect, fill=(0, 0, 0))
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
            doc.save(destination, garbage=4, deflate=True)
        else:
            text = str(options.get("text", ""))[:500]
            if not text:
                raise PdfError("Annotation text is required.")
            annot = page.add_freetext_annot(rect, text, fontsize=12)
            annot.update()
            doc.save(destination)
        doc.close()
    else:
        try:
            with Pdf.new() as output:
                if operation == "merge":
                    for source in sources:
                        with Pdf.open(source) as pdf:
                            output.pages.extend(pdf.pages)
                else:
                    with Pdf.open(sources[0]) as pdf:
                        count = len(pdf.pages)
                        pages = list(range(count))
                        if operation == "split":
                            start = int(options.get("start", 1))
                            end = int(options.get("end", count))
                            if start < 1 or end > count or start > end:
                                raise PdfError("Invalid page range.")
                            pages = list(range(start - 1, end))
                        elif operation == "reorder":
                            raw = str(options.get("order", ""))
                            pages = [int(v.strip()) - 1 for v in raw.split(",") if v.strip()]
                            if sorted(pages) != list(range(count)):
                                raise PdfError("Order must list each page exactly once.")
                        output.pages.extend(pdf.pages[i] for i in pages)
                        if operation == "rotate":
                            degrees = int(options.get("degrees", 90))
                            if degrees not in {90, 180, 270}:
                                raise PdfError("Rotation must be 90, 180, or 270 degrees.")
                            for page in output.pages:
                                page.Rotate = (int(page.get("/Rotate", 0)) + degrees) % 360
                        elif operation == "watermark":
                            text = str(options.get("text", ""))[:100]
                            if not text:
                                raise PdfError("Watermark text is required.")
                            for page in output.pages:
                                box = page.MediaBox
                                width, height = float(box[2]) - float(box[0]), float(box[3]) - float(box[1])
                                buffer = io.BytesIO()
                                c = canvas.Canvas(buffer, pagesize=(width, height))
                                c.setFillColor(HexColor("#64748b"))
                                c.setFillAlpha(0.25)
                                c.setFont("Helvetica-Bold", 40)
                                c.translate(width / 2, height / 2)
                                c.rotate(35)
                                c.drawCentredString(0, 0, text)
                                c.save()
                                buffer.seek(0)
                                with Pdf.open(buffer) as overlay:
                                    page.add_overlay(overlay.pages[0])
                        elif operation not in {"split", "reorder", "rotate", "compress"}:
                            raise PdfError("Unsupported operation.")
                output.save(destination, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
        except (pikepdf.PdfError, pikepdf.PasswordError) as exc:
            raise PdfError("The PDF could not be processed. It may be damaged or encrypted.") from exc
    return inspect_pdf(destination)


def office_to_pdf(source: Path, destination: Path) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        try:
            subprocess.run([
                "libreoffice", "-env:UserInstallation=file:///tmp/lo-pdf-editor",
                "--headless", "--convert-to", "pdf", "--outdir", temp, str(source)
            ], check=True, timeout=120, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise PdfError("Office conversion is unavailable or timed out. Check LibreOffice and the file format.") from exc
        made = Path(temp) / (source.stem + ".pdf")
        if not made.exists():
            raise PdfError("Office conversion did not produce a PDF.")
        destination.write_bytes(made.read_bytes())
    return inspect_pdf(destination)
