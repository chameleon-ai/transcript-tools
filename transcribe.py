import argparse
import io
import json
import mimetypes
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid

LANGUAGE_NAME_TO_ISO = {
    'afrikaans': 'af',
    'arabic': 'ar',
    'armenian': 'hy',
    'azerbaijani': 'az',
    'belarusian': 'be',
    'bosnian': 'bs',
    'bulgarian': 'bg',
    'catalan': 'ca',
    'chinese': 'zh',
    'cantonese': 'yue',
    'croatian': 'hr',
    'czech': 'cs',
    'danish': 'da',
    'dutch': 'nl',
    'english': 'en',
    'estonian': 'et',
    'finnish': 'fi',
    'french': 'fr',
    'galician': 'gl',
    'german': 'de',
    'greek': 'el',
    'hebrew': 'he',
    'hindi': 'hi',
    'hungarian': 'hu',
    'icelandic': 'is',
    'indonesian': 'id',
    'italian': 'it',
    'japanese': 'ja',
    'kannada': 'kn',
    'kazakh': 'kk',
    'korean': 'ko',
    'latvian': 'lv',
    'lithuanian': 'lt',
    'macedonian': 'mk',
    'malay': 'ms',
    'maori': 'mi',
    'marathi': 'mr',
    'nepali': 'ne',
    'norwegian': 'no',
    'persian': 'fa',
    'polish': 'pl',
    'portuguese': 'pt',
    'romanian': 'ro',
    'russian': 'ru',
    'serbian': 'sr',
    'slovak': 'sk',
    'slovenian': 'sl',
    'spanish': 'es',
    'swahili': 'sw',
    'swedish': 'sv',
    'tagalog': 'tl',
    'filipino': 'tl',
    'tamil': 'ta',
    'thai': 'th',
    'turkish': 'tr',
    'ukrainian': 'uk',
    'urdu': 'ur',
    'vietnamese': 'vi',
    'welsh': 'cy',
}

def _language_to_iso(lang):
    if not lang:
        return None
    s = str(lang).strip().lower()
    if len(s) == 2 and s.isalpha():
        return s
    return LANGUAGE_NAME_TO_ISO.get(s)

def fetch_models(base_url: str = "http://127.0.0.1:8080/v1") -> list[str]:
    """Fetch available model IDs from the OpenASR server."""
    url = f"{base_url}/models"
    with urllib.request.urlopen(url) as resp:
        data = json.load(resp)
    return [m["id"] for m in data["data"]]


def _encode_multipart(fields: dict, file_field: tuple) -> tuple[bytes, str]:
    """Encode multipart/form-data body for file upload."""
    boundary = "----OpenASRBoundary" + str(random.randint(0, 1_000_000))
    buf = io.BytesIO()
    def write(s: bytes):
        buf.write(s)
    for key, value in fields.items():
        if isinstance(value, (list, tuple)):
            for v in value:
                write(f"--{boundary}\r\n".encode())
                write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
                write(str(v).encode())
                write(b"\r\n")
        else:
            write(f"--{boundary}\r\n".encode())
            write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            write(str(value).encode())
            write(b"\r\n")
    field_name, (filename, data_bytes, mime_type) = file_field
    write(f"--{boundary}\r\n".encode())
    if mime_type is None:
        mime_type = "application/octet-stream"
    write(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
    )
    write(f"Content-Type: {mime_type}\r\n\r\n".encode())
    if isinstance(data_bytes, bytes):
        buf.write(data_bytes)
    else:
        buf.write(data_bytes.read())
    buf.write(b"\r\n")
    write(f"--{boundary}--\r\n".encode())
    content_type = f"multipart/form-data; boundary={boundary}"
    return buf.getvalue(), content_type


def poll_progress(
    session_id: str, stop_event: threading.Event, port: int = 8080
) -> None:
    """Poll /v1/audio/transcriptions/{session_id}/progress until done."""
    url = (
        f"http://127.0.0.1:{port}/v1/audio/transcriptions/{session_id}/progress"
    )
    last_pct = -1

    deadline = time.monotonic() + 900

    while not stop_event.is_set() and time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                #status = resp.status
                data_bytes = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                time.sleep(1.0)
                continue
            time.sleep(1.0)
            continue
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1.0)
            continue

        data = json.loads(data_bytes)
        phase = str(data.get("phase") or "?idle")
        fraction = float(data.get("fraction", 0))
        pct = int(fraction * 100)

        if stop_event.is_set():
            break

        if pct != last_pct:
            print(f"\rProgress: {pct:>3d}%  phase: {phase}    ", end="", flush=True)
            last_pct = pct

        time.sleep(1.0)


