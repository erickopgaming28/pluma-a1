"""Real, in-memory PDF fixtures keep these tests independent of PDF authoring packages."""

import unittest
from unittest.mock import patch

import numpy as np

from plotter import pdf_import


def make_pdf(pages):
    """Build a valid PDF with explicit vector content, page sizes and rotation."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    page_refs = []
    for width, height, contents, rotation in pages:
        number = len(objects) + 1
        page_refs.append(f"{number} 0 R")
        objects.append(
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
             f"/Rotate {rotation} /Resources << >> /Contents {number + 1} 0 R >>").encode()
        )
        content = contents.encode()
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Count {len(page_refs)} /Kids [{' '.join(page_refs)}] >>".encode()
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


class PdfImportTests(unittest.TestCase):
    def setUp(self):
        self.data = make_pdf([
            (72, 144, "1 0 0 rg 10 10 20 20 re f\n0 0 0 RG 1 w 10 80 m 60 80 l S", 0),
            (144, 72, "0 0 1 rg 10 10 20 20 re f", 0),
        ])

    def test_first_page_has_real_geometry_physical_size_and_rgb_colors(self):
        result = pdf_import.pdf_to_image(self.data)
        self.assertEqual((result['page'], result['pages']), (1, 2))
        self.assertAlmostEqual(result['width_mm'], 25.4)
        self.assertAlmostEqual(result['height_mm'], 50.8)
        image = result['image']
        self.assertEqual(image.shape, (360, 180, 3))
        self.assertEqual(image.dtype, np.uint8)
        # PDF coordinates start at bottom left: a filled red rectangle survives rendering.
        np.testing.assert_array_equal(image[310, 50], [255, 0, 0])
        np.testing.assert_array_equal(image[20, 20], [255, 255, 255])
        self.assertLess(image[159:162, 40:140].mean(), 200)
        self.assertTrue(any('página 1 de 2' in value for value in result['warnings']))

    def test_selected_page_uses_its_own_layout_and_color(self):
        result = pdf_import.pdf_to_image(self.data, ' 2 ')
        self.assertEqual(result['page'], 2)
        self.assertEqual(result['image'].shape, (180, 360, 3))
        self.assertAlmostEqual(result['width_mm'], 50.8)
        self.assertAlmostEqual(result['height_mm'], 25.4)
        np.testing.assert_array_equal(result['image'][130, 50], [0, 0, 255])

    def test_rotated_page_reports_visible_width_and_height(self):
        data = make_pdf([(72, 144, "0 0 0 rg 10 10 20 20 re f", 90)])
        result = pdf_import.pdf_to_image(data)
        self.assertAlmostEqual(result['width_mm'], 50.8)
        self.assertAlmostEqual(result['height_mm'], 25.4)
        self.assertEqual(result['image'].shape, (180, 360, 3))
        self.assertGreater(np.count_nonzero(result['image'][:, :, 0] < 200), 1000)

    def test_large_page_is_rendered_directly_with_bounded_resolution(self):
        data = make_pdf([(1000, 2000, "0 0 0 RG 2 w 100 100 m 900 1900 l S", 0)])
        result = pdf_import.pdf_to_image(data)
        self.assertEqual(result['image'].shape, (1600, 800, 3))
        self.assertAlmostEqual(result['height_mm'], 2000 * 25.4 / 72, places=5)
        self.assertTrue(any('1600' in value for value in result['warnings']))
        self.assertGreater(np.count_nonzero(result['image'][:, :, 0] < 200), 1000)

    def test_malformed_files_and_invalid_selection_are_rejected(self):
        for data in (b'', b'not a pdf', b'%PDF-1.7\nbroken'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                pdf_import.pdf_to_image(data)
        for page in (0, -1, True, 1.5, '1.5', 'x', '3', '9' * 4000):
            with self.subTest(page=str(page)[:20]), self.assertRaises(ValueError):
                pdf_import.pdf_to_image(self.data, page)

    def test_byte_and_page_limits_reject_real_input(self):
        oversized = b'%PDF-' + b' ' * pdf_import.MAX_PDF_BYTES
        with self.assertRaisesRegex(ValueError, '20 MB'):
            pdf_import.pdf_to_image(oversized)
        many_pages = make_pdf([(72, 72, '', 0)] * 201)
        with self.assertRaisesRegex(ValueError, '200 páginas'):
            pdf_import.pdf_to_image(many_pages)

    def test_unreasonable_page_size_and_pixel_limit_are_rejected_before_render(self):
        data = make_pdf([(14401, 72, '', 0)])
        with self.assertRaisesRegex(ValueError, 'dimensiones'):
            pdf_import.pdf_to_image(data)
        with patch.object(pdf_import, 'MAX_RENDER_PIXELS', 100):
            with self.assertRaisesRegex(ValueError, 'píxeles'):
                pdf_import.pdf_to_image(self.data)


if __name__ == '__main__':
    unittest.main()
