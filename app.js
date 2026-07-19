"use strict";
// ============================================================================
// Constants (mirrors v2mp/config.py)
// ============================================================================

const JPEG_SOI = new Uint8Array([0xff, 0xd8]);
const JPEG_EOI = new Uint8Array([0xff, 0xd9]);
const JPEG_APP0 = 0xe0;
const JPEG_APP1 = 0xe1;
const JPEG_APP2 = 0xe2;
const JPEG_APP4 = 0xe4;
const JPEG_SOS = 0xda;

const EXIF_IDENTIFIER = strToBytes("Exif\x00\x00");
const XMP_IDENTIFIER = strToBytes("http://ns.adobe.com/xap/1.0/\x00");
const XIAOMI_IDENTIFIER = strToBytes("XIAOMI_CUSTOMIZE\x00");
const XIAOMI_HEADER_VERSION_BYTES = new Uint8Array([0x01, 0x01]);

const NS_GCAMERA = "http://ns.google.com/photos/1.0/camera/";
const NS_CONTAINER = "http://ns.google.com/photos/1.0/container/";
const NS_CONTAINER_ITEM = "http://ns.google.com/photos/1.0/container/item/";

// Base64 of the exact sRGB ICC profile embedded by the v2mp CLI (generated
// once via Pillow/LittleCMS, with the CMM-type fingerprint zeroed out to
// match a genuine Xiaomi sample's "no CMM declared" header -- see
// v2mp/metadata.py:_generate_srgb_icc_profile).
const ICC_PROFILE_BASE64 =
  "AAACTAAAAAAEQAAAbW50clJHQiBYWVogB+oABwAMAAUAOgALYWNzcEFQUEwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPbWAAEAAAAA0y1sY21zAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAALZGVzYwAAAQgAAAA2Y3BydAAAAUAAAABMd3RwdAAAAYwAAAAUY2hhZAAAAaAAAAAsclhZWgAAAcwAAAAUYlhZWgAAAeAAAAAUZ1hZWgAAAfQAAAAUclRSQwAAAggAAAAgZ1RSQwAAAggAAAAgYlRSQwAAAggAAAAgY2hybQAAAigAAAAkbWx1YwAAAAAAAAABAAAADGVuVVMAAAAaAAAAHABzAFIARwBCACAAYgB1AGkAbAB0AC0AaQBuAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAADAAAAAcAE4AbwAgAGMAbwBwAHkAcgBpAGcAaAB0ACwAIAB1AHMAZQAgAGYAcgBlAGUAbAB5WFlaIAAAAAAAAPbWAAEAAAAA0y1zZjMyAAAAAAABDEIAAAXe///zJQAAB5MAAP2Q///7of///aIAAAPcAADAblhZWiAAAAAAAABvoAAAOPUAAAOQWFlaIAAAAAAAACSfAAAPhAAAtsNYWVogAAAAAAAAYpcAALeHAAAY2XBhcmEAAAAAAAMAAAACZmYAAPKnAAANWQAAE9AAAApbY2hybQAAAAAAAwAAAACj1wAAVHsAAEzNAACZmgAAJmYAAA9c";

// ============================================================================
// Small helpers
// ============================================================================

function strToBytes(str) {
  const bytes = new Uint8Array(str.length);
  for (let i = 0; i < str.length; i++) bytes[i] = str.charCodeAt(i);
  return bytes;
}

function concatBytes(chunks) {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out;
}

function u16be(value) {
  return new Uint8Array([(value >> 8) & 0xff, value & 0xff]);
}

function u32be(value) {
  return new Uint8Array([
    (value >>> 24) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 8) & 0xff,
    value & 0xff,
  ]);
}

function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// ============================================================================
// jpeg.js -- JPEG marker parsing / splicing (mirrors v2mp/jpeg.py)
// ============================================================================

class JpegFormatError extends Error {}

function validateSoi(data) {
  if (data.length < 2 || data[0] !== 0xff || data[1] !== 0xd8) {
    throw new JpegFormatError("Data does not start with a valid JPEG SOI marker (FFD8)");
  }
}

function findEoiIndex(data) {
  const length = data.length;
  let pos = 2;

  while (true) {
    if (pos + 1 >= length) throw new JpegFormatError("Reached end of data while parsing JPEG markers");
    if (data[pos] !== 0xff) {
      throw new JpegFormatError(`Expected JPEG marker (0xFF) at offset ${pos}, found 0x${data[pos].toString(16)}`);
    }
    const marker = data[pos + 1];

    if (marker === 0xd9) return pos;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      pos += 2;
      continue;
    }
    if (pos + 4 > length) throw new JpegFormatError("Truncated JPEG segment length field");
    const segLen = (data[pos + 2] << 8) | data[pos + 3];
    if (segLen < 2) throw new JpegFormatError(`Invalid JPEG segment length ${segLen} at offset ${pos}`);

    if (marker === JPEG_SOS) {
      return findEoiAfterScan(data, pos + 2 + segLen);
    }
    pos = pos + 2 + segLen;
  }
}

function findEoiAfterScan(data, scanStart) {
  const length = data.length;
  let i = scanStart;
  while (i < length - 1) {
    if (data[i] === 0xff) {
      const nxt = data[i + 1];
      if (nxt === 0x00) { i += 2; continue; }
      if (nxt >= 0xd0 && nxt <= 0xd7) { i += 2; continue; }
      if (nxt === 0xff) { i += 1; continue; }
      if (nxt === 0xd9) return i;
      i += 1;
      continue;
    }
    i += 1;
  }
  throw new JpegFormatError("No JPEG EOI marker (FFD9) found after scan data");
}

function stripSoi(jpegBytes) {
  validateSoi(jpegBytes);
  const eoiIndex = findEoiIndex(jpegBytes);
  return jpegBytes.slice(2, eoiIndex + 2);
}

function ensureApp0Present(body, jfifSegment) {
  if (body.length >= 2 && body[0] === 0xff && body[1] === 0xe0) return body;
  return concatBytes([jfifSegment, body]);
}

function insertAfterApp0(body, newSegments) {
  let insertionPoint = 0;
  if (body.length >= 4 && body[0] === 0xff && body[1] === 0xe0) {
    const segLen = (body[2] << 8) | body[3];
    insertionPoint = 2 + segLen;
  }
  return concatBytes([body.slice(0, insertionPoint), concatBytes(newSegments), body.slice(insertionPoint)]);
}

function assembleMotionPhoto(coverJpegBytes, videoBytes, headerSegments, postApp0Segments, ensureApp0Segment) {
  let body = stripSoi(coverJpegBytes);
  if (ensureApp0Segment) body = ensureApp0Present(body, ensureApp0Segment);
  if (postApp0Segments && postApp0Segments.length) body = insertAfterApp0(body, postApp0Segments);
  const header = concatBytes(headerSegments);
  return concatBytes([JPEG_SOI, header, body, videoBytes]);
}

function splitMotionPhoto(data) {
  validateSoi(data);
  const eoiIndex = findEoiIndex(data);
  const jpegEnd = eoiIndex + 2;
  return [data.slice(0, jpegEnd), data.slice(jpegEnd)];
}

const MARKER_NAMES = {
  0xd8: "SOI", 0xd9: "EOI", 0xda: "SOS", 0xdb: "DQT", 0xc4: "DHT",
  0xc0: "SOF0", 0xc1: "SOF1", 0xc2: "SOF2", 0xc3: "SOF3", 0xfe: "COM", 0x01: "TEM",
};
for (let i = 0; i < 8; i++) MARKER_NAMES[0xd0 + i] = `RST${i}`;
for (let i = 0; i < 16; i++) MARKER_NAMES[0xe0 + i] = `APP${i}`;

