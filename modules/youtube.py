"""
youtube.py — RSS 피드 파싱 + 자막 추출 + 에러 처리
"""
import re
import time
import feedparser
from datetime import datetime, timezone, timedelta
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

TRANSCRIPT_PRIORITY = ['ko', 'ko-KR']
TRANSCRIPT_FALLBACK = ['en', 'en-US']
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


def extract_video_id(url: str) -> str | None:
    """YouTube URL에서 video_id 추출"""
    patterns = [
        r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})',
        r'(?:embed/)([A-Za-z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_channel_rss_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def resolve_channel_from_url(url: str) -> dict | None:
    """
    YouTube 채널 URL에서 channel_id와 channel_name을 추출.
    지원 형식:
      - https://www.youtube.com/channel/UCxxxxxxxxxx
      - https://www.youtube.com/@username
      - https://www.youtube.com/c/customname
      - https://www.youtube.com/user/username
    반환: { 'channel_id': str, 'channel_name': str } 또는 None
    """
    # /channel/UC... 형식은 바로 추출 가능
    direct = re.search(r'youtube\.com/channel/(UC[A-Za-z0-9_-]{22})', url)
    if direct:
        channel_id = direct.group(1)
        # 채널명은 yt-dlp로 확인
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True,
                                    'skip_download': True, 'playlist_items': '1'}) as ydl:
                info = ydl.extract_info(url, download=False)
                name = info.get('channel') or info.get('uploader') or channel_id
        except Exception:
            name = channel_id
        return {'channel_id': channel_id, 'channel_name': name}

    # @username, /c/, /user/ 형식 — yt-dlp로 채널 페이지 접근
    try:
        import yt_dlp
    except ImportError:
        return None

    # URL 정규화: 채널 탭이 없으면 /videos 붙여서 플레이리스트 형태로 접근
    if not any(x in url for x in ['/videos', '/featured', '/about']):
        fetch_url = url.rstrip('/') + '/videos'
    else:
        fetch_url = url

    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True,
                                'skip_download': True, 'playlist_items': '1'}) as ydl:
            info = ydl.extract_info(fetch_url, download=False)
            channel_id = info.get('channel_id') or info.get('uploader_id')
            channel_name = info.get('channel') or info.get('uploader') or channel_id
            if channel_id and channel_id.startswith('UC'):
                return {'channel_id': channel_id, 'channel_name': channel_name}
    except Exception:
        pass

    return None


def fetch_recent_videos(channel_id: str, days: int = 7, fetch_limit: int = 15) -> list[dict]:
    """
    yt-dlp로 채널 최신 fetch_limit개를 가져온 뒤 days일 내 영상만 반환
    반환: [{ video_id, title, published, channel_name, url }, ...]
    """
    try:
        import yt_dlp
    except ImportError:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'playlist_items': f'1-{fetch_limit}',
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f'https://www.youtube.com/channel/{channel_id}/videos',
                download=False,
            )
    except Exception:
        return []

    videos = []
    for entry in info.get('entries') or []:
        video_id = entry.get('id')
        if not video_id:
            continue

        upload_date = entry.get('upload_date', '')
        if upload_date and len(upload_date) == 8:
            published = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}T00:00:00+00:00"
            from datetime import date
            published_date = date(int(upload_date[:4]), int(upload_date[4:6]), int(upload_date[6:8]))
            if published_date < cutoff:
                continue
        else:
            published = ''

        videos.append({
            'video_id': video_id,
            'title': entry.get('title', ''),
            'published': published,
            'channel_name': entry.get('channel') or entry.get('uploader', ''),
            'url': f"https://www.youtube.com/watch?v={video_id}",
        })

    return videos


def get_transcript(video_id: str) -> dict:
    """
    자막 추출 (우선순위: 한국어 수동 → 한국어 자동 → 영어 → yt-dlp fallback)
    반환: { 'text': str, 'language': str, 'source': 'manual'|'auto'|'translated'|'ytdlp_manual'|'ytdlp_auto' }
    에러 시: { 'error': str }
    """
    # 1차: youtube-transcript-api
    for attempt in range(MAX_RETRIES):
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

            # 1. 한국어 수동 자막
            for lang in TRANSCRIPT_PRIORITY:
                try:
                    t = transcript_list.find_manually_created_transcript([lang])
                    text = _join_transcript(t.fetch())
                    return {'text': text, 'language': lang, 'source': 'manual'}
                except NoTranscriptFound:
                    continue

            # 2. 한국어 자동 자막
            for lang in TRANSCRIPT_PRIORITY:
                try:
                    t = transcript_list.find_generated_transcript([lang])
                    text = _join_transcript(t.fetch())
                    return {'text': text, 'language': lang, 'source': 'auto'}
                except NoTranscriptFound:
                    continue

            # 3. 영어 자막 (번역)
            for lang in TRANSCRIPT_FALLBACK:
                try:
                    t = transcript_list.find_transcript([lang])
                    translated = t.translate('ko')
                    text = _join_transcript(translated.fetch())
                    return {'text': text, 'language': 'ko(번역)', 'source': 'translated'}
                except Exception:
                    continue

            # youtube-transcript-api로 자막 없음 → yt-dlp 시도
            break

        except TranscriptsDisabled:
            return {'error': '이 영상은 자막이 비활성화되어 있습니다.'}
        except Exception as e:
            err_str = str(e)
            if 'private' in err_str.lower() or 'unavailable' in err_str.lower():
                return {'error': '비공개 또는 삭제된 영상입니다.'}
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
                continue
            break  # 재시도 소진 → yt-dlp 시도

    # 2차 fallback: yt-dlp
    return _get_transcript_ytdlp(video_id)


