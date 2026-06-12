# app/services/ocr_service.py
import asyncio
import io
from typing import Optional

from app.core.exceptions import DocumentProcessingError
from app.core.logging import get_logger

logger = get_logger(__name__)


class OcrService:
    """
    Text extraction for all document types supported by the Business Brain.

    PDF  → PyMuPDF (fitz), page-by-page; falls back to Tesseract for image-only PDFs
    DOCX → python-docx
    XLSX → openpyxl (converts cells to tab-separated text)
    Images (PNG/JPG/WEBP/TIFF) → Tesseract via pytesseract
    TXT  → direct decode
    """

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def extract_text(self, file_bytes: bytes, filename: str) -> str:
        """
        Detect file type from extension and extract all text.
        Returns a single string suitable for chunking.
        """
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        dispatch = {
            "pdf":  self.extract_text_from_pdf,
            "docx": self.extract_text_from_docx,
            "doc":  self.extract_text_from_docx,
            "xlsx": self.extract_text_from_xlsx,
            "xls":  self.extract_text_from_xlsx,
            "csv":  self._extract_csv,
            "txt":  self._extract_txt,
            "md":   self._extract_txt,
            "png":  self.extract_text_from_image,
            "jpg":  self.extract_text_from_image,
            "jpeg": self.extract_text_from_image,
            "webp": self.extract_text_from_image,
            "tiff": self.extract_text_from_image,
            "tif":  self.extract_text_from_image,
        }

        handler = dispatch.get(ext)
        if handler is None:
            raise DocumentProcessingError(filename, f"Unsupported file type: .{ext}")

        try:
            return await handler(file_bytes)
        except DocumentProcessingError:
            raise
        except Exception as exc:
            raise DocumentProcessingError(filename, str(exc))

    async def extract_text_from_pdf(self, pdf_bytes: bytes) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._pdf_sync, pdf_bytes
        )

    async def extract_text_from_docx(self, docx_bytes: bytes) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._docx_sync, docx_bytes
        )

    async def extract_text_from_xlsx(self, xlsx_bytes: bytes) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._xlsx_sync, xlsx_bytes
        )

    async def extract_text_from_image(self, image_bytes: bytes) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._image_sync, image_bytes
        )

    async def _extract_txt(self, file_bytes: bytes) -> str:
        for enc in ("utf-8", "utf-16", "latin-1"):
            try:
                return file_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        return file_bytes.decode("utf-8", errors="replace")

    async def _extract_csv(self, file_bytes: bytes) -> str:
        import csv
        text = await self._extract_txt(file_bytes)
        lines = []
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            lines.append("\t".join(row))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Blocking sync implementations (run in executor)
    # ------------------------------------------------------------------

    def _pdf_sync(self, pdf_bytes: bytes) -> str:
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise DocumentProcessingError("file.pdf", "PyMuPDF not installed")

        pages: list[str] = []
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num, page in enumerate(doc):
            text = page.get_text("text")
            if text.strip():
                pages.append(text)
            else:
                # Image-only page — run OCR
                logger.info(f"PDF page {page_num} is image-only, running OCR")
                pix = page.get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
                ocr_text = self._image_sync(img_bytes)
                if ocr_text.strip():
                    pages.append(ocr_text)
        doc.close()

        result = "\n\n".join(pages)
        logger.info("PDF extracted", pages=len(pages), chars=len(result))
        return result

    def _docx_sync(self, docx_bytes: bytes) -> str:
        try:
            from docx import Document
        except ImportError:
            raise DocumentProcessingError("file.docx", "python-docx not installed")

        doc = Document(io.BytesIO(docx_bytes))
        parts: list[str] = []

        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text)

        for table in doc.tables:
            for row in table.rows:
                row_text = "\t".join(cell.text.strip() for cell in row.cells)
                if row_text.strip():
                    parts.append(row_text)

        result = "\n".join(parts)
        logger.info("DOCX extracted", paragraphs=len(doc.paragraphs), chars=len(result))
        return result

    def _xlsx_sync(self, xlsx_bytes: bytes) -> str:
        try:
            import openpyxl
        except ImportError:
            raise DocumentProcessingError("file.xlsx", "openpyxl not installed")

        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
        sheets: list[str] = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows: list[str] = [f"=== Sheet: {sheet_name} ==="]
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                if any(c.strip() for c in cells):
                    rows.append("\t".join(cells))
            if len(rows) > 1:
                sheets.append("\n".join(rows))

        wb.close()
        result = "\n\n".join(sheets)
        logger.info("XLSX extracted", sheets=len(sheets), chars=len(result))
        return result

    def _image_sync(self, image_bytes: bytes) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            raise DocumentProcessingError("image", "pytesseract or Pillow not installed")

        img = Image.open(io.BytesIO(image_bytes))

        # Pre-process: convert to greyscale for better OCR accuracy
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")

        # Try Amharic + English; fallback to English only
        try:
            text = pytesseract.image_to_string(img, lang="amh+eng")
        except pytesseract.TesseractError:
            text = pytesseract.image_to_string(img, lang="eng")

        result = text.strip()
        logger.info("Image OCR complete", chars=len(result))
        return result

    # ------------------------------------------------------------------
    # Receipt / structured extraction helper
    # ------------------------------------------------------------------

    def extract_receipt_data(self, text: str) -> dict:
        """
        Lightweight structured extraction from receipt text.
        Returns a best-effort dict; used by the Accountant agent.
        """
        import re

        lines = text.splitlines()
        data: dict = {"raw_text": text, "items": [], "total": None, "vendor": None, "date": None}

        # Vendor: usually the first non-empty line
        for line in lines:
            if line.strip():
                data["vendor"] = line.strip()
                break

        # Total: look for "total", "amount", "birr" patterns
        total_pattern = re.compile(
            r"(?:total|amount|grand total|birr)[^\d]*(\d[\d,\.]+)", re.IGNORECASE
        )
        for line in lines:
            m = total_pattern.search(line)
            if m:
                data["total"] = m.group(1).replace(",", "")
                break

        # Date: common Ethiopian and international date formats
        date_pattern = re.compile(
            r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{4}[\/\-\.]\d{2}[\/\-\.]\d{2})\b"
        )
        for line in lines:
            m = date_pattern.search(line)
            if m:
                data["date"] = m.group(1)
                break

        return data


ocr_service = OcrService()
