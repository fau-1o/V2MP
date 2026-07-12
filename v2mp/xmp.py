"""
xmp.py
======

Construction of the Google Motion Photo XMP packet.

Earlier versions of this module built the XMP programmatically via
``lxml``, which guarantees well-formed output but produces a different
(minified, differently-namespaced) byte layout than what a genuine Xiaomi
device actually writes. Since a **complete, untruncated** dump of a real
sample's XMP segment is now available, this module instead renders a
literal text template matching that sample byte-for-byte (verified: the
only variable content is the ``Item:Length`` value and, optionally,
``GCamera:MotionPhotoPresentationTimestampUs``; substituting those two
integers into the confirmed template reproduces the real segment's exact
size).

Key structural facts, confirmed via full (not truncated) hex/text dumps
of a genuine Xiaomi "Convert Video to Motion Photo" output, across two
independent samples:

* The packet is NOT wrapped in ``<?xpacket begin=...?> ... <?xpacket
  end="w"?>`` processing instructions -- it starts directly with
  ``<x:xmpmeta``.
* The container entries are ``<Container:Item .../>`` elements (element
  in the ``Container`` namespace; ``Mime``/``Semantic``/``Length``/
  ``Padding`` attributes in the ``Item`` namespace) -- NOT
  ``<Item:Item>``. This is the single most important fix: it is
  XML-well-formed either way and passes loose validators (e.g.
  ExifTool), but Xiaomi Gallery specifically walks the container looking
  for ``Container:Item`` elements and silently rejects the file
  otherwise.
* It is hand-formatted (Adobe XMP SDK style): multi-line, indented, with
  namespace declarations spread across levels (``xmlns:x`` on
  ``<x:xmpmeta>``, ``xmlns:rdf`` on ``<rdf:RDF>``, and
  ``xmlns:GCamera``/``xmlns:Container``/``xmlns:Item`` on
  ``<rdf:Description>``) rather than all hoisted onto the root element
  the way a generic serializer (including ``lxml``) would produce them.

Only two integers are ever substituted into the template (video length
and presentation timestamp), both coerced through ``int()`` first, so
there is no XML injection surface despite this being string-based rather
than tree-based construction.
"""

from __future__ import annotations

from lxml import etree

from .config import NS_CONTAINER, NS_CONTAINER_ITEM, NS_GCAMERA, NS_RDF

#: Literal XMP template, matching a genuine Xiaomi sample byte-for-byte
#: apart from the two substituted integers. Do not reformat/reindent this
#: string -- every space and newline here was reproduced from a real,
#: fully-dumped APP1-XMP segment.
_XMP_TEMPLATE = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 5.1.0-jc003">\n'
    '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '    <rdf:Description rdf:about=""\n'
    '        xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"\n'
    '        xmlns:Container="http://ns.google.com/photos/1.0/container/"\n'
    '        xmlns:Item="http://ns.google.com/photos/1.0/container/item/"\n'
    '      GCamera:MotionPhoto="1"\n'
    '      GCamera:MotionPhotoVersion="1"\n'
    '      GCamera:MotionPhotoPresentationTimestampUs="{timestamp_us}">\n'
    "      <Container:Directory>\n"
    "        <rdf:Seq>\n"
    '          <rdf:li rdf:parseType="Resource">\n'
    "            <Container:Item\n"
    '              Item:Mime="image/jpeg"\n'
    '              Item:Semantic="Primary"/>\n'
    "          </rdf:li>\n"
    '          <rdf:li rdf:parseType="Resource">\n'
    "            <Container:Item\n"
    '              Item:Mime="video/mp4"\n'
    '              Item:Semantic="MotionPhoto"\n'
    '              Item:Length="{video_length}"\n'
    '              Item:Padding="0"/>\n'
    "          </rdf:li>\n"
    "        </rdf:Seq>\n"
    "      </Container:Directory>\n"
    "    </rdf:Description>\n"
    "  </rdf:RDF>\n"
    "</x:xmpmeta>"
)


def build_motion_photo_xmp(
    video_length_bytes: int,
    presentation_timestamp_us: int = 0,
) -> bytes:
    """
    Build a complete Google Motion Photo XMP packet.

    Renders :data:`_XMP_TEMPLATE`, a literal reproduction of a genuine
    Xiaomi sample's APP1-XMP content, substituting only the two values
    that legitimately vary per-file.

    Args:
        video_length_bytes: Exact size, in bytes, of the MP4 payload that
            will be appended after the JPEG's EOI marker. This is written
            verbatim into ``Item:Length`` and MUST match the actual
            appended data size or Google Photos / gallery apps may refuse
            to recognize the file as a Motion Photo.
        presentation_timestamp_us: Value for
            ``GCamera:MotionPhotoPresentationTimestampUs``, in microseconds.
            ``0`` (the default) indicates the still cover frame itself
            should be used as the presentation frame.

    Returns:
        The full XMP packet, starting directly with ``<x:xmpmeta`` (no
        ``<?xpacket?>`` wrapper), as UTF-8 encoded bytes, ready to be
        embedded in an APP1 JPEG segment.

    Raises:
        ValueError: If ``video_length_bytes`` is not a positive integer.
    """
    if video_length_bytes <= 0:
        raise ValueError("video_length_bytes must be a positive integer")

    text = _XMP_TEMPLATE.format(
        video_length=int(video_length_bytes),
        timestamp_us=int(presentation_timestamp_us),
    )
    return text.encode("utf-8")


