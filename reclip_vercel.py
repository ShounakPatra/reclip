import os
import re
import uuid
import json
from urllib.parse import urlparse, quote, parse_qs
from urllib.request import Request, urlopen
from flask import jsonify, send_file, after_this_request
import yt_dlp

TMP = "/tmp/reclip"
os.makedirs(TMP, exist_ok=True)


def clean_name(value, ext):
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '', value or '').strip()
    value = re.sub(r'\s+', ' ', value)[:100].strip() or 'reclip'
    return f"{value}.{ext}"


def youtube_video_id(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if host.endswith('youtu.be'):
            return parsed.path.strip('/').split('/')[0] or None
        if host.endswith('youtube.com'):
            values = parse_qs(parsed.query).get('v')
            if values:
                return values[0]
            parts = parsed.path.strip('/').split('/')
            if len(parts) >= 2 and parts[0] in {'shorts', 'embed', 'live'}:
                return parts[1]
    except Exception:
        pass
    return None


def is_youtube_url(url):
    try:
        host = (urlparse(url).hostname or '').lower()
        return host.endswith('youtube.com') or host == 'youtu.be' or host.endswith('.youtube.com')
    except Exception:
        return False


def youtube_oembed(url):
    endpoint = 'https://www.youtube.com/oembed?url=' + quote(url, safe='') + '&format=json'
    req = Request(endpoint, headers={
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
        'Accept': 'application/json',
    })
    with urlopen(req, timeout=10) as response:
        payload = json.loads(response.read().decode('utf-8'))

    video_id = youtube_video_id(url)
    thumbnail = payload.get('thumbnail_url', '')
    if not thumbnail and video_id:
        thumbnail = f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg'

    return {
        'title': payload.get('title', '') or 'YouTube video',
        'thumbnail': thumbnail,
        'uploader': payload.get('author_name', ''),
        'duration': None,
        'webpage_url': url,
        'formats': [],
        'downloadable': False,
        'notice': 'YouTube currently requires additional verification for server-side downloads. ReClip will not ask you for cookies or account access.',
        'source': 'youtube',
    }


def youtube_fallback(url):
    video_id = youtube_video_id(url)
    return {
        'title': 'YouTube video',
        'thumbnail': f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg' if video_id else '',
        'uploader': '',
        'duration': None,
        'webpage_url': url,
        'formats': [],
        'downloadable': False,
        'notice': 'YouTube is currently requiring additional verification for server-side downloads. ReClip keeps your account and cookies out of the process.',
        'source': 'youtube',
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
            bot_problem = any(token in message for token in (
                'sign in to confirm',
                'not a bot',
                "confirm you're not a bot",
                'cookies-from-browser',
                'cookies for the authentication',
                'player webpage',
                'verification',
            ))
            if bot_problem:
                try:
                    return youtube_oembed(url)
                except Exception:
                    return youtube_fallback(url)
            raise
    return extract(url)


def format_options(info):
    # Serverless downloads should only expose formats that already contain
    # both video and audio. Those formats can be downloaded directly without
    # a video+audio merge, so FFmpeg is not required for MP4 downloads.
    best = {}
    for f in info.get('formats', []):
        h = f.get('height')
        if not h or f.get('vcodec') in (None, 'none'):
            continue
        if f.get('acodec') in (None, 'none'):
            continue
        ext = (f.get('ext') or '').lower()
        score = (
            1 if ext == 'mp4' else 0,
            f.get('tbr') or 0,
        )
        if h not in best or score > best[h][0]:
            best[h] = (score, f)

    out = []
    for h, (_, f) in sorted(best.items(), reverse=True):
        out.append({
            'id': f.get('format_id'),
            'label': f'{h}p',
            'height': h,
            'ext': f.get('ext') or 'mp4',
        })
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
            'error': 'YouTube currently requires additional verification for server-side downloads. ReClip cannot complete this download right now.',
            'code': 'YOUTUBE_VERIFICATION_REQUIRED',
            'open_url': url,
        }), 409

    job = uuid.uuid4().hex
    out = os.path.join(TMP, job + '.%(ext)s')

    ffmpeg = None
    if mode == 'audio':
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            return error_json(RuntimeError(f'FFmpeg is unavailable for MP3 conversion: {exc}'), 500)

    opts = {
        'outtmpl': out,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 25,
        'retries': 2,
        'fragment_retries': 2,
        'cachedir': False,
        'restrictfilenames': True,
        'nopart': True,
    }

    if mode == 'audio':
        # Give yt-dlp the exact bundled FFmpeg binary. yt-dlp accepts either
        # the binary path or its containing directory for --ffmpeg-location.
        opts['ffmpeg_location'] = ffmpeg
        opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
        expected_ext = 'mp3'
    else:
        # format_options() only exposes progressive formats (video+audio),
        # so downloading the selected format requires no merge step and no
        # external FFmpeg dependency.
        opts['format'] = format_id or 'best[ext=mp4][vcodec!=none][acodec!=none]/best[vcodec!=none][acodec!=none]'
        expected_ext = None

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            requested_ext = expected_ext or info.get('ext') or 'mp4'

        matches = [
            os.path.join(TMP, f)
            for f in os.listdir(TMP)
            if f.startswith(job + '.') and not f.endswith('.part')
        ]
        if not matches:
            return error_json(RuntimeError('Download finished but no output file was produced.'), 500)

        path = matches[0]
        actual_ext = os.path.splitext(path)[1].lstrip('.').lower() or requested_ext
        filename = clean_name(title, actual_ext)

        mimetypes = {
            'mp3': 'audio/mpeg',
            'm4a': 'audio/mp4',
            'mp4': 'video/mp4',
            'webm': 'video/webm',
            'mov': 'video/quicktime',
            'mkv': 'video/x-matroska',
        }

        @after_this_request
        def cleanup(response):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            return response

        return send_file(
            path,
            as_attachment=True,
            download_name=filename,
            mimetype=mimetypes.get(actual_ext, 'application/octet-stream'),
        )
    except Exception as exc:
        for name in list(os.listdir(TMP)):
            if name.startswith(job + '.'):
                try:
                    os.remove(os.path.join(TMP, name))
                except OSError:
                    pass
        return error_json(exc)