def _unique_path(path: str) -> str:
    """Return a non-colliding path by inserting .1, .2, ... before the extension."""
    directory, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    if os.path.exists(path):
        for n in range(1, 1000):
            candidate = os.path.join(directory, f"{stem}.{n}{ext}")
            if not os.path.exists(candidate):
                return candidate
    return path


def fmt_time(s: float) -> str:
    """Format time in seconds to WebVTT time format HH:MM:SS.mmm."""
    total_ms = int(round(s * 1000))

    h = total_ms // (3600 * 1000)
    total_ms %= (3600 * 1000)
    m = total_ms // (60 * 1000)
    total_ms %= (60 * 1000)
    sec = total_ms // 1000
    ms  = total_ms % 1000

    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"

SENTENCE_END_PUNCT = {'.', '!', '?'}

# VTT lines are built from word-level timings (the subtitle_cues endpoint can
# drop words, the word list never does). Target line quality: a block over
# either MAX_DUR / MAX_CHARS is split at its widest internal word boundary.
MIN_DUR = 2.0      # seconds; blocks under this are "fragments" that keep absorbing
MAX_DUR = 4.5      # target max line duration
MAX_CHARS = 88     # target max line length (~2 rendered lines of 42 chars)
GAP_SOFT = 0.5     # max word gap to extend a well-formed block
GAP_SHORT = 2.5    # max word gap a fragment block may bridge while it is still short
HARD_DUR = 6.5     # absolute cap a single block may reach
HARD_CHARS = 110   # absolute char cap for a single block
MIN_PIECE = 0.8    # seconds; never split a block into a shorter line


def _segment_words(segment: dict) -> list[dict]:
    """Return the segment's words with the segment text's punctuation attached.

    subtitle_cues drops words, so subtitles are built from the word-level
    timings instead. Word strings are bare ("book"), while the segment text
    carries the punctuation ("book?"); the two are 1:1 aligned, so each word's
    surrounding punctuation is re-attached to recover sentence-final markers
    (".", "!", "?", "...", "??") for the sentence-break heuristics. Leading and
    trailing punctuation are the non-alnum/non-apostrophe runs at each end of
    the token, so contractions ("don't", "I'm") stay intact.
    """
    words = segment.get("words", [])
    if not words:
        return []
    tokens = str(segment.get("text", "")).split()
    att = []
    for i, w in enumerate(words):
        bare = str(w.get("word", " ")).strip() or " "
        token = tokens[i].strip() if i < len(tokens) else bare
        a, b = 0, len(token)
        while a < b and not (token[a].isalnum() or token[a] == "'"):
            a += 1
        while b > a and not (token[b - 1].isalnum() or token[b - 1] == "'"):
            b -= 1
        core = token[a:b].lower()
        if core == bare.lower():
            prefix, suffix = token[:a], token[b:]
        else:
            # token/word out of alignment: keep the bare word so no text is lost
            prefix, suffix = "", ""
        att.append({"word": prefix + bare + suffix,
                    "start": float(w["start"]), "end": float(w["end"])})
    return att


def _is_sentence_break(prev_text: str, next_text: str) -> bool:
    return (
        prev_text.rstrip()[-1:] in SENTENCE_END_PUNCT and next_text[:1].isupper()
    )


def _split_block(parts: list[dict], out: list[dict]) -> None:
    """Emit the block as one or more lines, splitting at the most natural
    internal boundary whenever the block exceeds MAX_DUR / MAX_CHARS.

    Candidates are the seams between words whose split leaves at least
    MIN_PIECE seconds on both sides. The best seam ends on sentence-final
    punctuation (. ! ?), then the widest gap, then the most balanced halves.
    The split is applied recursively (halves may themselves be too long).
    """
    start = parts[0]["start"]
    end = parts[-1]["end"]
    text = " ".join(p["word"] for p in parts)

    if end - start <= MAX_DUR and len(text) <= MAX_CHARS or len(parts) < 2:
        out.append({"start": start, "end": end, "text": text})
        return

    candidates = []
    for k in range(1, len(parts)):
        left_dur = parts[k - 1]["end"] - start
        right_dur = end - parts[k]["start"]
        if left_dur < MIN_PIECE or right_dur < MIN_PIECE:
            continue
        sentence_end = 1 if parts[k - 1]["word"].rstrip()[-1:] in SENTENCE_END_PUNCT else 0
        candidates.append(
            (sentence_end, parts[k]["gap"], -abs(left_dur - right_dur), k)
        )
    if not candidates:
        out.append({"start": start, "end": end, "text": text})
        return

    k = max(candidates)[3]
    _split_block(parts[:k], out)
    _split_block(parts[k:], out)