function iterMarkers(data) {
  validateSoi(data);
  const markers = [{ marker: "SOI", offset: 0, length: 2 }];
  const length = data.length;
  let pos = 2;

  while (true) {
    if (pos + 1 >= length) throw new JpegFormatError("Reached end of data while parsing JPEG markers");
    if (data[pos] !== 0xff) throw new JpegFormatError(`Expected JPEG marker at offset ${pos}`);
    const markerByte = data[pos + 1];
    const name = MARKER_NAMES[markerByte] || `0x${markerByte.toString(16).toUpperCase()}`;

    if (markerByte === 0xd9) {
      markers.push({ marker: name, offset: pos, length: 2 });
      return markers;
    }
    if (markerByte === 0x01 || (markerByte >= 0xd0 && markerByte <= 0xd7)) {
      markers.push({ marker: name, offset: pos, length: 2 });
      pos += 2;
      continue;
    }
    if (pos + 4 > length) throw new JpegFormatError("Truncated JPEG segment length field");
    const segLen = (data[pos + 2] << 8) | data[pos + 3];
    if (segLen < 2) throw new JpegFormatError(`Invalid JPEG segment length ${segLen}`);
    markers.push({ marker: name, offset: pos, length: 2 + segLen });

    if (markerByte === JPEG_SOS) {
      const eoiIndex = findEoiAfterScan(data, pos + 2 + segLen);
      markers.push({ marker: "EOI", offset: eoiIndex, length: 2 });
      return markers;
    }
    pos = pos + 2 + segLen;
  }
}

// ============================================================================
// metadata.js -- Exif / Xiaomi APP4 / ICC / JFIF segment builders
// (mirrors v2mp/metadata.py)
// ============================================================================

function buildAppSegment(markerByte, payload) {
  const length = payload.length + 2;
  if (length > 0xffff) throw new Error(`Segment payload too large (${payload.length} bytes)`);
  return concatBytes([new Uint8Array([0xff, markerByte]), u16be(length), payload]);
}

// -- Exif (hand-rolled minimal TIFF writer, byte-identical to the patched
//    piexif output the CLI produces: IFD0 = {ImageWidth, ImageLength} as
//    SHORT (type 3), optional Orientation; Exif SubIFD = {0x8897=1,
//    LightSource=0, 0x9A01=1} as SHORT, in ascending tag order.) --

function tiffShortEntry(tag, value) {
  // tag(2) type(2)=3 count(4)=1 value(2)+pad(2)
  return concatBytes([u16be(tag), u16be(3), u32be(1), u16be(value), u16be(0)]);
}

function tiffLongEntry(tag, value) {
  // tag(2) type(2)=4 count(4)=1 value(4)
  return concatBytes([u16be(tag), u16be(4), u32be(1), u32be(value)]);
}

function buildExifSegment({ orientation = null, imageWidth = null, imageHeight = null } = {}) {
  if (orientation !== null && (orientation < 1 || orientation > 8)) {
    throw new Error("EXIF orientation must be between 1 and 8");
  }

  const zerothEntries = [];
  if (imageWidth !== null) zerothEntries.push({ tag: 0x0100, bytes: tiffShortEntry(0x0100, imageWidth) });
  if (imageHeight !== null) zerothEntries.push({ tag: 0x0101, bytes: tiffShortEntry(0x0101, imageHeight) });
  if (orientation !== null) zerothEntries.push({ tag: 0x0112, bytes: tiffShortEntry(0x0112, orientation) });
  zerothEntries.sort((a, b) => a.tag - b.tag);

  // Exif SubIFD: 0x8897=1, LightSource(0x9208)=0, 0x9A01=1 (ascending order)
  const exifEntries = [
    { tag: 0x8897, bytes: tiffShortEntry(0x8897, 1) },
    { tag: 0x9208, bytes: tiffShortEntry(0x9208, 0) },
    { tag: 0x9a01, bytes: tiffShortEntry(0x9a01, 1) },
  ].sort((a, b) => a.tag - b.tag);

  // IFD0 also needs an ExifIFDPointer (0x8769) entry -- ascending order
  // means it goes wherever it numerically falls (after Orientation, before
  // nothing else here since 0x8769 > all other IFD0 tags used).
  const headerLen = 8; // byte-order(2) + magic(2) + ifd0-offset(4)
  const ifd0Offset = headerLen;
  const ifd0EntryCount = zerothEntries.length + 1; // +1 for ExifIFDPointer
  const ifd0Len = 2 + ifd0EntryCount * 12 + 4;
  const exifSubIfdOffset = ifd0Offset + ifd0Len;

  const ifd0AllEntries = [...zerothEntries, { tag: 0x8769, bytes: null }].sort((a, b) => a.tag - b.tag);

  const ifd0Bytes = [];
  ifd0Bytes.push(u16be(ifd0EntryCount));
  for (const entry of ifd0AllEntries) {
    if (entry.tag === 0x8769) {
      ifd0Bytes.push(tiffLongEntry(0x8769, exifSubIfdOffset));
    } else {
      ifd0Bytes.push(entry.bytes);
    }
  }
  ifd0Bytes.push(u32be(0)); // next IFD offset

  const exifSubIfdBytes = [];
  exifSubIfdBytes.push(u16be(exifEntries.length));
  for (const entry of exifEntries) exifSubIfdBytes.push(entry.bytes);
  // NOTE: no trailing 4-byte "next IFD offset" here -- confirmed via
  // byte-for-byte comparison against genuine Xiaomi samples (and matches
  // piexif's own output) that the Exif SubIFD is not terminated with one,
  // unlike IFD0 which does have a (zero) next-offset field.

  const tiff = concatBytes([
    strToBytes("MM"),
    u16be(0x002a),
    u32be(ifd0Offset),
    ...ifd0Bytes,
    ...exifSubIfdBytes,
  ]);

  const exifPayload = concatBytes([EXIF_IDENTIFIER, tiff]);
  return buildAppSegment(JPEG_APP1, exifPayload);
}

function buildXmpSegmentRaw(xmpPacketBytes) {
  const payload = concatBytes([XMP_IDENTIFIER, xmpPacketBytes]);
  return buildAppSegment(JPEG_APP1, payload);
}

function jsonStringifyOrdered(pairs) {
  // JS engines enumerate integer-like string keys ("8897") in ascending
  // numeric order BEFORE any other string keys, regardless of insertion
  // order -- so a plain object literal would silently reorder
  // {"9a01":..., "8897":...} to {"8897":..., "9a01":...} since "8897" is
  // purely numeric but "9a01" is not. Building the JSON string directly
  // from an explicit ordered list of [key, value] pairs sidesteps that
  // entirely and guarantees the exact byte order confirmed against a
  // genuine sample.
  const parts = pairs.map(([k, v]) => `${JSON.stringify(k)}:${JSON.stringify(v)}`);
  return `{${parts.join(",")}}`;
}

const DEFAULT_XIAOMI_PAYLOAD_PAIRS = [
  ["9a01", "1"],
  ["8897", "1"],
  ["version", "32"],
];

function buildXiaomiSegment(payloadPairs = null) {
  const pairs = payloadPairs || DEFAULT_XIAOMI_PAYLOAD_PAIRS;
  const jsonBytes = strToBytes(jsonStringifyOrdered(pairs));
  const fullPayload = concatBytes([XIAOMI_IDENTIFIER, XIAOMI_HEADER_VERSION_BYTES, jsonBytes]);
  return buildAppSegment(JPEG_APP4, fullPayload);
}

const JFIF_PAYLOAD = new Uint8Array([
  0x4a, 0x46, 0x49, 0x46, 0x00, // "JFIF\0"
  0x01, 0x01, // version 1.01
  0x00, // units
  0x00, 0x01, // Xdensity
  0x00, 0x01, // Ydensity
  0x00, 0x00, // thumbnail w/h
]);

function buildJfifSegment() {
  return buildAppSegment(JPEG_APP0, JFIF_PAYLOAD);
}

const ICC_IDENTIFIER = strToBytes("ICC_PROFILE\x00");

function buildIccProfileSegment(iccBytes = null) {
  const data = iccBytes || base64ToBytes(ICC_PROFILE_BASE64);
  const payload = concatBytes([ICC_IDENTIFIER, new Uint8Array([1, 1]), data]);
  return [buildAppSegment(JPEG_APP2, payload)];
}

// ============================================================================
// xmp.js -- literal template, byte-identical to v2mp/xmp.py's _XMP_TEMPLATE
// ============================================================================

