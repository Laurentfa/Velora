"""Scan : image reçue de l'iPhone (Continuité) convertie en page."""
import io

from PIL import Image

from mailia import scan


def test_any_image_is_turned_into_a_jpeg_page() -> None:
    buf = io.BytesIO()
    Image.new("RGBA", (40, 30), (255, 0, 0, 128)).save(buf, "PNG")
    out = scan.to_jpeg(buf.getvalue())
    assert out[:3] == b"\xff\xd8\xff" and Image.open(io.BytesIO(out)).size == (40, 30)
    assert scan.thumbnail(out)


def test_a_non_image_is_refused() -> None:
    import pytest
    with pytest.raises(Exception):
        scan.to_jpeg(b"%PDF-1.4 pas une image")
