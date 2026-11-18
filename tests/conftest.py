from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
DOCX = RAIZ / "documentos" / "originales" / "FORMATO FINAL DE INSULINA.docx"


@pytest.fixture(scope="session")
def docx_real() -> Path:
    if not DOCX.exists():
        pytest.skip("falta el documento real de referencia")
    return DOCX
