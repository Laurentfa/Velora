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


def test_images_from_the_native_window_reach_the_scan(tmp_path) -> None:
    import sys

    import pytest
    if sys.platform != "darwin":
        pytest.skip("Continuité : macOS seulement")
    from mailia import native_menu as nm
    f = nm._inbox_dir() / "essai.png"
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "PNG")
    f.write_bytes(buf.getvalue())
    nm.inbox_push([str(f), str(tmp_path / "ailleurs.png")])  # un chemin hors du dossier de réception est ignoré
    (tmp_path / "ailleurs.png").write_bytes(b"x")
    assert nm.inbox_take() == [buf.getvalue()] and nm.inbox_take() == [] and (tmp_path / "ailleurs.png").exists()


def test_a_pdf_and_a_photo_become_png_pages() -> None:
    import sys

    import pytest
    if sys.platform != "darwin":
        pytest.skip("Continuité : macOS seulement")
    from AppKit import NSPasteboard, NSPasteboardTypePDF
    from Foundation import NSData

    from mailia import native_menu as nm
    pdf = io.BytesIO()
    Image.new("RGB", (300, 400), "red").save(pdf, "PDF", save_all=True, append_images=[Image.new("RGB", (300, 400), "blue")])
    pb = NSPasteboard.pasteboardWithUniqueName()
    pb.declareTypes_owner_([NSPasteboardTypePDF], None)
    pb.setData_forType_(NSData.dataWithBytes_length_(pdf.getvalue(), len(pdf.getvalue())), NSPasteboardTypePDF)
    pages = nm.pages_from_pasteboard(pb)
    assert len(pages) == 2 and all(Image.open(io.BytesIO(p)).format == "PNG" for p in pages)
