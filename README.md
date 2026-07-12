# V2MP

V2MP converts ordinary MP4 videos into a **Google Motion Photo** — a
single `.jpg` file that looks like a normal photo but can also play back
as a short video in Google Photos, Xiaomi Gallery, and other compatible
Android gallery apps.

The output is just **one `.jpg` file**, no extra files.

## Installation

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

## Testing (optional)

To run the test suite:

```bash
pip install pytest
pytest
```

## License

MIT — free to use, modify, and distribute.
