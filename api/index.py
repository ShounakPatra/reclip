import os
import re
import tempfile
import uuid
from flask import Flask, request, jsonify, send_file, after_this_request
import yt_dlp

app = Flask(__name__)

# Vercel's deployed filesystem is read-only; /tmp is the writable ephemeral area.
TMP = "/tmp/reclip"
os.makedirs(TMP, exist_ok=True)


def clean_name(value, ext):
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '', value or '').strip()
    value = re.sub(r'\s+', ' ', value)[:100].strip() or 'reclip'
    return f"{value}.{ext}"


def extract(url, download=False, extra=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": not download,
        "socket_timeout": 25,
        "retries": 2,
        "fragment_retries": 2,
        "cachedir": False,
    }
    if extra:
        opts.update(extra)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=download)


def format_options(info):
    best = {}
    for f in info.get("formats", []):
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        # Prefer a stream that already contains audio; otherwise yt-dlp can merge it.
        score = (1 if f.get("acodec") not in (None, "none") else 0, f.get("tbr") or 0)
        if h not in best or score > best[h][0]:
            best[h] = (score, f)
    out = []
    for h, (_, f) in sorted(best.items(), reverse=True):
        out.append({"id": f.get("format_id"), "label": f"{h}p", "height": h})
    return out[:8]


@app.route("/api", methods=["GET"])
def home():
    return jsonify({"name": "ReClip", "status": "ok"})


@app.route("/api/info", methods=["POST"])
def info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return jsonify({"error": "Enter a valid HTTP or HTTPS URL."}), 400
    try:
        meta = extract(url)
        return jsonify({
            "title": meta.get("title", ""),
            "thumbnail": meta.get("thumbnail", ""),
            "duration": meta.get("duration"),
            "uploader": meta.get("uploader", ""),
            "webpage_url": meta.get("webpage_url") or url,
            "formats": format_options(meta),
        })
    except Exception as e:
        return jsonify({"error": str(e).split("\n")[-1][:500]}), 400


@app.route("/api/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    mode = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")
    if not re.match(r"^https?://", url, re.I):
        return jsonify({"error": "Enter a valid HTTP or HTTPS URL."}), 400
    if mode not in ("video", "audio"):
        return jsonify({"error": "Unsupported format."}), 400

    job = uuid.uuid4().hex
    out = os.path.join(TMP, job + ".%(ext)s")

    # imageio-ffmpeg bundles a Linux FFmpeg binary in the Python wheel, so the
    # Vercel runtime does not need apt/Docker. It keeps the original ReClip
    # yt-dlp + FFmpeg architecture while using /tmp instead of a project folder.
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        return jsonify({"error": f"FFmpeg is unavailable: {e}"}), 500

    opts = {
        "outtmpl": out,
        "ffmpeg_location": os.path.dirname(ffmpeg),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 25,
        "retries": 2,
        "fragment_retries": 2,
        "cachedir": False,
        "restrictfilenames": True,
    }
    if mode == "audio":
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        })
        ext = "mp3"
    else:
        opts.update({
            "format": (f"{format_id}+bestaudio/best" if format_id else "bestvideo+bestaudio/best"),
            "merge_output_format": "mp4",
        })
        ext = "mp4"

    path = os.path.join(TMP, job + "." + ext)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        if not os.path.exists(path):
            matches = [os.path.join(TMP, f) for f in os.listdir(TMP) if f.startswith(job + ".")]
            if not matches:
                return jsonify({"error": "Download finished but no output file was produced."}), 500
            path = matches[0]

        filename = clean_name(title, ext)

        @after_this_request
        def cleanup(response):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            return response

        return send_file(path, as_attachment=True, download_name=filename, mimetype=("audio/mpeg" if ext == "mp3" else "video/mp4"))
    except Exception as e:
        for f in list(os.listdir(TMP)):
            if f.startswith(job + "."):
                try:
                    os.remove(os.path.join(TMP, f))
                except OSError:
                    pass
        return jsonify({"error": str(e).split("\n")[-1][:500]}), 400


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8899")))
