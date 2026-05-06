"""
youtube.py — RSS 피드 파싱 + 자막 추출 + 에러 처리
"""
import logging
import os
import re
import tempfile
import time
import feedparser
import requests
from http.cookiejar import MozillaCookieJar
from datetime import datetime, timezone, timedelta
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

logger = logging.getLogger(__name__)

NETSCAPE_HEADER = "# Netscape HTTP Cookie File\n"

_yt_api: YouTubeTranscriptApi | None = None


def _write_cookie_file(cookie_text: str) -> str:
    """쿠키 텍스트를 Netscape 헤더 포함 임시 파일로 저장하고 경로 반환"""
    if not cookie_text.startswith("# Netscape") and not cookie_text.startswith("# HTTP Cookie"):
        cookie_text = NETSCAPE_HEADER + cookie_text
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
    tmp.write(cookie_text)
    tmp.close()
    return tmp.name


def _apply_ytdlp_cookies(ydl_opts: dict) -> dict:
    """YOUTUBE_COOKIES 환경변수가 있으면 yt-dlp 옵션에 cookiefile 주입"""
    cookie_text = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if cookie_text:
        ydl_opts['cookiefile'] = _write_cookie_file(cookie_text)
    return ydl_opts


def _get_yt_api() -> YouTubeTranscriptApi:
    """쿠키 인증된 API 인스턴스를 lazy 생성 (호출 시점에 환경변수 읽기)"""
    global _yt_api
    if _yt_api is not None:
        return _yt_api

    cookie_text = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not cookie_text:
        logger.info("[자막] YOUTUBE_COOKIES 미설정 — 쿠키 없이 진행")
        _yt_api = YouTubeTranscriptApi()
        return _yt_api

    logger.info("[자막] 쿠키 원문 첫 200자: %s", repr(cookie_text[:200]))

    # Netscape 쿠키 텍스트 → 임시 파일 → MozillaCookieJar → requests.Session
    cookie_path = _write_cookie_file(cookie_text)
    with open(cookie_path, 'r', encoding='utf-8') as f:
        logger.info("[자막] 쿠키 파일 첫 3줄: %s", repr(f.readlines()[:3]))

    jar = MozillaCookieJar(cookie_path)
    jar.load(ignore_discard=True, ignore_expires=True)
    os.unlink(cookie_path)

    session = requests.Session()
    session.cookies = jar
    logger.info("[자막] YouTube 쿠키 로드 완료 (쿠키 %d개)", len(jar))
    _yt_api = YouTubeTranscriptApi(http_client=session)
    return _yt_api

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
            with yt_dlp.YoutubeDL(_apply_ytdlp_cookies({'quiet': True, 'no_warnings': True,
                                    'skip_download': True, 'ignore_no_formats_error': True,
                                    'playlist_items': '1'})) as ydl:
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
        with yt_dlp.YoutubeDL(_apply_ytdlp_cookies({'quiet': True, 'no_warnings': True,
                                'skip_download': True, 'ignore_no_formats_error': True,
                                'playlist_items': '1'})) as ydl:
            info = ydl.extract_info(fetch_url, download=False)
            channel_id = info.get('channel_id') or info.get('uploader_id')
            channel_name = info.get('channel') or info.get('uploader') or channel_id
            if channel_id and channel_id.startswith('UC'):
                return {'channel_id': channel_id, 'channel_name': channel_name}
    except Exception:
        pass

    return None


def fetch_video_metadata(video_id: str) -> dict | None:
    """
    yt-dlp로 단일 영상의 제목/채널명을 조회.
    반환: { video_id, title, channel_name, url } 또는 None
    """
    try:
        import yt_dlp
    except ImportError:
        return None

    ydl_opts = _apply_ytdlp_cookies({
        'quiet': True, 'no_warnings': True, 'skip_download': True,
        'ignore_no_formats_error': True,
    })
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f'https://www.youtube.com/watch?v={video_id}',
                download=False,
            )
    except Exception as e:
        logger.error("[메타] video=%s 조회 실패: %s: %s", video_id, type(e).__name__, str(e)[:300])
        return None

    return {
        'video_id': video_id,
        'title': info.get('title') or video_id,
        'channel_name': info.get('uploader') or info.get('channel') or '',
        'url': f'https://www.youtube.com/watch?v={video_id}',
    }


