# Vendored files

`ffmpeg.js` and `814.ffmpeg.js` are the official browser (UMD) build of
[ffmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm)
(`@ffmpeg/ffmpeg` v0.12.10, MIT License), copied here unmodified.

They're hosted locally (instead of loaded from a CDN) because the
library spawns a Web Worker internally, and browsers require a Worker's
script to be same-origin with the page — a CDN-hosted script fails with
an error like:

```
Failed to construct 'Worker': Script at 'https://.../814.ffmpeg.js'
cannot be accessed from origin '...'
```

The much larger ffmpeg core (`ffmpeg-core.js` / `ffmpeg-core.wasm`,
~30MB) is still loaded from a CDN at runtime — that part is fetched as
a blob first, which isn't subject to the same restriction.
