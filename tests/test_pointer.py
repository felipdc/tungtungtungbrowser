import io
from types import SimpleNamespace
import unittest

from tungtungtungbrowser.pointer import NodeAncestry, PointerGeometryError, highlight_colors, highlight_point

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None


def capture(color, box=(20, 20, 80, 80), *, size=(100, 100), scale=1):
    image = Image.new('RGB', size, 'white')
    if box:
        ImageDraw.Draw(image).rectangle(box, fill=color)
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=85)
    return SimpleNamespace(jpeg=out.getvalue(), width=size[0], height=size[1], coordinate_scale=scale)


@unittest.skipIf(Image is None, 'Install the pointer extra for native image tests')
class PointerGeometryTests(unittest.TestCase):
    colors = ((230, 30, 170), (30, 220, 70))

    def test_jpeg_pair_locates_an_interior_and_converts_screen_scale(self):
        point = highlight_point([capture(c, scale=2) for c in self.colors], self.colors)
        self.assertAlmostEqual(point['x'], 25, delta=1)
        self.assertAlmostEqual(point['y'], 25, delta=1)
        self.assertEqual(point['screen_width'], 50)

    def test_unchanged_page_offscreen_and_unrelated_movement_provide_no_point(self):
        cases = [[capture(self.colors[0])] * 2,
                 [capture(c, box=None) for c in self.colors],
                 [capture(self.colors[0], (0, 0, 30, 30)), capture(self.colors[1], (60, 60, 90, 90))]]
        for images in cases:
            with self.assertRaises(PointerGeometryError):
                highlight_point(images, self.colors)

    def test_small_region_and_image_edge_do_not_authorize_contact(self):
        for box in [(20, 20, 27, 27), (0, 0, 8, 80)]:
            with self.assertRaises(PointerGeometryError):
                highlight_point([capture(c, box) for c in self.colors], self.colors)

    def test_geometry_mismatch_corrupt_image_and_missing_scale_are_rejected(self):
        for patch in [{'coordinate_scale': None}, {'coordinate_scale': float('nan')},
                      {'coordinate_scale': True}, {'width': 99}, {'jpeg': b'broken'}]:
            images = [capture(c) for c in self.colors]
            for key, value in patch.items():
                setattr(images[1], key, value)
            with self.assertRaises(PointerGeometryError):
                highlight_point(images, self.colors)

    def test_random_colors_cannot_both_match_an_unchanged_pixel(self):
        for _ in range(30):
            first, second = highlight_colors()
            self.assertGreater(max(abs(a - b) for a, b in zip(first, second)), 100)


class NodeAncestryTests(unittest.TestCase):
    def test_descendants_use_native_ids_and_removal_invalidates_the_path(self):
        tree = NodeAncestry()
        tree.add([{'nodeId': 1, 'children': [{'nodeId': 2}]}])
        tree.event('DOM.setChildNodes', {'parentId': 2, 'nodes': [{'nodeId': 3, 'children': [{'nodeId': 4}]}]})
        self.assertTrue(tree.contains(2, 4))
        self.assertFalse(tree.contains(3, 2))
        tree.event('DOM.childNodeRemoved', {'parentNodeId': 2, 'nodeId': 3})
        self.assertFalse(tree.contains(2, 4))
        self.assertFalse(tree.contains(3, 3))
        tree.event('DOM.documentUpdated', {})
        self.assertFalse(tree.contains(2, 2))

    def test_cycles_and_oversized_trees_do_not_authorize_a_match(self):
        tree = NodeAncestry()
        tree.parents = {2: 3, 3: 2}
        self.assertFalse(tree.contains(1, 2))
        tree.add([{'nodeId': i} for i in range(1, 32770)])
        self.assertFalse(tree.contains(2, 2))