const XMP_TEMPLATE_PARTS = [
  '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 5.1.0-jc003">\n',
  '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n',
  '    <rdf:Description rdf:about=""\n',
  '        xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"\n',
  '        xmlns:Container="http://ns.google.com/photos/1.0/container/"\n',
  '        xmlns:Item="http://ns.google.com/photos/1.0/container/item/"\n',
  '      GCamera:MotionPhoto="1"\n',
  '      GCamera:MotionPhotoVersion="1"\n',
  '      GCamera:MotionPhotoPresentationTimestampUs="{timestamp_us}">\n',
  "      <Container:Directory>\n",
  "        <rdf:Seq>\n",
  '          <rdf:li rdf:parseType="Resource">\n',
  "            <Container:Item\n",
  '              Item:Mime="image/jpeg"\n',
  '              Item:Semantic="Primary"/>\n',
  "          </rdf:li>\n",
  '          <rdf:li rdf:parseType="Resource">\n',
  "            <Container:Item\n",
  '              Item:Mime="video/mp4"\n',
  '              Item:Semantic="MotionPhoto"\n',
  '              Item:Length="{video_length}"\n',
  '              Item:Padding="0"/>\n',
  "          </rdf:li>\n",
  "        </rdf:Seq>\n",
  "      </Container:Directory>\n",
  "    </rdf:Description>\n",
  "  </rdf:RDF>\n",
  "</x:xmpmeta>",
].join("");

function buildMotionPhotoXmp(videoLengthBytes, presentationTimestampUs = 0) {
  if (videoLengthBytes <= 0) throw new Error("videoLengthBytes must be a positive integer");
  const text = XMP_TEMPLATE_PARTS
    .replace("{timestamp_us}", String(Math.trunc(presentationTimestampUs)))
    .replace("{video_length}", String(Math.trunc(videoLengthBytes)));
  return strToBytes(text);
}

// ============================================================================
// validator.js -- lightweight validation (mirrors v2mp/validator.py)
// ============================================================================

function extractXmpBytes(jpegBytes) {
  let pos = 2;
  const length = jpegBytes.length;
  while (pos + 4 <= length) {
    if (jpegBytes[pos] !== 0xff) break;
    const marker = jpegBytes[pos + 1];
    if (marker === JPEG_SOS || marker === 0xd9) break;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) { pos += 2; continue; }
    const segLen = (jpegBytes[pos + 2] << 8) | jpegBytes[pos + 3];
    const segStart = pos + 4;
    const segEnd = pos + 2 + segLen;
    if (marker === 0xe1) {
      const payload = jpegBytes.slice(segStart, segEnd);
      if (payload.length >= XMP_IDENTIFIER.length) {
        let matches = true;
        for (let k = 0; k < XMP_IDENTIFIER.length; k++) {
          if (payload[k] !== XMP_IDENTIFIER[k]) { matches = false; break; }
        }
        if (matches) return payload.slice(XMP_IDENTIFIER.length);
      }
    }
    pos = segEnd;
  }
  return null;
}

function xmpHasMotionPhotoFlag(xmpBytes) {
  const text = bytesToLatin1String(xmpBytes);
  return /GCamera:MotionPhoto="1"/.test(text);
}

function xmpHasRequiredNamespaces(xmpBytes) {
  const text = bytesToLatin1String(xmpBytes);
  return text.includes(NS_GCAMERA) && text.includes(NS_CONTAINER) && text.includes(NS_CONTAINER_ITEM);
}

function xmpHasContainerItemElement(xmpBytes) {
  const text = bytesToLatin1String(xmpBytes);
  const correct = (text.match(/<Container:Item\b/g) || []).length;
  const incorrect = (text.match(/<Item:Item\b/g) || []).length;
  return correct >= 2 && incorrect === 0;
}

function xmpVideoLength(xmpBytes) {
  const text = bytesToLatin1String(xmpBytes);
  const match = text.match(/Item:Length="(\d+)"/);
  return match ? parseInt(match[1], 10) : null;
}

function bytesToLatin1String(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return s;
}

function validateMotionPhoto(data) {
  const checks = {};
  const messages = {};
  const add = (name, passed, message = "") => {
    checks[name] = passed;
    messages[name] = message;
  };

  try {
    validateSoi(data);
    add("jpeg_starts_with_soi", true);
  } catch (e) {
    add("jpeg_starts_with_soi", false, e.message);
    return { checks, messages, isValid: false };
  }

  let eoiIndex;
  try {
    eoiIndex = findEoiIndex(data);
    add("jpeg_contains_eoi", true);
  } catch (e) {
    add("jpeg_contains_eoi", false, e.message);
    return { checks, messages, isValid: false };
  }

  const jpegEnd = eoiIndex + 2;
  const jpegBytes = data.slice(0, jpegEnd);
  const mp4Bytes = data.slice(jpegEnd);

  add("output_size_matches_components", jpegBytes.length + mp4Bytes.length === data.length);

  const mp4Present = mp4Bytes.length > 0;
  add("mp4_payload_present", mp4Present, mp4Present ? "" : "No trailing MP4 data found");

  if (mp4Present) {
    const ftypOk =
      mp4Bytes.length >= 8 &&
      mp4Bytes[4] === 0x66 && mp4Bytes[5] === 0x74 && mp4Bytes[6] === 0x79 && mp4Bytes[7] === 0x70;
    add("mp4_starts_with_ftyp", ftypOk, ftypOk ? "" : "Expected 'ftyp' box at offset 4");
  }

  const xmpBytes = extractXmpBytes(jpegBytes);
  const xmpPresent = xmpBytes !== null;
  add("xmp_segment_present", xmpPresent, xmpPresent ? "" : "No XMP APP1 segment found");

  if (xmpPresent) {
    add("xmp_namespaces_correct", xmpHasRequiredNamespaces(xmpBytes));
    add("motion_photo_flag_set", xmpHasMotionPhotoFlag(xmpBytes));
    add("container_item_structure_correct", xmpHasContainerItemElement(xmpBytes));
    if (mp4Present) {
      const declared = xmpVideoLength(xmpBytes);
      const matches = declared === mp4Bytes.length;
      add(
        "video_length_field_matches",
        matches,
        matches ? "" : `XMP declares ${declared}, actual is ${mp4Bytes.length} bytes`
      );
    }
  }

  const isValid = Object.values(checks).every(Boolean);
  return { checks, messages, isValid };
}



// =====================================================================
// ffmpeg wrapper (mirrors v2mp/ffmpeg.py) -- all processing happens
// locally via WebAssembly; nothing is ever sent to a server.
// =====================================================================

// NOTE: ffmpeg.wasm's own worker rejects with a plain STRING (via
// `e.toString()`), not an Error object -- so `err.message` on anything
// that bubbles up from an ffmpeg operation is `undefined`. This helper
// normalizes any thrown value into a readable string.
function errorMessage(err) {
  if (err instanceof Error) return err.message || err.toString() || "Unknown error";
  if (typeof err === "string") return err;
  try {
    return String(err);
  } catch (e) {
    return "Unknown error";
  }
}

/**
 * Like errorMessage(), but adds an actionable suggestion when the error
 * is ffmpeg.wasm's WASM memory-limit crash and a retry already happened
 * (i.e. it failed twice in a row -- so this isn't a one-off fluke).
 */
function friendlyErrorMessage(err) {
  const msg = errorMessage(err);
  if (isRecoverableFfmpegError(err)) {
    return (
      msg +
      " — this can happen with long or high-resolution videos, since ffmpeg's " +
      "in-browser engine has a fixed memory limit (this is unrelated to your " +
      "device's free RAM). Try trimming the video to a shorter clip first, or " +
      "use \"By timestamp\" instead of \"By frame number\"."
    );
  }
  return msg;
}

let ffmpegInstance = null;
let ffmpegLoadPromise = null;

