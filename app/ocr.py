"""사진·PDF 속 글자 읽기(OCR). 일정표나 공지 사진, PDF 공지문을 보내면 글자를 읽어 대화 입력창에 넣어 준다.

Python 라이브러리(pytesseract, pdf2image)는 requirements.txt 로 설치되지만, 실제로 글자를 읽는 tesseract 와
PDF 를 이미지로 바꾸는 poppler 는 운영체제에 따로 설치하는 프로그램이다. 노트북에는 직접 설치하고, 서버에서는 실행 환경(이미지)에 들어 있어야 한다.
"""
import io

from app.logs import log

MAX_BYTES = 5 * 1024 * 1024  # 파일 하나 최대 5MB
MAX_PDF_PAGES = 3  # PDF 는 앞 3쪽만 읽는다
PDF_DPI = 200
PDF_TIMEOUT = 30  # PDF 를 이미지로 바꾸는 데 쓸 수 있는 최대 초


class OcrError(Exception):
    pass


def pdf_to_images(data: bytes):
    from pdf2image import convert_from_bytes
    from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError, PDFPopplerTimeoutError, PDFSyntaxError

    try:
        return convert_from_bytes(data, dpi=PDF_DPI, first_page=1, last_page=MAX_PDF_PAGES, timeout=PDF_TIMEOUT)
    except PDFInfoNotInstalledError as error:
        log("ERROR", "poppler 프로그램을 찾을 수 없습니다. 실행 환경에 poppler 가 설치되어 있지 않습니다.")
        raise OcrError("PDF를 읽는 프로그램이 서버에 없습니다.") from error
    except PDFPopplerTimeoutError as error:
        raise OcrError("PDF를 읽는 데 시간이 너무 오래 걸립니다.") from error
    except (PDFPageCountError, PDFSyntaxError) as error:
        raise OcrError("PDF 파일을 읽을 수 없습니다.") from error


def read_text(data: bytes) -> str:
    import pytesseract
    from PIL import Image, UnidentifiedImageError

    if len(data) > MAX_BYTES:
        raise OcrError("파일이 5MB보다 큽니다.")
    if data.startswith(b"%PDF"):
        images = pdf_to_images(data)
    else:
        try:
            images = [Image.open(io.BytesIO(data))]
        except UnidentifiedImageError as error:
            raise OcrError("사진 파일을 읽을 수 없습니다.") from error
    try:
        langs = pytesseract.get_languages(config="")
        lang = "kor+eng" if "kor" in langs else "eng"
        if lang == "eng":
            log("WARNING", "tesseract 한국어 데이터가 없어 영어로만 읽습니다.")
        # --psm 6: 사진 전체를 글자 덩어리 하나로 본다. 기본값(자동)은 여백이 많은 공지 사진을 빈 페이지로 읽는 경우가 있다.
        pages = [pytesseract.image_to_string(image, lang=lang, config="--psm 6") for image in images]
    except pytesseract.TesseractNotFoundError as error:
        log("ERROR", "tesseract 프로그램을 찾을 수 없습니다. 실행 환경에 tesseract 가 설치되어 있지 않습니다.")
        raise OcrError("사진에서 글자를 읽는 프로그램이 서버에 없습니다.") from error
    text = "\n".join(line.strip() for page in pages for line in page.splitlines() if line.strip())
    log("INFO", "사진 글자 읽기", lang=lang, chars=len(text), pages=len(images))
    return text