def build_vtt_words(segments: list[dict]) -> list[dict]:
    """Build VTT lines from word-level timings.

    Phase 1 (merge): greedily grow a block from consecutive words across all
    segments (segment boundaries are not real speech boundaries). A block may
    absorb the next word across a gap of GAP_SHORT when it is still shorter
    than MIN_DUR, or across GAP_SOFT once well-formed, up to the HARD caps.
    A well-formed block that ends a sentence (. ! ?) flushes before a word
    that starts with a capital so complete sentences are not glued together.

    Phase 2 (split): any block longer than MAX_DUR or MAX_CHARS is split at
    its most natural internal seam (sentence-final punctuation preferred,
    then the widest timing gap, then the most balanced halves) recursively,
    as long as both resulting lines are at least MIN_PIECE seconds long.
    """
    words: list[dict] = []
    for seg in segments:
        words.extend(_segment_words(seg))
    words.sort(key=lambda w: w["start"])

    lines: list[dict] = []
    parts: list[dict] = []

    def flush():
        nonlocal parts
        if parts:
            _split_block(parts, lines)
            parts = []

    for word in words:
        if not parts:
            parts.append({**word, "gap": 0.0})
            continue

        # silence gap: words overlap in ASR, so clamp to zero
        gap = max(0.0, word["start"] - parts[-1]["end"])
        block_dur = parts[-1]["end"] - parts[0]["start"]
        acc_dur = word["end"] - parts[0]["start"]
        acc_text = " ".join(p["word"] for p in parts) + " " + word["word"]

        if _is_sentence_break(parts[-1]["word"], word["word"]) and block_dur >= MIN_DUR:
            flush()
            parts.append({**word, "gap": 0.0})
            continue

        max_gap = GAP_SHORT if block_dur < MIN_DUR else GAP_SOFT
        if gap <= max_gap and acc_dur <= HARD_DUR and len(acc_text) <= HARD_CHARS:
            parts.append({**word, "gap": gap})
        else:
            flush()
            parts.append({**word, "gap": 0.0})

    flush()
    return lines


