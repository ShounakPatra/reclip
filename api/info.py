import re
from flask import Flask, request, jsonify
from reclip_vercel import extract_public_info, format_options

app = Flask(__name__)


@app.route('/', methods=['POST'])
def info():
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    if not re.match(r'^https?://', url, re.I):
        return jsonify({'error': 'Enter a valid HTTP or HTTPS URL.'}), 400
    try:
        meta = extract_public_info(url)
        if meta.get('downloadable') is False:
            return jsonify(meta), 200
        return jsonify({
            'title': meta.get('title', ''),
            'thumbnail': meta.get('thumbnail', ''),
            'duration': meta.get('duration'),
            'uploader': meta.get('uploader', ''),
            'webpage_url': meta.get('webpage_url') or url,
            'formats': format_options(meta),
            'downloadable': True,
        }), 200
    except Exception as exc:
        return jsonify({
            'error': 'ReClip could not read this media source right now. Please try another link.',
            'detail': str(exc).split('\n')[-1][:300],
        }), 400
