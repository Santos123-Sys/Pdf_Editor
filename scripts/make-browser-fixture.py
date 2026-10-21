from pathlib import Path
import fitz

folder = Path(__file__).resolve().parents[1] / 'web/tests/fixtures'
folder.mkdir(parents=True, exist_ok=True)
with fitz.open() as pdf:
    pdf.new_page().insert_text((70,90), 'Browser page one')
    pdf.new_page().insert_text((70,90), 'Browser page two')
    pdf.save(folder / 'two-pages.pdf')
