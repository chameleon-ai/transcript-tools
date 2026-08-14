# transcript-tools

Simple transcription tools leveraging [OpenASR](https://github.com/QuintinShaw/openasr), which was chosen as a backend due to its wide-ranging support for state-of-the-art models. My previous tools leveraged [whisper](https://github.com/linto-ai/whisper-timestamped) which is good but locked into a model family that [hasn't been updated in years](https://huggingface.co/openai/whisper-large-v3-turbo). With new transcription models coming out all the time, OpenASR looks to be the most promising solution that unifies multiple architectures with potential support for future models.

OpenASR is usable by itself, but for convenience, I've developed a thin wrapper that does exactly what I need it to do: make .vtt and .json transcripts. With an alias to the `transcribe.py` script, all I need to do in the command-line is type `transcribe input.mp4` and out pops the transcript files.

Developed on linux with python 3.14. Probably works on Windows. Developed with the assistance of [opencode](https://opencode.ai/) using [Qwen3.6](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) and [Muse Glimmer](https://huggingface.co/meta-models/Muse-Glimmer-30B).

## Description

`transcribe.py` transcribes audio/video files using a local OpenASR server. It automatically starts the server locally, sends an API request to transcribe, stops the server, and writes outputs:

- `*<.lang>.vtt` — [WebVTT](https://www.w3.org/TR/webvtt1/) subtitles
- `*.json` — Includes timestamps at the single word level

## Requirements

- `transcribe.py`:
  - Only uses the python standard library. No virtual environment setup is necessary.
  - Depends on [OpenASR](https://github.com/QuintinShaw/openasr). Build it from source or download the [latest release](https://github.com/QuintinShaw/openasr/releases/) and place the `openasr` directory alongside `transcribe.py`. The script will attempt to find the `openasr` executable automatically, but the path may be manually specified with `--openasr-path`
  - You must manually install openasr models before use: `openasr pull <model>`
  - There is a soft dependency on [ffprobe](https://ffmpeg.org/ffprobe.html) to determine the max timeout of the transcription request based on the input duration. Make sure `ffprobe` is in PATH or manually specify via `--ffprobe-path`. If missing, a long duration is used as a fallback.

## Usage

```
python transcribe.py [-v] <file>...
```

Options:
- `--ffprobe-path PATH` - path to ffprobe directory
- `--model` - model to use, default `whisper-large-v3-turbo`. Tested models:
  - [cohere-transcribe-03-2026](https://huggingface.co/OpenASR/cohere-transcribe-03-2026)
  - [firered-aed-l-v2](https://huggingface.co/OpenASR/firered-aed-l-v2)
  - [firered2-llm](https://huggingface.co/OpenASR/firered2-llm)
  - [mimo-v2.5-asr](https://huggingface.co/OpenASR/mimo-v2.5-asr)
  - [moonshine-tiny](https://huggingface.co/OpenASR/moonshine-tiny)
  - [moss-transcribe-diarize](https://huggingface.co/OpenASR/moss-transcribe-diarize)
  - [qwen3-asr-0.6b](https://huggingface.co/OpenASR/qwen3-asr-0.6b)
  - [qwen3-asr-1.7b](https://huggingface.co/OpenASR/qwen3-asr-1.7b)
  - [whisper-large-v3-turbo](https://huggingface.co/OpenASR/whisper-large-v3-turbo) 
- `--openasr-path PATH` - path to openasr directory
- `--port INT` - default 8080
- `--timeout-multiplier FLOAT` - default 0.25. Multiply the audio duration by this value to determine the http request timeout. Value < 1 means it is expected to complete faster than real time, which is usually the case unless the machine is underpowered and doing something like CPU inference.
- `-v/--verbose` - print server output and progress

Positional unknown args are treated as input files. Audio/video types detected via mimetypes.

Example:
```
python transcribe.py audio.mp3
python transcribe.py -v --model qwen3-asr-0.6b video.mp4 audio.wav
```

Outputs per input file `<stem>`:
- `<stem><.lang>.vtt` — WebVTT cues, deduplicated
- `<stem>.json` — verbose JSON with duration, text, segments, model, language
