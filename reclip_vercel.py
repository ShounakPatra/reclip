import os
import re
import uuid
import json
from urllib.parse import urlparse, quote
from urllib.request import Request, urlopen
from flask import jsonify, send_file, after_this_request
import yt_dlp

TMP = "/tmp/reclip"
os.makedirs(TMP, exist_ok=True)


def clean_name(value, ext):
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '', value or '').strip()
    value = re.sub(r'\s+', ' ', value)[:100].strip() or 'reclip'
    return f"{value}.{ext}"


def is_youtube_url(url):
    try:
        host = (urlparse(url).hostname or '').lower().split('.')
        return (len(host) >= 2 and host[-2:] == ['youtube', 'com']) or (len(host) >= 2 and host[-2:] == ['youtu', 'be']) or (len(host) >= 3 and host[-3:] == ['youtube', 'nocookie', 'com'])
    except Exception:
        return False


def youtube_oembed(url):
    endpoint = 'https://www.youtube.com/oembed?url=' + quote(url, safe='') + '&format=json'
    req = Request(endpoint, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urlopen(req, timeout=10) as response:
        payload = json.loads(response.read().decode('utf-8'))
    return {
        'title': payload.get('title', ''),
        'thumbnail': payload.get('thumbnail_url', ''),
        'uploader': payload.get('author_name', ''),
        'duration': None,
        'webpage_url': url,
        'formats': [],
        'downloadable': False,
        'notice': 'YouTube currently requires additional verification for server-side downloads. ReClip will not ask you for cookies or account access.',
    }


def extract(url, download=False, extra=None):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'skip_download': not download,
        'socket_timeout': 25,
        'retries': 2,
        'fragment_retries': 2,
        'cachedir': False,
    }
    if extra:
        opts.update(extra)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=download)


def extract_public_info(url):
    if is_youtube_url(url):
        try:
            return extract(url)
        except Exception as exc:
            message = str(exc).lower()
            bot_problem = any(token in message for token in ('sign in to confirm', 'not a bot', 'confirm you\'re not a bot', 'cookies-from-browser', 'player webpage'))
            if bot_problem:
                try:
                    return youtube_oembed(url)
                except Exception:
                    raise RuntimeError('YouTube could not provide download information to this server right now. ReClip does not request your cookies or sign-in details.')
            raise
    return extract(url)


def format_options(info):
    best = {}
    for f in info.get('formats', []):
        h = f.get('height')
        if not h or f.get('vcodec') in (None, 'none'):
            continue
        score = (1 if f.get('acodec') not in (None, 'none') else 0, f.get('tbr') or 0)
        if h not in best or score > best[h][0]:
            best[h] = (score, f)
    out = []
    for h, (_, f) in sorted(best.items(), reverse=True):
        out.append({'id': f.get('format_id'), 'label': f'{h}p', 'height': h})
    return out[:8]


def error_json(exc, status=400):
    return jsonify({'error': str(exc).split('\n')[-1][:500]}), status


def download_media(url, mode='video', format_id=None, title=''):
    if not re.match(r'^https?://', url, re.I):
        return error_json(ValueError('Enter a valid HTTP or HTTPS URL.'))
    if mode not in ('video', 'audio'):
        return error_json(ValueError('Unsupported format.'))

    if is_youtube_url(url):
        return jsonify({
            'error': 'YouTube currently requires additional verification for server-side downloads. ReClip does not collect cookies or ask users to sign in.',
            'code': 'YOUTUBE_VERIFICATION_REQUIRED',
            'open_url': url,
        }), 409

    job = uuid.uuid4().hex
    out = os.path.join(TMP, job + '.%(ext)s')

    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        return error_json(RuntimeError(f'FFmpeg is unavailable: {exc}'), 500)

    opts = {
        'outtmpl': out,
        'ffmpeg_location': os.path.dirname(ffmpeg),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 25,
        'retries': 2,
        'fragment_retries': 2,
        'cachedir': False,
        'restrictfilenames': True,
    }

    if mode == 'audio':
        opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
        ext = 'mp3'
    else:
        opts.update({
            'format': (f'{format_id}+bestaudio/best' if format_id else 'bestvideo+bestaudio/best'),
            'merge_output_format': 'mp4',
        })
        ext = 'mp4'

    path = os.path.join(TMP, job + '.' + ext)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        if not os.path.exists(path):
            matches = [os.path.join(TMP, f) for f in os.listdir(TMP) if f.startswith(job + '.')]
            if not matches:
                return error_json(RuntimeError('Download finished but no output file was produced.'), 500)
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

        return send_file(path, as_attachment=True, download_name=filename, mimetype='audio/mpeg' if ext == 'mp3' else 'video/mp4')
    except Exception as exc:
        for name in list(os.listdir(TMP)):
            if name.startswith(job + '.'):
                try:
                    os.remove(os.path.join(TMP, name))
                except OSError:
                    pass
        return error_json(exc)
