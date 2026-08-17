import re
from flask import Flask, request, jsonify
from reclip_vercel import extract, format_options

app = Flask(__name__)


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
    except Exception as exc:
        return jsonify({"error": str(exc).split("\n")[-1][:500]}), 400