def parse_video_length_from_xmp(xmp_bytes: bytes) -> int | None:
    """
    Extract the ``Item:Length`` value for the MotionPhoto item from an XMP packet.

    Used by the validator to confirm that the declared video length in the
    XMP matches the actual number of bytes appended after the JPEG.

    Args:
        xmp_bytes: Raw XMP packet bytes (with or without the xpacket wrapper).

    Returns:
        The parsed integer length, or ``None`` if it could not be found.
    """
    root = _parse_xmpmeta(xmp_bytes)
    if root is None:
        return None

    # The container item is a <Container:Item> ELEMENT whose Semantic/Length
    # are attributes in the Item namespace.
    items = root.findall(
        f".//{{{NS_CONTAINER}}}Item[@{{{NS_CONTAINER_ITEM}}}Semantic='MotionPhoto']"
    )
    for item in items:
        length_str = item.get(f"{{{NS_CONTAINER_ITEM}}}Length")
        if length_str is not None:
            try:
                return int(length_str)
            except ValueError:
                return None
    return None


def has_motion_photo_flag(xmp_bytes: bytes) -> bool:
    """
    Check whether an XMP packet declares ``GCamera:MotionPhoto="1"``.

    Args:
        xmp_bytes: Raw XMP packet bytes.

    Returns:
        True if the MotionPhoto flag is present and set to ``"1"``.
    """
    root = _parse_xmpmeta(xmp_bytes)
    if root is None:
        return False

    descriptions = root.findall(f".//{{{NS_RDF}}}Description")
    for description in descriptions:
        if description.get(f"{{{NS_GCAMERA}}}MotionPhoto") == "1":
            return True
    return False


def required_namespaces_present(xmp_bytes: bytes) -> bool:
    """
    Verify all three required Google namespaces appear in the raw XMP bytes.

    This is a lightweight substring check (in addition to structural
    validation elsewhere) since some readers are sensitive to the exact
    namespace URIs being present verbatim in the packet.

    Args:
        xmp_bytes: Raw XMP packet bytes.

    Returns:
        True if all required namespace URIs are present.
    """
    required = (
        NS_GCAMERA.encode("ascii"),
        NS_CONTAINER.encode("ascii"),
        NS_CONTAINER_ITEM.encode("ascii"),
    )
    return all(ns in xmp_bytes for ns in required)


def has_container_item_element(xmp_bytes: bytes) -> bool:
    """
    Verify the container entries are ``<Container:Item>`` elements (not
    the incorrect ``<Item:Item>`` form).

    This directly checks for the structural bug this module was fixed to
    avoid: an ``Item:Item`` element is namespace-well-formed and will
    often still validate loosely (e.g. under ExifTool), but Google/Xiaomi
    gallery apps that walk the container looking specifically for
    ``Container:Item`` elements will not recognize the file.

    Args:
        xmp_bytes: Raw XMP packet bytes.

    Returns:
        True if at least one proper ``Container:Item`` element is found
        and no incorrect ``Item:Item`` element is found.
    """
    root = _parse_xmpmeta(xmp_bytes)
    if root is None:
        return False
    correct = root.findall(f".//{{{NS_CONTAINER}}}Item")
    incorrect = root.findall(f".//{{{NS_CONTAINER_ITEM}}}Item")
    return len(correct) >= 2 and len(incorrect) == 0


def _parse_xmpmeta(xmp_bytes: bytes) -> etree._Element | None:
    """
    Parse the ``<x:xmpmeta>`` subtree out of a raw XMP packet.

    Strips the ``<?xpacket?>`` processing-instruction wrapper (which lxml
    does not need to see) before parsing, for backward compatibility with
    any XMP that still has one.

    Args:
        xmp_bytes: Raw XMP packet bytes, with or without the xpacket wrapper.

    Returns:
        The parsed ``<x:xmpmeta>`` root element, or ``None`` if the bytes
        could not be located or parsed.
    """
    try:
        start = xmp_bytes.find(b"<x:xmpmeta")
        end = xmp_bytes.rfind(b"</x:xmpmeta>")
        if start == -1 or end == -1:
            return None
        xml_slice = xmp_bytes[start : end + len(b"</x:xmpmeta>")]
        return etree.fromstring(xml_slice)
    except etree.XMLSyntaxError:
        return None
