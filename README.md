# V2MP

V2MP converts ordinary MP4 videos into a **Google Motion Photo** — a
single `.jpg` file that looks like a normal photo but can also play back
as a short video in Google Photos, Xiaomi Gallery, and other compatible
Android gallery apps.

The output is just **one `.jpg` file**, no extra files.

## 🌐 Try it in your browser — no install needed

**[Open the web app](https://fau-1o.github.io/V2MP/)**

Everything runs locally on your own device (powered by
[ffmpeg.wasm](https://ffmpegwasm.netlify.app/)) — no video is ever
uploaded anywhere. Works on both desktop and mobile.

<details>
<summary>How to turn on this link for your own copy of the repo</summary>

1. Push this repo to GitHub.
2. Go to **Settings → Pages**.
3. Under "Build and deployment", set **Source** to "Deploy from a
   branch", pick the **main** branch and the **/ (root)** folder, then
   **Save**.
4. After a minute, your page will be live at
   `https://<your-username>.github.io/<repo-name>/`.
5. Replace the link at the top of this README with that URL.

</details>

## Installation (command-line version)

Requires Python 3.10+ and `ffmpeg` (make sure `ffmpeg -version` works in
your terminal).

```bash
pip install -r requirements.txt
```

Or install it as a command (`v2mp`) you can run from anywhere:

```bash
pip install -e .
```

## Usage

```bash
# Convert a single video (output: video.jpg in the same folder)
python main.py video.mp4

# Choose the output filename
python main.py video.mp4 output.jpg

# Convert every video in a folder
python main.py video_folder/

# Convert every video, including subfolders
python main.py video_folder/ --recursive
```

If you installed it with `pip install -e .`, replace `python main.py`
with `v2mp` in all the examples above.

### Choosing which frame becomes the photo

```bash
python main.py video.mp4 --cover-timestamp 2.5   # use the frame at 2.5s
python main.py video.mp4 --cover-frame 42        # use frame 42 exactly
python main.py video.mp4 --cover-auto            # automatically pick the best frame
```

### Audio

```bash
python main.py video.mp4              # audio kept (default)
python main.py video.mp4 --no-audio   # output video has no audio
```

### Other features

```bash
# Preview which frame will become the photo, without building the full motion photo
python main.py video.mp4 --preview-cover preview.jpg

# Pull the video back out of an existing motion photo
python main.py output.jpg --extract-video original_video.mp4

# Check the structure & validity of a motion photo file
python main.py output.jpg --inspect

# Trim the video before embedding it (0s to 3s)
python main.py video.mp4 --trim-start 0 --trim-end 3

# Convert many files at once (faster)
python main.py video_folder/ --jobs 4
```

See all options with:

```bash
python main.py --help
```

## Web app feature parity

The web app is three files: `index.html` (markup), `style.css`
(styling), and `app.js` (all the logic — JPEG/XMP building, ffmpeg
wrapper, and UI wiring). The `vendor/` folder holds a couple of small
files from the [ffmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm)
project itself (see `vendor/README.md` for why they need to live in this
repo instead of being loaded from a CDN). You don't need to touch any of
these to just use the app — they only matter if you're modifying it.

The web app (`index.html`) mirrors the CLI:

- **A timeline editor** — drag the two side handles to trim the video,
  and drag the center line to pick exactly which frame becomes the still
  photo, with a live preview as you drag (single-file mode)
- Keep or strip audio
- Toggle the Xiaomi compatibility tag / ICC color profile
- Convert multiple files in one go (an "Advanced" panel lets you set one
  timestamp/trim range that applies to the whole batch)
- Extract the video back out of an existing Motion Photo
- Inspect a file's structure and validation report

**How cover frame extraction works:** the web app grabs the cover frame
using the browser's own video decoder (an offscreen `<video>` +
`<canvas>`), not ffmpeg. ffmpeg.wasm's single-threaded core has a small,
*fixed* WASM memory ceiling (unrelated to the device's actual RAM), and
decoding frames is exactly the kind of operation that can exceed it on
long or high-resolution videos. The browser's native decoder has no such
ceiling, so this is what makes long videos work reliably in the browser,
much like the CLI. The trade-off: "by frame number" becomes an
approximate seek (assumes 30fps) rather than ffmpeg's exact frame
count, and "auto-pick" just avoids frame 0 instead of comparing frames
for sharpness. ffmpeg.wasm is still used as a fallback if the browser
can't decode a particular video's codec, and remains the only way trim /
audio-removal are done (both need real video-processing, not just
decoding a single frame).

## Testing (optional)

To run the test suite:

```bash
pip install pytest
pytest
```

## License

MIT — free to use, modify, and distribute.
