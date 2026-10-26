"""PDF 글자 읽기 테스트. CI 러너에는 poppler 와 tesseract 프로그램이 없으므로 변환·인식 함수를 가짜로 바꿔 확인한다.

이 테스트는 읽는 방식(쪽 수 제한, 오류 안내, 글자 합치기)만 지킨다. 서버에 poppler 가 실제로 설치되어 있는지는 확인하지 못하므로,
그것은 로컬 컨테이너와 staging 에서 같은 PDF 로 직접 확인한다.
"""
import pdf2image
import pdf2image.exceptions
import pytesseract
import pytest

from app import ocr


def test_pdf_converts_only_first_pages(monkeypatch):
    seen = {}

    def fake_convert(data, **kwargs):
        seen.update(kwargs)
        return ["page1", "page2", "page3"]

    monkeypatch.setattr(pdf2image, "convert_from_bytes", fake_convert)
    assert ocr.pdf_to_images(b"%PDF-1.4") == ["page1", "page2", "page3"]
    assert seen["first_page"] == 1
    assert seen["last_page"] == ocr.MAX_PDF_PAGES


def test_pdf_without_poppler_gives_clear_error(monkeypatch):
    def no_poppler(data, **kwargs):
        raise pdf2image.exceptions.PDFInfoNotInstalledError("pdfinfo 없음")

    monkeypatch.setattr(pdf2image, "convert_from_bytes", no_poppler)
    with pytest.raises(ocr.OcrError, match="서버에 없습니다"):
        ocr.pdf_to_images(b"%PDF-1.4")


def test_pdf_text_from_every_page_is_joined(monkeypatch):
    monkeypatch.setattr(ocr, "pdf_to_images", lambda data: ["page1", "page2"])
    monkeypatch.setattr(pytesseract, "get_languages", lambda config="": ["kor", "eng"])
    monkeypatch.setattr(pytesseract, "image_to_string", lambda image, **kwargs: f"  {image} 첫 줄  \n\n{image} 둘째 줄\n")
    assert ocr.read_text(b"%PDF-1.4") == "page1 첫 줄\npage1 둘째 줄\npage2 첫 줄\npage2 둘째 줄"
