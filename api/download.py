import re
from flask import Flask, request, jsonify
from reclip_vercel import download_media

app = Flask(__name__)

@app.route('/', methods=['POST'])
def download():
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    mode = data.get('format', 'video')
    format_id = data.get('format_id')
    title = data.get('title', '')
    if not re.match(r'^https?://', url, re.I):
        return jsonify({'error': 'Enter a valid HTTP or HTTPS URL.'}), 400
    return download_media(url, mode, format_id, title)