def fetch_recent_videos(channel_id: str, days: int = 7, fetch_limit: int = 15) -> list[dict]:
    """
    yt-dlp로 채널 최신 fetch_limit개를 가져온다.
    days일 내 영상은 is_recent=True, 그 이전 영상은 is_recent=False로 표시.
    반환: [{ video_id, title, published, channel_name, url, is_recent }, ...]
    """
    try:
        import yt_dlp
    except ImportError:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()

    ydl_opts = _apply_ytdlp_cookies({
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'ignore_no_formats_error': True,
        'playlist_items': f'1-{fetch_limit}',
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f'https://www.youtube.com/channel/{channel_id}/videos',
                download=False,
            )
    except Exception as e:
        logger.error("[수집] channel=%s yt-dlp 실패: %s: %s", channel_id, type(e).__name__, str(e)[:300])
        return []

    videos = []
    for entry in info.get('entries') or []:
        video_id = entry.get('id')
        if not video_id:
            continue

        upload_date = entry.get('upload_date', '')
        is_recent = False
        if upload_date and len(upload_date) == 8:
            published = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}T00:00:00+00:00"
            from datetime import date
            published_date = date(int(upload_date[:4]), int(upload_date[4:6]), int(upload_date[6:8]))
            is_recent = published_date >= cutoff
        else:
            published = ''

        videos.append({
            'video_id': video_id,
            'title': entry.get('title', ''),
            'published': published,
            'channel_name': entry.get('channel') or entry.get('uploader', ''),
            'url': f"https://www.youtube.com/watch?v={video_id}",
            'is_recent': is_recent,
        })

    return videos


def get_transcript(video_id: str) -> dict:
    """
    자막 추출 (우선순위: 한국어 수동 → 한국어 자동 → 영어 → yt-dlp fallback)
    반환: { 'text': str, 'language': str, 'source': 'manual'|'auto'|'translated'|'ytdlp_manual'|'ytdlp_auto' }
    에러 시: { 'error': str }
    """
    logger.info("[자막] video_id=%s 자막 추출 시작", video_id)

    # 1차: youtube-transcript-api
    for attempt in range(MAX_RETRIES):
        try:
            logger.info("[자막] youtube-transcript-api 시도 %d/%d", attempt + 1, MAX_RETRIES)
            transcript_list = _get_yt_api().list(video_id)
            logger.info("[자막] list() 성공 — 사용 가능한 자막 목록 조회 완료")

            # 1. 한국어 수동 자막
            for lang in TRANSCRIPT_PRIORITY:
                try:
                    t = transcript_list.find_manually_created_transcript([lang])
                    text = _join_transcript(t.fetch())
                    logger.info("[자막] 한국어 수동 자막 성공 (lang=%s, len=%d)", lang, len(text))
                    return {'text': text, 'language': lang, 'source': 'manual'}
                except NoTranscriptFound:
                    logger.debug("[자막] 수동 자막 없음 (lang=%s)", lang)
                    continue

            # 2. 한국어 자동 자막
            for lang in TRANSCRIPT_PRIORITY:
                try:
                    t = transcript_list.find_generated_transcript([lang])
                    text = _join_transcript(t.fetch())
                    logger.info("[자막] 한국어 자동 자막 성공 (lang=%s, len=%d)", lang, len(text))
                    return {'text': text, 'language': lang, 'source': 'auto'}
                except NoTranscriptFound:
                    logger.debug("[자막] 자동 자막 없음 (lang=%s)", lang)
                    continue

            # 3. 영어 자막 (번역)
            for lang in TRANSCRIPT_FALLBACK:
                try:
                    t = transcript_list.find_transcript([lang])
                    translated = t.translate('ko')
                    text = _join_transcript(translated.fetch())
                    logger.info("[자막] 영어→한국어 번역 자막 성공 (lang=%s, len=%d)", lang, len(text))
                    return {'text': text, 'language': 'ko(번역)', 'source': 'translated'}
                except Exception as e:
                    logger.warning("[자막] 번역 자막 실패 (lang=%s): %s: %s", lang, type(e).__name__, e)
                    continue

            # youtube-transcript-api로 자막 없음 → yt-dlp 시도
            logger.warning("[자막] youtube-transcript-api: 모든 언어에서 자막 없음 → yt-dlp fallback")
            break

        except TranscriptsDisabled:
            logger.error("[자막] TranscriptsDisabled 예외 — 자막 비활성화")
            return {'error': '이 영상은 자막이 비활성화되어 있습니다.'}
        except Exception as e:
            err_str = str(e)
            logger.error("[자막] 예외 발생 (attempt %d): %s: %s", attempt + 1, type(e).__name__, err_str[:300])
            if 'private' in err_str.lower() or 'unavailable' in err_str.lower():
                return {'error': '비공개 또는 삭제된 영상입니다.'}
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
                continue
            break  # 재시도 소진 → yt-dlp 시도

    # 2차 fallback: yt-dlp
    logger.info("[자막] yt-dlp fallback 시작")
    result = _get_transcript_ytdlp(video_id)
    if 'error' not in result:
        return result

    # 3차 fallback: 외부 웹 서비스
    logger.info("[자막] 외부 서비스 fallback 시작")
    return _get_transcript_web(video_id)


