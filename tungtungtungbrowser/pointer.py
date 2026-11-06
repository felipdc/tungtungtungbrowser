"""Native Inspector overlay geometry and node ancestry for experimental clicks.

The overlays belong to Web Inspector, not the inspected document. Image matching
locates a candidate; the native element picker must still verify its DOM target.
"""
import io
import math
import secrets


class PointerGeometryError(ValueError):
    pass


def highlight_colors():
    first = tuple(20 + secrets.randbelow(216) for _ in range(3))
    while True:
        second = tuple(20 + secrets.randbelow(216) for _ in range(3))
        # An unchanged pixel cannot match both colors within JPEG tolerance.
        if max(abs(a - b) for a, b in zip(first, second)) > 100:
            return first, second


def highlight_point(images, colors):
    """Find a six-point interior in two corresponding opaque native overlays.

    Requires native JPEGs with screen-point scale. A QMP image has no native
    coordinate scale and cannot authorize input. No screenshot is retained here.
    """
    try:
        from PIL import Image, ImageChops, ImageFilter, ImageOps
    except ImportError as error:
        raise PointerGeometryError('Selector clicks require the pointer extra: pip install .[pointer]') from error
    if len(images) != 2 or len(colors) != 2:
        raise PointerGeometryError('Two native highlight captures are required')
    if (any(len(color) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in color)
            for color in colors)
            or max(abs(a - b) for a, b in zip(*colors)) <= 40):
        raise PointerGeometryError('Native highlight colors are not distinct')
    geometry = None
    masks = []
    for image, color in zip(images, colors):
        width, height, scale = image.width, image.height, image.coordinate_scale
        if (type(width) is not int or type(height) is not int or width < 1 or height < 1
                or width * height > 16_000_000 or isinstance(scale, bool)
                or not isinstance(scale, (int, float)) or not math.isfinite(scale)
                or not 1 <= scale <= 4 or not isinstance(getattr(image, 'jpeg', None), bytes)
                or len(image.jpeg) > 32 * 1024**2):
            raise PointerGeometryError('Unsupported native screenshot geometry')
        current = (width, height, scale)
        if geometry is not None and geometry != current:
            raise PointerGeometryError('Screen geometry changed between highlights; input not sent')
        geometry = current
        try:
            with Image.open(io.BytesIO(image.jpeg)) as decoded:
                if decoded.format != 'JPEG' or decoded.size != (width, height):
                    raise PointerGeometryError('Native image dimensions or format disagree')
                rgb = decoded.convert('RGB')
        except (OSError, ValueError) as error:
            raise PointerGeometryError('Cannot decode native highlight capture') from error
        diff = ImageChops.difference(rgb, Image.new('RGB', rgb.size, color))
        channels = [channel.point(lambda value: 255 if value <= 20 else 0)
                    for channel in diff.split()]
        masks.append(ImageChops.darker(ImageChops.darker(channels[0], channels[1]), channels[2]))
    mask = ImageChops.darker(*masks)
    box = mask.getbbox()
    if box is None:
        raise PointerGeometryError('Selector has no verified visible highlight; input not sent')
    radius = math.ceil(6 * scale)
    # Padding keeps an image edge from being mistaken for an interior. Six
    # points contain the qualified five-point native contact plus a margin.
    cropped = ImageOps.expand(mask.crop(box), border=radius, fill=0)
    cropped = cropped.filter(ImageFilter.MinFilter(2 * radius + 1))
    cropped = cropped.crop((radius, radius, cropped.width - radius, cropped.height - radius))
    valid = cropped.getbbox()
    if valid is None:
        raise PointerGeometryError('Visible selector region is too small for a native contact')
    x, y = (valid[0] + valid[2]) // 2, (valid[1] + valid[3]) // 2
    if not cropped.getpixel((x, y)):
        y, x = divmod(cropped.tobytes().index(255), cropped.width)
    return {'x': (box[0] + x) / scale, 'y': (box[1] + y) / scale,
            'screen_width': width / scale, 'screen_height': height / scale,
            'coordinate_scale': scale}


class NodeAncestry:
    """Bounded ancestry from native DOM replies, never labels or HTML strings."""
    def __init__(self):
        self.parents = {}
        self.removed = set()
        self.valid = True

    def add(self, nodes, parent=None):
        pending = [(node, parent) for node in nodes]
        while pending and self.valid:
            node, owner = pending.pop()
            identifier = node.get('nodeId')
            if type(identifier) is not int or identifier < 1:
                self.valid = False
                return
            self.parents[identifier] = owner
            self.removed.discard(identifier)
            if len(self.parents) > 32768:
                self.valid = False
                self.parents.clear()
                return
            pending.extend((child, identifier) for child in node.get('children', []))

    def event(self, method, params):
        if method == 'DOM.setChildNodes':
            self.add(params['nodes'], params['parentId'])
        elif method == 'DOM.childNodeInserted':
            self.add([params['node']], params['parentNodeId'])
        elif method == 'DOM.childNodeRemoved':
            self.parents.pop(params['nodeId'], None)
            self.removed.add(params['nodeId'])
            if len(self.removed) > 32768:
                self.valid = False
        elif method == 'DOM.documentUpdated':
            self.valid = False

    def contains(self, ancestor, node):
        seen = set()
        while self.valid and node is not None and node not in seen:
            if node in self.removed:
                return False
            if node == ancestor:
                return True
            seen.add(node)
            node = self.parents.get(node)
        return False