def write_vtt_cues(segments: list[dict], output_path: str) -> None:
    """Write word-level timings to a WebVTT file as compacted subtitle lines."""

    cues = build_vtt_words(segments)

    lines = ["WEBVTT", ""]
    for cue in cues:
        text = cue["text"].strip()
        if not text:
            continue
        lines.append(f"{fmt_time(cue['start'])} --> {fmt_time(cue['end'])}")
        lines.append(text)
        lines.append("")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_json_output(result: dict, model: str, output_path: str) -> None:
    """Write a complete JSON transcription with words nested inside each segment."""
    output = dict()
    output["duration"] = result["duration"]
    output["text"] = result["text"] # the entire bulk transcription
    output["segments"] = result["segments"]
    output["model"] = model
    if "language" in result and result["language"]:
        iso = _language_to_iso(result["language"])
        if iso:
            output["language"] = iso

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def get_duration(input_filename,ffprobe_path=None):
    """Get audio/video file duration in seconds using ffprobe."""
    mime_type_result = mimetypes.guess_type(input_filename)
    if not mime_type_result or not mime_type_result[0]:
        raise RuntimeError(f"Could not determine mime type for '{input_filename}'")
    mime, subtype = mime_type_result[0].split('/')
    if mime != 'video' and mime != 'audio':
        raise RuntimeError(f"Unsupported mime type '{mime}/{subtype}' for input file '{input_filename}'")
    ffprobe_exe = os.path.join(ffprobe_path, 'ffprobe') if ffprobe_path else 'ffprobe'
    try:
        # https://superuser.com/questions/650291/how-to-get-video-duration-in-seconds
        result = subprocess.run([ffprobe_exe,"-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_filename], stdout=subprocess.PIPE, text=True)
        duration_seconds = float(result.stdout)
        return duration_seconds
    except FileNotFoundError as e:
        print(e)
        print("Warning: Couldn't execute ffprobe, defaulting to fixed timeout")
    return 43200.0


def find_openasr_binary(override_path=None):
    """Locate the openasr executable in common paths."""
    binary_name = "openasr.exe" if platform.system() == 'Windows' else "openasr"
    if override_path:
        if os.path.isfile(override_path) and os.access(override_path, os.X_OK): # Direct path specified
            return os.path.realpath(override_path)
        # Try finding the executable in the same directory as the specified path
        test_path = os.path.join(override_path, binary_name)
        if os.path.isfile(test_path) and os.access(test_path, os.X_OK):
            return os.path.realpath(test_path)
        raise FileNotFoundError(f"openasr binary not found at specified path: {override_path}")
    path = shutil.which('openasr')
    if path and os.path.isfile(path) and os.access(path, os.X_OK):
        return os.path.realpath(path)
    # Search for executable in directories sibling to this script that start with 'openasr'
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        for entry in os.listdir(script_dir):
            full_entry = os.path.join(script_dir, entry)
            if os.path.isdir(full_entry) and entry.startswith('openasr'):
                candidate = os.path.join(full_entry, binary_name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return os.path.realpath(candidate)
                # Manual repo clone and build places the executable here
                candidate = os.path.join(full_entry, "target", "release", binary_name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return os.path.realpath(candidate)
    except Exception:
        pass
    raise FileNotFoundError("openasr binary not found. Download the latest release from https://github.com/QuintinShaw/openasr/releases/")


def is_server_ready(addr: str, timeout: int = 10) -> bool:
    """Check if OpenASR server health endpoint is responsive."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            url = f"http://{addr}/health"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def start_openasr_server(model: str, addr: str = "127.0.0.1:8080", verbose: bool = False, openasr_path=None):
    """Start OpenASR server process and wait until ready."""
    openasr_bin = find_openasr_binary(override_path=openasr_path)
    if verbose:
        print(f"\nopenasr path found at '{openasr_bin}'")
    cmd = [
        openasr_bin,
        'serve',
        '--addr', addr,
        '--backend', 'native',
        '--model', model,
    ]
    env = os.environ.copy()
    
    if verbose:
        proc = subprocess.Popen(
            cmd,
            stdout=sys.stderr,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            bufsize=1,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )
    
    # Wait for health endpoint to be responsive
    if not is_server_ready(addr):
        proc.terminate()
        raise RuntimeError("OpenASR server failed to start within 30 seconds")
    
    return proc


def stop_openasr_server(proc: subprocess.Popen):
    """Terminate and cleanup OpenASR server process."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    except Exception:
        pass


if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(prog='transcribe', description='Transcribes audio using OpenASR.')
    
    model_list = [ 'cohere-transcribe-03-2026', 'firered-aed-l-v2', 'firered2-llm', 'mimo-v2.5-asr', 'moonshine-tiny', 'moss-transcribe-diarize', 'qwen3-asr-0.6b', 'qwen3-asr-1.7b', 'whisper-large-v3-turbo' ]

    parser.add_argument('--ffmpeg-path', type=str, default=None, help='Path to ffmpeg. If not specified, it is presumed that ffmpeg is in your PATH.')
    parser.add_argument('--ffprobe-path', type=str, default=None, help='Path to ffprobe. If not specified, it is presumed that ffprobe is in your PATH.')
    parser.add_argument('-m', '--model', type=str, default='qwen3-asr-1.7b', choices=model_list, help='Run through all calculations but do not render the video.')
    parser.add_argument('--openasr-path', type=str, default=None, help='Path to openasr binary. If not specified, will search PATH for openasr command (resolves aliases via shutil.which) or fail.')
    parser.add_argument('--port', type=int, default=8080, help='Port to run OpenASR server on')
    parser.add_argument('--timeout-multiplier',type=float, default=0.25, help='Multiply the audio duration by this value to determine the http request timeout. Value < 1 means it is expected to complete faster than real time.')
    parser.add_argument('-v', '--verbose', action='store_true', help='Print OpenASR server output')
    parser.add_argument('--vtt-only', action='store_true', help='Only output .vtt file, skip .json')
    parser.add_argument('--json-only', action='store_true', help='Only output .json file, skip .vtt')
    
    args, unknown_args = parser.parse_known_args()
    
    if len(unknown_args) == 0:
        parser.print_help()
        sys.exit(0)
    
    if args.vtt_only and args.json_only:
        print('--vtt-only and --json-only are mutually exclusive flags')
        sys.exit(0)
    
    server_proc = None
    temp_audio = None
    try:
        if args.verbose:
            print("Starting OpenASR server...", end="") 
        addr = f"127.0.0.1:{args.port}"
        server_proc = start_openasr_server(args.model, addr=addr, verbose=args.verbose, openasr_path=args.openasr_path)
        if args.verbose:
            print(" done.")
        
        available_models = fetch_models(base_url=f"http://127.0.0.1:{args.port}/v1")
        if args.verbose:
            print(f"Available models: {available_models}")

        if args.model not in available_models:
            # search for model substring in case it's not an exact match
            # this is mostly a workaround for mimo-v2.5-asr where it reports as 'mimo-v2.5-asr-q4k'
            found = False
            for model in available_models: 
                if args.model in model:
                    found = True
                    args.model = model
                    break
            if not found:
                raise RuntimeError(
                    f"The selected model '{args.model}' isn't available, please install with "
                    f"'openasr pull {args.model}'\n"
                )
        
        # Reject unknown arguments to avoid confusion or typos
        for arg in unknown_args:
            if not os.path.isfile(arg):
                raise RuntimeError(f"Unknown argument '{arg}'")

        # Presumably the other unknown arguments are input files to process
        for arg in unknown_args:
            source_path = arg
            filename = arg
            mime_type, _ = mimetypes.guess_type(filename)

            # Need to determine a timeout for the transcription request
            # Assuming the transcription happens faster than real-time, we can base
            # the worst case on the total file duration and a constant factor (like 4x).
            # This may have to be adjusted depending on the platform and transcription model.
            duration = get_duration(filename, ffprobe_path=args.ffprobe_path)
            client_timeout = max(1.0, duration * args.timeout_multiplier + 10.0) # Add a fixed duration buffer
            if args.verbose:
                print(f"Client timeout is {client_timeout} seconds")

            # Note: For qwen3-asr models (0.6b and 1.7b), the qwen3-forcedaligner-0.6b runs
            # if the specified granularity is "word_aligned".
            word_granularity = "word_aligned" if "qwen3-asr" in args.model else "word"
            stem = os.path.splitext(os.path.basename(filename))[0]

            if args.verbose:
                print(f"{mime_type} {filename}")
            else:
                print(filename)

            # The OpenASR native backend only prepares recognized audio extensions
            # (wav, mp3, mp4, m4a, m4b, mov, webm, flac, ogg, opus, aac, ...).
            # Unrecognized video containers (mkv, avi, ts, ...) are rejected, so we
            # extract the audio stream by remuxing it into a container whose
            # extension the server recognizes (audio copy, no re-encoding).
            audio_ext = os.path.splitext(filename)[1].lower()
            recognized = {'.wav', '.mp3', '.mp4', '.m4a', '.m4b', '.mov', '.webm',
                          '.flac', '.ogg', '.opus', '.aif', '.aiff', '.caf', '.wma', '.amr'}
            if mime_type and not (mime_type.startswith("audio/") and audio_ext in recognized):
                if not shutil.which("ffmpeg") and not args.ffmpeg_path:
                    raise RuntimeError(
                        "Cannot process this input: ffmpeg is required to extract the audio stream. "
                        "Install ffmpeg or add its location with --ffmpeg-path."
                    )
                ffprobe_exe = os.path.join(args.ffprobe_path, "ffprobe") if args.ffprobe_path else "ffprobe"
                stream_probe = subprocess.run(
                    [ffprobe_exe, "-v", "error", "-select_streams", "a:0",
                     "-show_entries", "stream=codec_name",
                     "-of", "default=noprint_wrappers=1:nokey=1", filename],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                codec = stream_probe.stdout.strip().splitlines()[0] if stream_probe.stdout.strip() else ""
                # map audio codec -> container extension the server recognizes
                copy_ext = {
                    'aac': '.m4a',
                    'mp3': '.mp3',
                    'vorbis': '.ogg',
                    'opus': '.opus',
                    'flac': '.flac',
                    'pcm_s16le': '.wav',
                    'pcm_f32le': '.wav',
                }.get(codec)
                stem, _ = os.path.splitext(os.path.basename(filename))
                tempdir = tempfile.mkdtemp(prefix="transcribe_")
                temp_audio = os.path.join(tempdir, stem + (copy_ext or ".wav"))
                ffmpeg_exe = os.path.join(args.ffmpeg_path, "ffmpeg") if args.ffmpeg_path else "ffmpeg"
                if args.verbose:
                    print(f"Extracting audio from {filename} to {temp_audio} (codec: {codec or 'unknown'})")
                if copy_ext:
                    extract_cmd = [ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-i", filename,
                                   "-map", "0:a:0", "-c:a", "copy", temp_audio]
                else:
                    # unknown codec: transcode instead of copy
                    extract_cmd = [ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-i", filename,
                                   "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", temp_audio]
                extract = subprocess.run(
                    extract_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if extract.returncode != 0:
                    os.unlink(temp_audio)
                    shutil.rmtree(tempdir, ignore_errors=True)
                    raise RuntimeError(
                        f"ffmpeg failed to extract audio from {filename}: {extract.stderr.strip()}"
                    )
                mime_type, _ = mimetypes.guess_type(temp_audio)
                filename = temp_audio
                output_dir = os.path.dirname(source_path) or "."
            else:
                output_dir = os.path.dirname(filename) or "."

            session_id = str(uuid.uuid4())

            stop_event = threading.Event()
            t = threading.Thread(
                target=poll_progress,
                args=(session_id, stop_event, args.port),
                daemon=True,
            )
            t.start()

            with open(filename, "rb") as f:
                file_bytes = f.read()

            fields = {
                "model": args.model,
                "response_format": "verbose_json",
                "timestamp_granularities[]": ["segment", word_granularity],
                "transcription_id": session_id,
            }
            if args.verbose:
                print(fields)
            body, content_type = _encode_multipart(fields, ("file", (filename, file_bytes, mime_type)))

            url = f"http://127.0.0.1:{args.port}/v1/audio/transcriptions"
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", content_type)
            max_retries = 6
            attempt = 0
            last_error = None
            while True:
                try:
                    with urllib.request.urlopen(req, timeout=client_timeout) as resp:
                        status = resp.status
                        result_bytes = resp.read()
                    break
                except urllib.error.HTTPError as e:
                    if (e.code == 429 or e.code == 409) and attempt < max_retries - 1:
                        attempt += 1
                        if args.verbose:
                            error_body = e.read()
                            try:
                                err_obj = json.loads(error_body.decode())
                                error_msg = err_obj.get("error", {}).get("message", str(e))
                            except Exception:
                                error_msg = error_body.decode(errors="ignore")
                            print(f"429 Too Many Requests, retrying {attempt}/{max_retries - 1}...", file=sys.stderr)
                        time.sleep(0.5 * attempt)
                        continue
                    error_body = e.read()
                    try:
                        err_obj = json.loads(error_body.decode())
                        error = err_obj.get("error", {})
                    except Exception:
                        error = {"message": error_body.decode(errors="ignore")}
                    raise RuntimeError(f"Transcription failed: {error}")

            stop_event.set()
            try:
                t.join(timeout=1.0)
            except KeyboardInterrupt:
                pass

            print()
            result = json.loads(result_bytes)
            segments = result.get("segments", [])

            if not segments:
                print("No segments found in response.")
                if temp_audio is not None and os.path.exists(temp_audio):
                    os.unlink(temp_audio)
                    shutil.rmtree(os.path.dirname(temp_audio), ignore_errors=True)
                    temp_audio = None
                continue
            words = result.get("words", [])

            if not args.json_only:
                lang_iso = _language_to_iso(result.get("language")) if "language" in result else None
                lang_suffix = f".{lang_iso}" if lang_iso else ""
                output_vtt = _unique_path(os.path.join(output_dir, f"{stem}{lang_suffix}.vtt"))
                write_vtt_cues(segments, output_vtt)
                print(f"Wrote {output_vtt}")

            if not args.vtt_only:
                output_json = _unique_path(os.path.join(output_dir, f"{stem}.json"))
                write_json_output(result, args.model, output_json)
                print(f"Wrote {output_json}")

            # Clean up the extracted temp audio file
            if temp_audio is not None and os.path.exists(temp_audio):
                os.unlink(temp_audio)
                shutil.rmtree(os.path.dirname(temp_audio), ignore_errors=True)
                temp_audio = None
    finally:
        if temp_audio is not None and os.path.exists(temp_audio):
            os.unlink(temp_audio)
            shutil.rmtree(os.path.dirname(temp_audio), ignore_errors=True)
        if server_proc is not None:
            if args.verbose:
                print("Stopping OpenASR server...", end="")
            stop_openasr_server(server_proc)
            if args.verbose:
                print(" done.")