async function blobUrlFor(url, mimeType) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Failed to fetch ${url}: ${resp.status}`);
  const buf = await resp.arrayBuffer();
  return URL.createObjectURL(new Blob([buf], { type: mimeType }));
}

const FFMPEG_CORE_SINGLE_THREAD = "https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.6/dist/umd";
const FFMPEG_CORE_MULTI_THREAD = "https://cdn.jsdelivr.net/npm/@ffmpeg/core-mt@0.12.6/dist/umd";

// `crossOriginIsolated` is only `true` when the page was served with the
// Cross-Origin-Opener-Policy / Cross-Origin-Embedder-Policy headers the
// multi-threaded core needs (SharedArrayBuffer requires it). Plain GitHub
// Pages hosting cannot set these headers at all, so this will be `false`
// there -- Cloudflare Pages / Netlify / Vercel can, via the included
// `_headers` / `vercel.json` files, which is what flips this to `true`.
let ffmpegEngine = null; // set once loaded: "multi-thread" or "single-thread"

function updateEngineBadge() {
  const el = document.getElementById("engineBadge");
  if (!el) return;
  if (ffmpegEngine === "multi-thread") {
    el.textContent = "Engine: multi-thread (handles longer videos)";
  } else if (ffmpegEngine === "single-thread") {
    el.textContent = "Engine: single-thread (best for short clips)";
  } else {
    el.textContent = "Engine: not loaded yet";
  }
}

async function getFfmpeg(onStatus) {
  if (ffmpegInstance) return ffmpegInstance;
  if (ffmpegLoadPromise) return ffmpegLoadPromise;

  ffmpegLoadPromise = (async () => {
    if (typeof FFmpegWASM === "undefined") {
      throw new Error("ffmpeg.wasm failed to load from the CDN. Check your internet connection and try again.");
    }
    const { FFmpeg } = FFmpegWASM;
    const ffmpeg = new FFmpeg();
    ffmpeg.on("log", ({ message }) => {
      if (onStatus) onStatus(message);
    });

    const useMultiThread = window.crossOriginIsolated === true;
    const base = useMultiThread ? FFMPEG_CORE_MULTI_THREAD : FFMPEG_CORE_SINGLE_THREAD;

    try {
      const coreURL = await blobUrlFor(`${base}/ffmpeg-core.js`, "text/javascript");
      const wasmURL = await blobUrlFor(`${base}/ffmpeg-core.wasm`, "application/wasm");
      const loadOpts = { coreURL, wasmURL };
      if (useMultiThread) {
        loadOpts.workerURL = await blobUrlFor(`${base}/ffmpeg-core.worker.js`, "text/javascript");
      }
      await ffmpeg.load(loadOpts);
      ffmpegEngine = useMultiThread ? "multi-thread" : "single-thread";
      updateEngineBadge();
    } catch (e) {
      // If the multi-thread core fails to load or init for any reason,
      // fall back to the single-thread core rather than failing outright.
      if (useMultiThread) {
        console.warn("Multi-thread ffmpeg core failed, falling back to single-thread:", errorMessage(e));
        const coreURL = await blobUrlFor(`${FFMPEG_CORE_SINGLE_THREAD}/ffmpeg-core.js`, "text/javascript");
        const wasmURL = await blobUrlFor(`${FFMPEG_CORE_SINGLE_THREAD}/ffmpeg-core.wasm`, "application/wasm");
        await ffmpeg.load({ coreURL, wasmURL });
        ffmpegEngine = "single-thread";
        updateEngineBadge();
      } else {
        throw e;
      }
    }

    ffmpegInstance = ffmpeg;
    return ffmpeg;
  })();

  return ffmpegLoadPromise;
}

// ffmpeg.wasm's single-threaded core has a small, FIXED internal WASM
// memory limit -- unrelated to how much free RAM the device actually
// has. It's a known upstream issue that this limit can be hit after a
// handful of exec() calls on the same instance, after which every
// subsequent call fails the same way until a fresh FFmpeg instance is
// created (see ffmpegwasm/ffmpeg.wasm#563 and similar reports). The
// helpers below detect that specific failure and transparently recover
// by discarding the current instance and loading a new one.

function isRecoverableFfmpegError(err) {
  const msg = errorMessage(err) || "";
  return /memory access out of bounds|RuntimeError|out of memory|Aborted\(/i.test(msg);
}

async function resetFfmpeg() {
  if (ffmpegInstance) {
    try {
      ffmpegInstance.terminate();
    } catch (e) {
      /* best effort -- the instance is being discarded either way */
    }
  }
  ffmpegInstance = null;
  ffmpegLoadPromise = null;
  ffmpegEngine = null;
  updateEngineBadge();
}

/**
 * Run `taskFn` once. If it fails with the WASM memory-limit error
 * described above, discard the current ffmpeg instance, load a fresh
 * one, and retry `taskFn` exactly once more before giving up.
 */
async function withFfmpegRetry(taskFn, onStatus) {
  try {
    return await taskFn();
  } catch (err) {
    if (!isRecoverableFfmpegError(err)) throw err;
    console.warn("ffmpeg hit its internal memory limit -- reloading and retrying once:", errorMessage(err));
    if (onStatus) onStatus("ffmpeg ran into a memory limit, restarting and retrying…");
    await resetFfmpeg();
    return await taskFn();
  }
}

/**
 * Extract a single still frame as a JPEG cover image.
 * Mirrors ffmpeg.py:extract_cover_frame -- same three selection modes,
 * same -bitexact flag to avoid a stray COM marker.
 */
/**
 * Probe a written file's frame rate by running ffmpeg with just `-i`
 * (which always exits non-zero since no output is given) and reading
 * the fps out of the stream-info it prints to its log. @ffmpeg/core's
 * single-threaded build doesn't bundle a separate ffprobe binary, so
 * this is the standard way to get basic stream info from it.
 */
async function probeFps(ffmpeg, inName) {
  const lines = [];
  const handler = ({ message }) => lines.push(message);
  ffmpeg.on("log", handler);
  try {
    await ffmpeg.exec(["-i", inName]);
  } catch (e) {
    /* expected -- ffmpeg always errors here since no output was given */
  } finally {
    ffmpeg.off("log", handler);
  }
  const match = lines.join("\n").match(/(\d+(?:\.\d+)?)\s*fps/);
  return match ? parseFloat(match[1]) : null;
}

async function extractCoverFrame(ffmpeg, videoBytes, { timestamp, frameIndex, auto }) {
  const inName = "cover_in_" + Math.random().toString(36).slice(2) + ".mp4";
  const outName = "cover_out_" + Math.random().toString(36).slice(2) + ".jpg";
  await ffmpeg.writeFile(inName, videoBytes);

  const args = ["-y"];

  if (auto) {
    // -ss skips a likely-uninteresting first second; `thumbnail` holds
    // every sampled frame in memory at once to compare them, so keeping
    // the sample count modest (10, not dozens) matters on longer or
    // higher-resolution videos, which can otherwise exceed ffmpeg.wasm's
    // fixed WASM memory ceiling (unrelated to the device's actual RAM).
    args.push("-ss", "1", "-i", inName, "-vf", "thumbnail=10", "-frames:v", "1");
  } else if (frameIndex !== null && frameIndex !== undefined) {
    // A bare `select=eq(n\,N)` with no seek forces ffmpeg to decode
    // EVERY frame from the very start of the file to count up to frame
    // N -- for a long or high-resolution video that alone can exceed
    // the memory ceiling. Seeking close first (cheap, keyframe-based)
    // and using `select` only to fine-tune the last couple of seconds
    // keeps the number of frames actually decoded small.
    const fps = await probeFps(ffmpeg, inName);
    if (fps && fps > 0) {
      const targetTime = frameIndex / fps;
      const seekTime = Math.max(0, targetTime - 2); // 2s safety margin
      const framesToSkip = Math.max(0, Math.round((targetTime - seekTime) * fps));
      args.push(
        "-ss", seekTime.toFixed(3),
        "-i", inName,
        "-vf", `select=eq(n\\,${framesToSkip})`,
        "-vsync", "vfr",
        "-frames:v", "1"
      );
    } else {
      // Couldn't determine the frame rate (unusual) -- fall back to the
      // exact but more memory-hungry method.
      args.push("-i", inName, "-vf", `select=eq(n\\,${frameIndex})`, "-vsync", "vfr", "-frames:v", "1");
    }
  } else {
    args.push("-ss", String(timestamp ?? "0.0"), "-i", inName, "-frames:v", "1");
  }

  args.push("-q:v", "2", "-bitexact", outName);

  const execCode = await ffmpeg.exec(args);
  if (execCode !== 0) {
    console.error("ffmpeg exec returned code", execCode, "for args", args);
  }

  let data;
  try {
    data = await ffmpeg.readFile(outName);
  } catch (e) {
    console.error(e);
    throw new Error(
      "Could not extract a cover frame" +
        (execCode !== 0 ? " (ffmpeg exited with code " + execCode + ")" : "") +
        ". If you set an exact frame number, it may be beyond the video's length."
    );
  }
  await safeDelete(ffmpeg, inName);
  await safeDelete(ffmpeg, outName);
  return new Uint8Array(data);
}

/** Trim a video. Mirrors ffmpeg.py:trim_video (stream copy, no re-encode). */
async function trimVideo(ffmpeg, videoBytes, start, end) {
  const inName = "trim_in_" + Math.random().toString(36).slice(2) + ".mp4";
  const outName = "trim_out_" + Math.random().toString(36).slice(2) + ".mp4";
  await ffmpeg.writeFile(inName, videoBytes);

  const args = ["-y"];
  if (start !== null && start !== undefined && start !== "") args.push("-ss", String(start));
  args.push("-i", inName);
  if (end !== null && end !== undefined && end !== "") args.push("-to", String(end));
  args.push("-c", "copy", outName);

  await ffmpeg.exec(args);
  const data = await ffmpeg.readFile(outName);
  await safeDelete(ffmpeg, inName);
  await safeDelete(ffmpeg, outName);
  return new Uint8Array(data);
}

/** Strip audio via remux. Mirrors ffmpeg.py:remux_strip_audio. */
async function stripAudio(ffmpeg, videoBytes) {
  const inName = "noaudio_in_" + Math.random().toString(36).slice(2) + ".mp4";
  const outName = "noaudio_out_" + Math.random().toString(36).slice(2) + ".mp4";
  await ffmpeg.writeFile(inName, videoBytes);
  await ffmpeg.exec(["-y", "-i", inName, "-c:v", "copy", "-an", outName]);
  const data = await ffmpeg.readFile(outName);
  await safeDelete(ffmpeg, inName);
  await safeDelete(ffmpeg, outName);
  return new Uint8Array(data);
}

async function safeDelete(ffmpeg, name) {
  try {
    await ffmpeg.deleteFile(name);
  } catch (e) {
    /* best-effort cleanup only */
  }
}

/**
 * Extract a cover frame using the browser's OWN video decoder (an
 * offscreen <video> + <canvas>) instead of ffmpeg.wasm.
 *
 * This is the key fix for long/large videos: ffmpeg.wasm's single-
 * threaded core has a small, FIXED WASM memory ceiling, and decoding
 * frames (via the `select` or `thumbnail` filters) is exactly the kind
 * of operation that hits it on longer or higher-resolution videos. A
 * real browser's native video decoder has no such ceiling -- it's the
 * same decoder used for normal video playback, built for handling
 * arbitrarily long files.
 *
 * The trade-off: HTMLVideoElement seeking is time-based and only
 * approximately frame-accurate (browsers may snap to the nearest
 * keyframe rather than decode to an exact frame number), unlike
 * ffmpeg's `select` filter. For choosing a Motion Photo's cover image,
 * being off by a frame or two is an acceptable trade for not crashing
 * on long videos.
 */
async function extractCoverFrameNative(source, { timestamp, frameIndex, auto }) {
  const blob = source instanceof Uint8Array ? new Blob([source], { type: "video/mp4" }) : source;
  const url = URL.createObjectURL(blob);
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  video.src = url;

  try {
    await new Promise((resolve, reject) => {
      video.addEventListener("loadedmetadata", resolve, { once: true });
      video.addEventListener(
        "error",
        () => reject(new Error("Your browser couldn't decode this video.")),
        { once: true }
      );
    });

    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    let targetTime;
    if (auto) {
      // No frame-comparison heuristic here (that's what ffmpeg's
      // `thumbnail` filter did) -- just avoid frame 0, which is often a
      // black or fading-in leader frame.
      targetTime = duration > 2 ? 1 : duration * 0.15;
    } else if (frameIndex !== null && frameIndex !== undefined) {
      // No reliable way to get the exact frame rate from a plain
      // <video> element -- 30fps is a reasonable assumption for picking
      // a cover frame (exact frame-accuracy isn't critical here).
      targetTime = frameIndex / 30;
    } else {
      targetTime = parseFloat(timestamp);
      if (!Number.isFinite(targetTime)) targetTime = 0;
    }
    targetTime = Math.max(0, Math.min(targetTime, Math.max(0, duration - 0.05)));

    await new Promise((resolve, reject) => {
      const onError = () => reject(new Error("Your browser couldn't seek to that point in the video."));
      video.addEventListener(
        "seeked",
        () => {
          video.removeEventListener("error", onError);
          resolve();
        },
        { once: true }
      );
      video.addEventListener("error", onError, { once: true });
      video.currentTime = targetTime;
    });

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    const blobOut = await new Promise((resolve, reject) => {
      canvas.toBlob(
        (b) => (b ? resolve(b) : reject(new Error("Your browser couldn't encode the cover frame."))),
        "image/jpeg",
        0.92
      );
    });

    return {
      bytes: new Uint8Array(await blobOut.arrayBuffer()),
      width: canvas.width,
      height: canvas.height,
    };
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Try native (browser-decoder) cover extraction first -- fast and
 * immune to the WASM memory ceiling. Falls back to ffmpeg.wasm only if
 * that fails, e.g. for a codec/container the browser can't decode
 * natively but ffmpeg can. The ffmpeg fallback keeps its own retry
 * behavior for the WASM memory-limit crash (see withFfmpegRetry).
 */
async function extractCoverFrameWithFallback(videoBytes, options, onStatus) {
  try {
    return await extractCoverFrameNative(videoBytes, options);
  } catch (nativeErr) {
    console.warn("Native cover extraction failed, falling back to ffmpeg:", errorMessage(nativeErr));
    if (onStatus) onStatus("Falling back to ffmpeg for this video…");
    const bytes = await withFfmpegRetry(async () => {
      const ffmpeg = await getFfmpeg();
      return extractCoverFrame(ffmpeg, videoBytes, options);
    }, onStatus);
    const dims = await readJpegDimensionsFallback(bytes);
    return { bytes, width: dims.width, height: dims.height };
  }
}

async function readJpegDimensionsFallback(jpegBytes) {
  const blob = new Blob([jpegBytes], { type: "image/jpeg" });
  const bitmap = await createImageBitmap(blob);
  const dims = { width: bitmap.width, height: bitmap.height };
  bitmap.close();
  return dims;
}

// =====================================================================
// Orchestration (mirrors v2mp/converter.py:convert_video_to_motion_photo)
// =====================================================================

async function convertVideoToMotionPhoto(file, options, onStatus) {
  const needsFfmpeg = Boolean(options.trimStart || options.trimEnd || options.stripAudio);
  if (needsFfmpeg) {
    return withFfmpegRetry(() => _runConvert(file, options, onStatus), onStatus);
  }
  // No trim/audio-strip requested -- skip ffmpeg entirely. This is both
  // faster (no ~30MB core to load) and immune to the WASM memory
  // ceiling that only ffmpeg-based operations are subject to.
  return _runConvert(file, options, onStatus);
}

async function _runConvert(file, options, onStatus) {
  let videoBytes = new Uint8Array(await file.arrayBuffer());

  if (options.trimStart || options.trimEnd) {
    onStatus("Loading ffmpeg to trim the video…");
    const ffmpeg = await getFfmpeg();
    onStatus("Trimming video…");
    videoBytes = await trimVideo(ffmpeg, videoBytes, options.trimStart, options.trimEnd);
  }

  if (options.stripAudio) {
    onStatus("Loading ffmpeg to remove audio…");
    const ffmpeg = await getFfmpeg();
    onStatus("Removing audio…");
    videoBytes = await stripAudio(ffmpeg, videoBytes);
  }

  onStatus("Extracting cover frame…");
  const cover = await extractCoverFrameWithFallback(videoBytes, options, onStatus);

  onStatus("Building metadata…");
  const headerSegments = [buildExifSegment({ imageWidth: cover.width, imageHeight: cover.height })];
  if (options.enableXiaomi) headerSegments.push(buildXiaomiSegment());
  const xmpPacket = buildMotionPhotoXmp(videoBytes.length);
  headerSegments.push(buildXmpSegmentRaw(xmpPacket));

  const postApp0Segments = options.embedIcc ? buildIccProfileSegment() : [];
  const jfifSegment = buildJfifSegment();

  onStatus("Assembling motion photo…");
  const assembled = assembleMotionPhoto(cover.bytes, videoBytes, headerSegments, postApp0Segments, jfifSegment);

  const validation = validateMotionPhoto(assembled);
  if (!validation.isValid) {
    const failed = Object.entries(validation.checks)
      .filter(([, ok]) => !ok)
      .map(([name]) => name)
      .join(", ");
    throw new Error(`Generated file failed validation: ${failed}`);
  }

  return { bytes: assembled, coverBytes: cover.bytes, dims: { width: cover.width, height: cover.height }, validation };
}

// =====================================================================
// UI wiring
// =====================================================================

window.addEventListener("unhandledrejection", (event) => {
  // Safety net: if any promise rejects with a raw string (as ffmpeg.wasm's
  // own worker does on internal errors) instead of an Error, this keeps
  // it from silently becoming "Uncaught (in promise)" with no useful info
  // in the console.
  console.error("Unhandled rejection:", event.reason);
});

(function () {
  "use strict";

  // ---- Timeline (trim + cover-frame scrubber) ----

  function formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function minTrimGap(duration) {
    return Math.min(0.5, Math.max(0.05, duration * 0.01));
  }

  function clampTrimStart(newStart, trimEnd, duration, gap) {
    gap = gap ?? minTrimGap(duration);
    return Math.max(0, Math.min(newStart, trimEnd - gap));
  }

  function clampTrimEnd(newEnd, trimStart, duration, gap) {
    gap = gap ?? minTrimGap(duration);
    return Math.min(duration, Math.max(newEnd, trimStart + gap));
  }

  function clampPlayhead(newPlayhead, trimStart, trimEnd) {
    return Math.max(trimStart, Math.min(newPlayhead, trimEnd));
  }

  function timeToPercent(time, duration) {
    if (!duration || duration <= 0) return 0;
    return Math.max(0, Math.min(100, (time / duration) * 100));
  }

  function pointerXToTime(clientX, rect, duration) {
    if (!rect.width) return 0;
    const fraction = (clientX - rect.left) / rect.width;
    return Math.max(0, Math.min(duration, fraction * duration));
  }

  function computeAutoPickTime(trimStart, trimEnd) {
    const span = trimEnd - trimStart;
    const offset = Math.min(1, span * 0.2);
    return trimStart + offset;
  }

  function filmstripSliceRect(index, count, totalWidth) {
    const sliceWidth = totalWidth / count;
    const x = Math.round(index * sliceWidth);
    const nextX = Math.round((index + 1) * sliceWidth);
    return { x, width: Math.max(1, nextX - x) };
  }

  function filmstripSampleTime(index, count, duration) {
    return ((index + 0.5) / count) * duration;
  }

  /**
   * Drives the trim/playhead timeline widget: two draggable trim handles
   * mark the range that will be embedded, and a draggable playhead line
   * (constrained to that range) marks which frame becomes the Motion
   * Photo's still cover image.
   */
  class TimelineController {
    constructor(els, callbacks) {
      this.els = els;
      this.onPreviewNeeded = callbacks.onPreviewNeeded || (() => {});
      this.onChange = callbacks.onChange || (() => {});
      this.duration = 0;
      this.trimStart = 0;
      this.trimEnd = 0;
      this.playhead = 0;
      this._wireDrag();
    }

    setDuration(duration) {
      this.duration = duration;
      this.trimStart = 0;
      this.trimEnd = duration;
      this.playhead = computeAutoPickTime(0, duration);
      this.render();
    }

    setPlayhead(time, { preview = true } = {}) {
      this.playhead = clampPlayhead(time, this.trimStart, this.trimEnd);
      this.render();
      if (preview) this.onPreviewNeeded(this.playhead);
      this.onChange();
    }

    setTrimStart(time) {
      this.trimStart = clampTrimStart(time, this.trimEnd, this.duration);
      if (this.playhead < this.trimStart) this.setPlayhead(this.trimStart, { preview: false });
      this.render();
      this.onChange();
    }

    setTrimEnd(time) {
      this.trimEnd = clampTrimEnd(time, this.trimStart, this.duration);
      if (this.playhead > this.trimEnd) this.setPlayhead(this.trimEnd, { preview: false });
      this.render();
      this.onChange();
    }

    autoPick() {
      this.setPlayhead(computeAutoPickTime(this.trimStart, this.trimEnd));
    }

    /** True trim range differs meaningfully from "the whole video" -- i.e. trimming is actually needed. */
    isTrimmed() {
      const gap = minTrimGap(this.duration);
      return this.trimStart > gap || this.trimEnd < this.duration - gap;
    }

    render() {
      const { maskLeft, maskRight, trimLeft, trimRight, playhead, trimStartLabel, trimEndLabel, playheadLabel } =
        this.els;
      const startPct = timeToPercent(this.trimStart, this.duration);
      const endPct = timeToPercent(this.trimEnd, this.duration);
      const playPct = timeToPercent(this.playhead, this.duration);

      maskLeft.style.left = "0%";
      maskLeft.style.width = startPct + "%";
      maskRight.style.left = endPct + "%";
      maskRight.style.width = 100 - endPct + "%";
      trimLeft.style.left = startPct + "%";
      trimRight.style.left = endPct + "%";
      playhead.style.left = playPct + "%";

      trimStartLabel.textContent = formatTime(this.trimStart);
      trimEndLabel.textContent = formatTime(this.trimEnd);
      playheadLabel.textContent = formatTime(this.playhead);
    }

    _wireDrag() {
      this._bindHandle(this.els.trimLeft, (time) => this.setTrimStart(time));
      this._bindHandle(this.els.trimRight, (time) => this.setTrimEnd(time));
      this._bindHandle(this.els.playhead, (time) => this.setPlayhead(time));
    }

    _bindHandle(el, onDrag) {
      const timelineEl = this.els.timeline;
      const onMove = (clientX) => {
        const rect = timelineEl.getBoundingClientRect();
        onDrag(pointerXToTime(clientX, rect, this.duration));
      };
      el.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        el.focus();
        onMove(e.clientX);
        const onPointerMove = (ev) => onMove(ev.clientX);
        const onPointerUp = () => {
          window.removeEventListener("pointermove", onPointerMove);
          window.removeEventListener("pointerup", onPointerUp);
        };
        window.addEventListener("pointermove", onPointerMove);
        window.addEventListener("pointerup", onPointerUp);
      });
      // Basic keyboard support (arrow keys) for accessibility.
      el.addEventListener("keydown", (e) => {
        const step = e.shiftKey ? 1 : 0.1;
        if (e.key === "ArrowLeft") { e.preventDefault(); onDrag(this._currentTimeFor(el) - step); }
        if (e.key === "ArrowRight") { e.preventDefault(); onDrag(this._currentTimeFor(el) + step); }
      });
    }

    _currentTimeFor(el) {
      if (el === this.els.trimLeft) return this.trimStart;
      if (el === this.els.trimRight) return this.trimEnd;
      return this.playhead;
    }
  }

  /** Build a filmstrip of evenly-spaced thumbnails into the timeline's canvas. */
  async function buildFilmstrip(videoEl, canvas, sampleCount) {
    const dpr = window.devicePixelRatio || 1;
    const cssWidth = canvas.clientWidth || 600;
    const cssHeight = canvas.clientHeight || 56;
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    const ctx = canvas.getContext("2d");
    const duration = videoEl.duration || 0;

    for (let i = 0; i < sampleCount; i++) {
      const time = filmstripSampleTime(i, sampleCount, duration);
      await seekVideo(videoEl, time);
      const { x, width } = filmstripSliceRect(i, sampleCount, canvas.width);
      // Cover-fit the frame into the slice (crop rather than squish).
      const vw = videoEl.videoWidth || 1;
      const vh = videoEl.videoHeight || 1;
      const sliceAspect = width / canvas.height;
      const videoAspect = vw / vh;
      let sx, sy, sw, sh;
      if (videoAspect > sliceAspect) {
        sh = vh;
        sw = vh * sliceAspect;
        sx = (vw - sw) / 2;
        sy = 0;
      } else {
        sw = vw;
        sh = vw / sliceAspect;
        sx = 0;
        sy = (vh - sh) / 2;
      }
      ctx.drawImage(videoEl, sx, sy, sw, sh, x, 0, width, canvas.height);
    }
  }

  function seekVideo(videoEl, time) {
    return new Promise((resolve, reject) => {
      const onSeeked = () => {
        videoEl.removeEventListener("error", onError);
        resolve();
      };
      const onError = () => {
        videoEl.removeEventListener("seeked", onSeeked);
        reject(new Error("Couldn't read a frame from this video."));
      };
      videoEl.addEventListener("seeked", onSeeked, { once: true });
      videoEl.addEventListener("error", onError, { once: true });
      videoEl.currentTime = time;
    });
  }

  // ---- Generic drag & drop wiring ----
  function wireDropZone(dropEl, inputEl, onFiles) {
    dropEl.addEventListener("click", () => inputEl.click());
    dropEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        inputEl.click();
      }
    });
    inputEl.addEventListener("change", () => {
      if (inputEl.files.length) onFiles(Array.from(inputEl.files));
    });
    ["dragenter", "dragover"].forEach((evt) =>
      dropEl.addEventListener(evt, (e) => {
        e.preventDefault();
        dropEl.classList.add("dragover");
      })
    );
    ["dragleave", "drop"].forEach((evt) =>
      dropEl.addEventListener(evt, (e) => {
        e.preventDefault();
        dropEl.classList.remove("dragover");
      })
    );
    dropEl.addEventListener("drop", (e) => {
      const files = Array.from(e.dataTransfer.files || []);
      if (files.length) onFiles(files);
    });
  }

  // ---- Main convert flow ----
  const fileListEl = document.getElementById("fileList");
  const progressWrap = document.getElementById("progressWrap");
  const progressLabel = document.getElementById("progressLabel");
  const progressFill = document.getElementById("progressFill");
  const convertBtn = document.getElementById("convertBtn");
  const previewBtn = document.getElementById("previewBtn");
  const stageEl = document.getElementById("stage");
  const stagePreviewImg = document.getElementById("stagePreviewImg");
  const stageLoading = document.getElementById("stageLoading");
  const timelineWrapEl = document.getElementById("timelineWrap");
  const advNoteBatch = document.getElementById("advNoteBatch");
  const advTimestamp = document.getElementById("advTimestamp");
  const advFrame = document.getElementById("advFrame");
  const advTrimStart = document.getElementById("advTrimStart");
  const advTrimEnd = document.getElementById("advTrimEnd");

  let selectedFiles = [];
  let timelineVideoEl = null; // offscreen <video> backing the active timeline, if any
  let timelineActive = false;
  let previewSeq = 0; // guards against out-of-order async preview updates

  const timeline = new TimelineController(
    {
      timeline: document.getElementById("timeline"),
      maskLeft: document.getElementById("maskLeft"),
      maskRight: document.getElementById("maskRight"),
      trimLeft: document.getElementById("trimLeft"),
      trimRight: document.getElementById("trimRight"),
      playhead: document.getElementById("playhead"),
      trimStartLabel: document.getElementById("trimStartLabel"),
      trimEndLabel: document.getElementById("trimEndLabel"),
      playheadLabel: document.getElementById("playheadLabel"),
    },
    {
      onPreviewNeeded: (time) => scheduleTimelinePreview(time),
      onChange: () => syncAdvancedFieldsFromTimeline(),
    }
  );

  function syncAdvancedFieldsFromTimeline() {
    advTimestamp.value = timeline.playhead.toFixed(2);
    advTrimStart.value = timeline.trimStart > 0.005 ? timeline.trimStart.toFixed(2) : "";
    advTrimEnd.value =
      timeline.trimEnd < timeline.duration - 0.005 ? timeline.trimEnd.toFixed(2) : "";
  }

  let previewDebounceTimer = null;
  function scheduleTimelinePreview(time) {
    clearTimeout(previewDebounceTimer);
    previewDebounceTimer = setTimeout(() => renderTimelinePreviewFrame(time), 80);
  }

  async function renderTimelinePreviewFrame(time) {
    if (!timelineVideoEl) return;
    const mySeq = ++previewSeq;
    try {
      await seekVideo(timelineVideoEl, time);
      if (mySeq !== previewSeq) return; // a newer drag position superseded this one
      const canvas = document.createElement("canvas");
      canvas.width = timelineVideoEl.videoWidth;
      canvas.height = timelineVideoEl.videoHeight;
      canvas.getContext("2d").drawImage(timelineVideoEl, 0, 0);
      canvas.toBlob((blob) => {
        if (mySeq !== previewSeq || !blob) return;
        stagePreviewImg.src = URL.createObjectURL(blob);
      }, "image/jpeg", 0.9);
    } catch (e) {
      console.warn("Timeline preview frame failed:", errorMessage(e));
    }
  }

  async function loadTimelineForFile(file) {
    timelineActive = false;
    stageEl.hidden = false;
    timelineWrapEl.hidden = true;
    advNoteBatch.hidden = true;
    stageLoading.classList.remove("hidden");
    stagePreviewImg.removeAttribute("src");

    if (timelineVideoEl) {
      URL.revokeObjectURL(timelineVideoEl.src);
      timelineVideoEl = null;
    }

    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.src = URL.createObjectURL(file);

    try {
      await new Promise((resolve, reject) => {
        video.addEventListener("loadedmetadata", resolve, { once: true });
        video.addEventListener("error", () => reject(new Error("Couldn't read this video file.")), { once: true });
      });

      timelineVideoEl = video;
      timeline.setDuration(video.duration || 0);
      syncAdvancedFieldsFromTimeline();

      const canvas = document.getElementById("filmstrip");
      const sampleCount = window.innerWidth < 480 ? 8 : 14;
      await buildFilmstrip(video, canvas, sampleCount);

      await renderTimelinePreviewFrame(timeline.playhead);

      timelineWrapEl.hidden = false;
      timelineActive = true;
    } catch (err) {
      console.warn("Couldn't set up the timeline for this video:", errorMessage(err));
      stageEl.hidden = true;
      timelineActive = false;
    } finally {
      stageLoading.classList.add("hidden");
    }
  }

  function clearTimeline() {
    timelineActive = false;
    if (timelineVideoEl) {
      URL.revokeObjectURL(timelineVideoEl.src);
      timelineVideoEl = null;
    }
    stageEl.hidden = true;
    timelineWrapEl.hidden = true;
  }

  function handleFileSelectionChanged() {
    if (selectedFiles.length === 1) {
      advNoteBatch.hidden = true;
      loadTimelineForFile(selectedFiles[0].file);
    } else {
      clearTimeline();
      advNoteBatch.hidden = selectedFiles.length <= 1;
    }
  }

  document.getElementById("autoPickBtn").addEventListener("click", () => {
    if (timelineActive) timeline.autoPick();
  });

  function renderFileList() {
    fileListEl.innerHTML = "";
    selectedFiles.forEach((entry, idx) => {
      const row = document.createElement("div");
      row.className = "file-row";
      row.innerHTML = `
        <span class="name">${escapeHtml(entry.file.name)}</span>
        <span class="status" data-idx="${idx}">${entry.status}</span>
      `;
      if (entry.downloadUrl) {
        const a = document.createElement("a");
        a.className = "dl";
        a.href = entry.downloadUrl;
        a.download = entry.outputName;
        a.textContent = "Download";
        row.appendChild(a);
      }
      fileListEl.appendChild(row);
    });
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  wireDropZone(document.getElementById("dropZone"), document.getElementById("fileInput"), (files) => {
    selectedFiles = files.map((file) => ({ file, status: "Ready", downloadUrl: null, outputName: null }));
    renderFileList();
    handleFileSelectionChanged();
  });

  function setProgress(label, indeterminate) {
    progressWrap.classList.add("show");
    progressLabel.textContent = label;
    progressFill.style.width = indeterminate ? "40%" : "0%";
  }

  function hideProgress() {
    progressWrap.classList.remove("show");
  }

  /**
   * Cover/trim options for the current conversion(s). Uses the live
   * timeline when it's active for a single selected file; otherwise
   * falls back to the Advanced numeric fields (used as-is for batch
   * conversions, since one timeline can't represent several videos of
   * different lengths at once).
   */
  function getAllOptions() {
    let timestamp = "0.0";
    let trimStart = "";
    let trimEnd = "";

    if (timelineActive) {
      timestamp = String(timeline.playhead);
      if (timeline.isTrimmed()) {
        trimStart = String(timeline.trimStart);
        trimEnd = String(timeline.trimEnd);
      }
    } else {
      timestamp = advTimestamp.value.trim() || "0.0";
      trimStart = advTrimStart.value.trim();
      trimEnd = advTrimEnd.value.trim();
    }

    const frameStr = advFrame.value.trim();
    const frameIndex = frameStr === "" ? null : parseInt(frameStr, 10);

    return {
      timestamp,
      frameIndex,
      auto: false,
      stripAudio: !document.getElementById("keepAudio").checked,
      enableXiaomi: document.getElementById("enableXiaomi").checked,
      embedIcc: document.getElementById("embedIcc").checked,
      trimStart,
      trimEnd,
    };
  }

  previewBtn.addEventListener("click", async () => {
    if (!selectedFiles.length) {
      alert("Choose a video first.");
      return;
    }
    previewBtn.disabled = true;
    convertBtn.disabled = true;
    try {
      const opts = getAllOptions();
      setProgress("Extracting frame…", true);
      const videoBytes = new Uint8Array(await selectedFiles[0].file.arrayBuffer());
      const cover = await extractCoverFrameWithFallback(videoBytes, opts, (msg) => setProgress(msg, true));
      const blob = new Blob([cover.bytes], { type: "image/jpeg" });
      stageEl.hidden = false;
      stageLoading.classList.add("hidden");
      stagePreviewImg.src = URL.createObjectURL(blob);
    } catch (err) {
      console.error(err);
      alert("Couldn't preview that frame: " + friendlyErrorMessage(err));
    } finally {
      hideProgress();
      previewBtn.disabled = false;
      convertBtn.disabled = false;
    }
  });

  convertBtn.addEventListener("click", async () => {
    if (!selectedFiles.length) {
      alert("Choose a video first.");
      return;
    }
    convertBtn.disabled = true;
    previewBtn.disabled = true;

    const options = getAllOptions();

    for (let i = 0; i < selectedFiles.length; i++) {
      const entry = selectedFiles[i];
      entry.status = "Working…";
      renderFileList();
      setProgress(`Converting ${entry.file.name}…`, true);
      try {
        const result = await convertVideoToMotionPhoto(entry.file, options, (msg) => {
          progressLabel.textContent = msg;
        });
        const blob = new Blob([result.bytes], { type: "image/jpeg" });
        entry.downloadUrl = URL.createObjectURL(blob);
        entry.outputName = entry.file.name.replace(/\.[^.]+$/, "") + ".jpg";
        entry.status = "Done";
      } catch (err) {
        console.error(err);
        entry.status = "Error: " + friendlyErrorMessage(err);
      }
      renderFileList();

      // Discard the ffmpeg instance between files in a batch (a no-op if
      // this file's options never needed ffmpeg in the first place). Its
      // internal WASM memory has a small fixed limit that can otherwise
      // get exhausted after a handful of conversions in a row; starting
      // each file fresh trades a short reload for much better reliability.
      if (i < selectedFiles.length - 1) await resetFfmpeg();
    }

    hideProgress();
    convertBtn.disabled = false;
    previewBtn.disabled = false;
  });

  // ---- Extract video tool ----
  // ---- Extract video tool ----
  const extractResultEl = document.getElementById("extractResult");

  wireDropZone(document.getElementById("extractDrop"), document.getElementById("extractInput"), async (files) => {
    extractResultEl.innerHTML = "";
    for (const file of files) {
      const row = document.createElement("div");
      row.className = "file-row";
      row.innerHTML = `<span class="name">${escapeHtml(file.name)}</span><span class="status">Reading…</span>`;
      extractResultEl.appendChild(row);

      try {
        const bytes = new Uint8Array(await file.arrayBuffer());
        const [, videoBytes] = splitMotionPhoto(bytes);
        if (!videoBytes.length) {
          row.querySelector(".status").textContent = "No video found in this file";
          row.querySelector(".status").classList.add("error");
          continue;
        }
        const blob = new Blob([videoBytes], { type: "video/mp4" });
        const url = URL.createObjectURL(blob);
        row.querySelector(".status").textContent = "Done";
        row.querySelector(".status").classList.add("ok");
        const a = document.createElement("a");
        a.className = "dl";
        a.href = url;
        a.download = file.name.replace(/\.[^.]+$/, "") + ".mp4";
        a.textContent = "Download video";
        row.appendChild(a);
      } catch (err) {
        console.error(err);
        row.querySelector(".status").textContent = "Error: " + errorMessage(err);
        row.querySelector(".status").classList.add("error");
      }
    }
  });

  // ---- Inspect tool ----
  const inspectReportEl = document.getElementById("inspectReport");

  wireDropZone(document.getElementById("inspectDrop"), document.getElementById("inspectInput"), async (files) => {
    const file = files[0];
    inspectReportEl.style.display = "block";
    inspectReportEl.textContent = "Reading " + file.name + "…";

    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const lines = [];
      lines.push(`${file.name} (${(bytes.length / 1024).toFixed(1)} KB)`, "", "Marker segments:");

      let markers;
      try {
        markers = iterMarkers(bytes);
      } catch (e) {
        lines.push("  <failed to parse markers: " + e.message + ">");
        inspectReportEl.textContent = lines.join("\n");
        return;
      }
      for (const m of markers) {
        lines.push(`  ${m.marker.padEnd(6)} offset=${String(m.offset).padEnd(10)} length=${m.length}`);
      }

      try {
        const [, trailing] = splitMotionPhoto(bytes);
        lines.push("");
        if (trailing.length) {
          const preview = Array.from(trailing.slice(0, 12))
            .map((b) => b.toString(16).padStart(2, "0"))
            .join(" ");
          lines.push(`Trailing data after EOI: ${(trailing.length / 1024).toFixed(1)} KB`);
          lines.push(`  first bytes: ${preview}`);
        } else {
          lines.push("Trailing data after EOI: none");
        }
      } catch (e) {
        /* ignore */
      }

      lines.push("");
      const result = validateMotionPhoto(bytes);
      lines.push("Validation:");
      for (const [name, ok] of Object.entries(result.checks)) {
        const msg = result.messages[name] ? " — " + result.messages[name] : "";
        lines.push(`  [${ok ? "class:check-ok" : "class:check-bad"}] ${name}${msg}`);
      }
      lines.push("");
      lines.push("Overall: " + (result.isValid ? "VALID" : "INVALID"));

      renderInspectReport(lines);
    } catch (err) {
      console.error(err);
      inspectReportEl.textContent = "Error: " + errorMessage(err);
    }
  });

  function renderInspectReport(lines) {
    inspectReportEl.innerHTML = "";
    for (const line of lines) {
      const div = document.createElement("div");
      if (line.startsWith("  [class:check-ok]")) {
        div.className = "check-ok";
        div.textContent = line.replace("  [class:check-ok] ", "  ");
      } else if (line.startsWith("  [class:check-bad]")) {
        div.className = "check-bad";
        div.textContent = line.replace("  [class:check-bad] ", "  ");
      } else {
        div.textContent = line || "\u00A0";
      }
      inspectReportEl.appendChild(div);
    }
  }
})();