def _get_transcript_ytdlp(video_id: str) -> dict:
    """yt-dlp를 이용한 자막 추출 (youtube-transcript-api 실패 시 fallback)"""
    try:
        import yt_dlp
        import urllib.request
    except ImportError:
        return {'error': '자막을 찾을 수 없습니다. 수동으로 내용을 입력해주세요.'}

    ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f'https://www.youtube.com/watch?v={video_id}',
                download=False,
            )
    except Exception as e:
        return {'error': f'자막을 찾을 수 없습니다. 수동으로 내용을 입력해주세요. (상세: {str(e)[:80]})'}

    langs = ['ko', 'ko-KR']

    for src_key, src_label in [('subtitles', 'ytdlp_manual'), ('automatic_captions', 'ytdlp_auto')]:
        subs = info.get(src_key, {})
        for lang in langs:
            if lang not in subs:
                continue
            formats = subs[lang]
            # json3 > vtt > srv1 순으로 선호
            chosen = None
            for preferred_ext in ('json3', 'vtt', 'srv1'):
                for fmt in formats:
                    if fmt.get('ext') == preferred_ext:
                        chosen = fmt
                        break
                if chosen:
                    break
            if not chosen and formats:
                chosen = formats[0]
            if not chosen:
                continue

            try:
                req = urllib.request.Request(
                    chosen['url'],
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode('utf-8')

                if chosen.get('ext') == 'json3':
                    text = _parse_json3(raw)
                else:
                    text = _parse_vtt(raw)

                if text.strip():
                    return {'text': text, 'language': lang, 'source': src_label}
            except Exception:
                continue

    return {'error': '자막을 찾을 수 없습니다. 수동으로 내용을 입력해주세요.'}


def _parse_json3(raw: str) -> str:
    """yt-dlp json3 자막 포맷 파싱"""
    import json as _json
    data = _json.loads(raw)
    texts = []
    for event in data.get('events', []):
        line = ''.join(s.get('utf8', '') for s in event.get('segs', [])).strip()
        if line and line != '\n':
            texts.append(line)
    return ' '.join(texts)


def _parse_vtt(raw: str) -> str:
    """WebVTT 자막 포맷 파싱 (타임스탬프·태그 제거, 연속 중복 제거)"""
    import re as _re
    texts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('WEBVTT') or line.startswith('NOTE') or '-->' in line:
            continue
        line = _re.sub(r'<[^>]+>', '', line)  # HTML 태그 제거
        if line:
            texts.append(line)
    # 연속 중복 제거 (VTT 특성상 자주 발생)
    deduped = []
    for t in texts:
        if not deduped or deduped[-1] != t:
            deduped.append(t)
    return ' '.join(deduped)


def search_videos_by_keyword(keyword: str, max_results: int = 20) -> list[dict]:
    """
    키워드로 YouTube 영상 검색 (yt-dlp 사용, API 키 불필요, 무료)
    반환: [{ video_id, title, published, channel_name, url }, ...]
    실패 시: [] 반환
    """
    try:
        import yt_dlp
    except ImportError:
        return []

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f'ytsearch{max_results}:{keyword}', download=False)
    except Exception:
        return []

    videos = []
    for entry in info.get('entries') or []:
        video_id = entry.get('id')
        if not video_id:
            continue

        # upload_date: YYYYMMDD 형태
        upload_date = entry.get('upload_date', '')
        if upload_date and len(upload_date) == 8:
            published = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}T00:00:00+00:00"
        else:
            published = ''

        videos.append({
            'video_id': video_id,
            'title': entry.get('title', ''),
            'published': published,
            'channel_name': entry.get('uploader', ''),
            'url': f"https://www.youtube.com/watch?v={video_id}",
            'source': 'keyword',
        })

    return videos


def _join_transcript(entries: list) -> str:
    return ' '.join(e['text'] for e in entries if e.get('text'))


def is_transcript_too_short(text: str, min_chars: int = 200) -> bool:
    """2분 미만 영상 기준 (대략 200자 이하)"""
    return len(text.strip()) < min_chars