def _get_transcript_ytdlp(video_id: str) -> dict:
    """yt-dlp를 이용한 자막 추출 (youtube-transcript-api 실패 시 fallback)"""
    try:
        import yt_dlp
        import urllib.request
    except ImportError:
        logger.error("[자막/yt-dlp] yt_dlp 모듈 import 실패")
        return {'error': '자막을 찾을 수 없습니다. 수동으로 내용을 입력해주세요.'}

    ydl_opts = {
        'quiet': True, 'no_warnings': True,
        'skip_download': True,
        'writesubtitles': True, 'writeautomaticsub': True,
        'subtitleslangs': ['ko', 'ko-KR', 'en', 'en-US'],
        'ignore_no_formats_error': True,
    }

    _apply_ytdlp_cookies(ydl_opts)
    if 'cookiefile' in ydl_opts:
        logger.info("[자막/yt-dlp] 쿠키 파일 적용")

    try:
        logger.info("[자막/yt-dlp] extract_info 시작")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f'https://www.youtube.com/watch?v={video_id}',
                download=False,
            )
        logger.info("[자막/yt-dlp] extract_info 성공")
    except Exception as e:
        logger.error("[자막/yt-dlp] extract_info 실패: %s: %s", type(e).__name__, str(e)[:300])
        return {'error': f'자막을 찾을 수 없습니다. 수동으로 내용을 입력해주세요. (상세: {str(e)[:80]})'}

    langs = ['ko', 'ko-KR']
    available_subs = list(info.get('subtitles', {}).keys())[:10]
    available_auto = list(info.get('automatic_captions', {}).keys())[:10]
    logger.info("[자막/yt-dlp] 수동자막 언어: %s / 자동자막 언어: %s", available_subs, available_auto)

    for src_key, src_label in [('subtitles', 'ytdlp_manual'), ('automatic_captions', 'ytdlp_auto')]:
        subs = info.get(src_key, {})
        for lang in langs:
            if lang not in subs:
                continue
            formats = subs[lang]
            logger.info("[자막/yt-dlp] %s/%s 포맷 %d개: %s", src_key, lang, len(formats), [f.get('ext') for f in formats[:5]])
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

            logger.info("[자막/yt-dlp] 선택된 포맷: ext=%s", chosen.get('ext'))
            try:
                req = urllib.request.Request(
                    chosen['url'],
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode('utf-8')

                logger.info("[자막/yt-dlp] 자막 다운로드 성공 (raw len=%d)", len(raw))

                if chosen.get('ext') == 'json3':
                    text = _parse_json3(raw)
                else:
                    text = _parse_vtt(raw)

                if text.strip():
                    logger.info("[자막/yt-dlp] 파싱 성공 (text len=%d, source=%s)", len(text), src_label)
                    return {'text': text, 'language': lang, 'source': src_label}
                else:
                    logger.warning("[자막/yt-dlp] 파싱 결과 빈 텍스트")
            except Exception as e:
                logger.error("[자막/yt-dlp] 자막 다운로드/파싱 실패: %s: %s", type(e).__name__, str(e)[:200])
                continue

    logger.error("[자막/yt-dlp] 모든 시도 실패 — 자막 없음")
    return {'error': '자막을 찾을 수 없습니다. 수동으로 내용을 입력해주세요.'}


def _get_transcript_web(video_id: str) -> dict:
    """외부 웹 서비스를 통한 자막 추출 (3차 fallback)"""
    import json as _json
    import urllib.request

    # 방법 1: youtubetranscript.com (무료, API 키 불필요)
    try:
        url = f"https://youtubetranscript.com/?server_vid2={video_id}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode('utf-8')

        # XML 형식 응답 파싱: <text>...</text> 태그에서 텍스트 추출
        texts = re.findall(r'<text[^>]*>(.*?)</text>', raw, re.DOTALL)
        if texts:
            import html
            cleaned = ' '.join(html.unescape(t).strip() for t in texts if t.strip())
            if len(cleaned) > 100:
                logger.info("[자막/web] youtubetranscript.com 성공 (len=%d)", len(cleaned))
                return {'text': cleaned, 'language': 'ko', 'source': 'web_service'}
    except Exception as e:
        logger.warning("[자막/web] youtubetranscript.com 실패: %s: %s", type(e).__name__, str(e)[:200])

    # 방법 2: downsub.com API
    try:
        api_url = f"https://downsub.com/api?url=https://www.youtube.com/watch?v={video_id}"
        req = urllib.request.Request(api_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read().decode('utf-8'))

        # 한국어 자막 URL 찾기
        for sub in data.get('subtitles', []) + data.get('autoSubs', []):
            lang = sub.get('language', '').lower()
            if 'ko' in lang or 'korean' in lang:
                sub_url = sub.get('url', '')
                if sub_url:
                    with urllib.request.urlopen(sub_url, timeout=15) as resp2:
                        sub_text = resp2.read().decode('utf-8')
                    cleaned = _parse_vtt(sub_text) if 'WEBVTT' in sub_text else sub_text
                    if len(cleaned) > 100:
                        logger.info("[자막/web] downsub.com 성공 (len=%d)", len(cleaned))
                        return {'text': cleaned, 'language': 'ko', 'source': 'web_downsub'}
    except Exception as e:
        logger.warning("[자막/web] downsub.com 실패: %s: %s", type(e).__name__, str(e)[:200])

    logger.error("[자막/web] 모든 외부 서비스 실패")
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

    ydl_opts = _apply_ytdlp_cookies({
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'ignore_no_formats_error': True,
    })

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


def _join_transcript(entries) -> str:
    parts = []
    for e in entries:
        text = e.text if hasattr(e, 'text') else e.get('text', '')
        if text:
            parts.append(text)
    return ' '.join(parts)


def is_transcript_too_short(text: str, min_chars: int = 200) -> bool:
    """2분 미만 영상 기준 (대략 200자 이하)"""
    return len(text.strip()) < min_chars
