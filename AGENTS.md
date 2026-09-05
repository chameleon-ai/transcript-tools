# transcript-tools

Personal transcription frontend for OpenASR.

## Repo layout
- `transcribe.py` — single CLI tool using only python standard library. No other source code.
- Output files are ignored by `.gitignore`: `*.wav *.mp3 *.webm *.mp4 *.mkv *.opus *.vtt *.json`.

## Run
Start server + transcribe:
```bash
python transcribe.py [--model qwen3-asr-0.6b|qwen3-asr-1.7b] [-v] [--openasr-path PATH] <file>...
```
- Positional unknown args are treated as input files
- Server starts automatically on `127.0.0.1:8080` with `--backend native --model <model>` and is stopped after work completes.
- `-v/--verbose` prints server output and progress.

Required:
- OpenASR binary found via `shutil.which('openasr')` or sibling dir `openasr*/openasr`.
- Model must be installed locally first: `openasr pull <model>`; otherwise script errors with install hint.
- `ffprobe` in PATH or via `--ffprobe-path`; if missing duration defaults to 43200s.

Outputs per input file `<stem>`:
- `<stem><.lang>.vtt` — WebVTT cues, with short/adjacent cues compacted into fuller lines.
- `<stem>.json` — verbose JSON with duration, text, segments, model, language.

Other flags:
- `--openasr-path` overrides binary search.
- `--ffprobe-path` path to ffprobe directory.
- `--timeout-multiplier` default 0.25, multiplied by audio duration +10s for HTTP timeout.

## Notes
- Script polls `/v1/audio/transcriptions/{session_id}/progress` and retries 429 up to 3 times.
- No tests, lint, or build steps in repo.
