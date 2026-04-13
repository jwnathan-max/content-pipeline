"""
app.py — 법인 컨설팅 콘텐츠 파이프라인 메인 앱
"""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
import streamlit as st
from dotenv import load_dotenv

from modules.youtube import (
    fetch_recent_videos,
    search_videos_by_keyword,
    get_transcript,
    extract_video_id,
    is_transcript_too_short,
    resolve_channel_from_url,
)
from modules.ai_processor import generate_content, refine_blog, extract_sms_from_blog, generate_sms_from_blog
from modules.image_generator import generate_card_image
from modules.ghost_publisher import publish_post, upload_image, test_connection

load_dotenv()

# Streamlit Cloud secrets → os.environ 동기화 (로컬 .env와 동일하게 동작)
try:
    for key, value in st.secrets.items():
        if isinstance(value, str):
            os.environ.setdefault(key, value)
except Exception:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# 기본 키워드 프리셋
DEFAULT_KEYWORDS = ["법인세 절세", "법인 세무", "법인 노무관리", "법인 재무관리", "중소기업 세금", "법인 대표 세금"]

# ──────────────────────────────────────────────
# DB 초기화
# ──────────────────────────────────────────────

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def _fetchall(conn, query, params=None):
    """Execute query and return list of dicts."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(query, params or ())
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def _fetchone(conn, query, params=None):
    """Execute query and return one dict or None."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(query, params or ())
    row = cur.fetchone()
    cur.close()
    return dict(row) if row else None


def _execute(conn, query, params=None):
    """Execute a write query."""
    cur = conn.cursor()
    cur.execute(query, params or ())
    conn.commit()
    cur.close()


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id           SERIAL PRIMARY KEY,
            channel_id   TEXT NOT NULL UNIQUE,
            channel_name TEXT,
            added_at     TIMESTAMP DEFAULT NOW(),
            is_active    INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_videos (
            id           SERIAL PRIMARY KEY,
            video_id     TEXT NOT NULL UNIQUE,
            title        TEXT,
            channel_name TEXT,
            processed_at TIMESTAMP DEFAULT NOW(),
            status       TEXT DEFAULT 'completed',
            content_json TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS video_cache (
            cache_key   TEXT NOT NULL PRIMARY KEY,
            data_json   TEXT,
            cached_at   TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS publications (
            id           SERIAL PRIMARY KEY,
            video_id     TEXT NOT NULL,
            channel      TEXT NOT NULL,
            published_at TIMESTAMP DEFAULT NOW(),
            url          TEXT,
            status       TEXT DEFAULT 'pending'
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_posts (
            id            SERIAL PRIMARY KEY,
            video_id      TEXT NOT NULL,
            channel       TEXT NOT NULL DEFAULT 'ghost',
            scheduled_at  TEXT NOT NULL,
            created_at    TIMESTAMP DEFAULT NOW(),
            status        TEXT DEFAULT 'pending',
            ghost_post_id TEXT,
            ghost_url     TEXT,
            error_msg     TEXT
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


# ──────────────────────────────────────────────
# DB 헬퍼
# ──────────────────────────────────────────────

def db_get_channels() -> list:
    conn = get_db()
    rows = _fetchall(conn, "SELECT * FROM channels ORDER BY added_at DESC")
    conn.close()
    return rows


def db_add_channel(channel_id: str, channel_name: str):
    conn = get_db()
    _execute(conn,
        "INSERT INTO channels (channel_id, channel_name, added_at) VALUES (%s, %s, NOW()) ON CONFLICT (channel_id) DO NOTHING",
        (channel_id.strip(), channel_name.strip()),
    )
    conn.close()


def db_toggle_channel(channel_id: str, is_active: int):
    conn = get_db()
    _execute(conn, "UPDATE channels SET is_active=%s WHERE channel_id=%s", (is_active, channel_id))
    conn.close()


def db_delete_channel(channel_id: str):
    conn = get_db()
    _execute(conn, "DELETE FROM channels WHERE channel_id=%s", (channel_id,))
    conn.close()


def db_is_processed(video_id: str) -> bool:
    conn = get_db()
    row = _fetchone(conn, "SELECT id FROM processed_videos WHERE video_id=%s", (video_id,))
    conn.close()
    return row is not None


def db_save_content(video_id: str, title: str, channel_name: str, content: dict, status: str = "completed"):
    conn = get_db()
    _execute(conn,
        """INSERT INTO processed_videos (video_id, title, channel_name, status, content_json)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (video_id) DO UPDATE SET title=EXCLUDED.title, channel_name=EXCLUDED.channel_name,
           status=EXCLUDED.status, content_json=EXCLUDED.content_json, processed_at=NOW()""",
        (video_id, title, channel_name, status, json.dumps(content, ensure_ascii=False)),
    )
    conn.close()


def db_get_content(video_id: str) -> dict | None:
    conn = get_db()
    row = _fetchone(conn, "SELECT content_json FROM processed_videos WHERE video_id=%s", (video_id,))
    conn.close()
    if row and row["content_json"]:
        return json.loads(row["content_json"])
    return None


# ──────────────────────────────────────────────
# 예약 발행 헬퍼
# ──────────────────────────────────────────────

def db_add_scheduled(video_id: str, channel: str, scheduled_at: str):
    conn = get_db()
    _execute(conn,
        "INSERT INTO scheduled_posts (video_id, channel, scheduled_at) VALUES (%s, %s, %s)",
        (video_id, channel, scheduled_at),
    )
    conn.close()


def db_get_scheduled(status: str | None = None) -> list:
    conn = get_db()
    if status:
        rows = _fetchall(conn,
            "SELECT sp.*, pv.title, pv.channel_name FROM scheduled_posts sp "
            "LEFT JOIN processed_videos pv ON sp.video_id = pv.video_id "
            "WHERE sp.status=%s ORDER BY sp.scheduled_at ASC", (status,))
    else:
        rows = _fetchall(conn,
            "SELECT sp.*, pv.title, pv.channel_name FROM scheduled_posts sp "
            "LEFT JOIN processed_videos pv ON sp.video_id = pv.video_id "
            "ORDER BY sp.scheduled_at DESC LIMIT 50")
    conn.close()
    return rows


def db_update_scheduled(sched_id: int, status: str, ghost_post_id: str = None,
                        ghost_url: str = None, error_msg: str = None):
    conn = get_db()
    _execute(conn,
        "UPDATE scheduled_posts SET status=%s, ghost_post_id=%s, ghost_url=%s, error_msg=%s WHERE id=%s",
        (status, ghost_post_id, ghost_url, error_msg, sched_id),
    )
    conn.close()


def db_add_publication(video_id: str, channel: str, url: str, status: str):
    conn = get_db()
    _execute(conn,
        "INSERT INTO publications (video_id, channel, url, status) VALUES (%s, %s, %s, %s)",
        (video_id, channel, url, status),
    )
    conn.close()


def process_pending_schedules():
    """예약 시간이 지난 pending 포스트를 자동 발행"""
    now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    pending = db_get_scheduled(status='pending')
    published_count = 0
    for sched in pending:
        if sched['scheduled_at'] <= now:
            content = db_get_content(sched['video_id'])
            if not content or 'blog' not in content:
                db_update_scheduled(sched['id'], 'failed', error_msg='콘텐츠 없음')
                continue

            blog = content['blog']
            # 이미지 업로드 시도
            feature_url = None
            img_key = f"blog_image_{sched['video_id']}"
            # 이미지는 세션에 없을 수 있으므로 파일에서 시도
            img_dir = Path(__file__).parent / "generated_images"
            img_files = list(img_dir.glob(f"*{sched['video_id']}*")) if img_dir.exists() else []
            if img_files:
                img_bytes = img_files[0].read_bytes()
                img_result = upload_image(img_bytes, img_files[0].name)
                if 'url' in img_result:
                    feature_url = img_result['url']

            result = publish_post(
                blog=blog,
                feature_image_url=feature_url,
            )

            if 'error' in result:
                db_update_scheduled(sched['id'], 'failed', error_msg=result['error'])
                db_add_publication(sched['video_id'], 'ghost', '', 'failed')
            else:
                db_update_scheduled(
                    sched['id'], 'published',
                    ghost_post_id=result.get('id', ''),
                    ghost_url=result.get('url', ''),
                )
                db_add_publication(sched['video_id'], 'ghost', result.get('url', ''), 'published')
                published_count += 1
    return published_count


# ──────────────────────────────────────────────
# 영상 수집 캐시 헬퍼 (당일 기준)
# ──────────────────────────────────────────────

def _today() -> str:
    return datetime.now().strftime('%Y-%m-%d')


def db_get_cache(cache_key: str):
    """캐시 반환. 없으면 None."""
    conn = get_db()
    row = _fetchone(conn,
        "SELECT data_json, cached_at FROM video_cache WHERE cache_key=%s",
        (cache_key,),
    )
    conn.close()
    if not row:
        return None
    return json.loads(row['data_json'])


def db_set_cache(cache_key: str, data):
    conn = get_db()
    _execute(conn,
        "INSERT INTO video_cache (cache_key, data_json, cached_at) VALUES (%s, %s, NOW()) "
        "ON CONFLICT (cache_key) DO UPDATE SET data_json=EXCLUDED.data_json, cached_at=NOW()",
        (cache_key, json.dumps(data, ensure_ascii=False)),
    )
    conn.close()


def db_clear_cache(cache_key: str):
    conn = get_db()
    _execute(conn, "DELETE FROM video_cache WHERE cache_key=%s", (cache_key,))
    conn.close()


# ──────────────────────────────────────────────
# 공통 영상 카드 렌더링
# ──────────────────────────────────────────────

def render_video_card(video: dict, key_suffix: str = ""):
    vid = video['video_id']
    is_done = db_is_processed(vid)

    with st.container():
        col1, col2 = st.columns([5, 1])
        with col1:
            badge = "✅ 처리완료" if is_done else "🆕 새 영상"
            st.markdown(f"{badge} **{video['title']}**")
            ch = video.get('channel_name', '')
            pub = video.get('published', '')[:16]
            src = " | 🔍 키워드 검색" if video.get('source') == 'keyword' else ""
            st.caption(f"{ch} | {pub}{src}")
            st.caption(video['url'])
        with col2:
            btn_label = "재생성" if is_done else "콘텐츠 생성"
            if st.button(btn_label, key=f"gen_{vid}_{key_suffix}"):
                st.session_state['target_video'] = video
                st.session_state['auto_generate'] = True
                st.session_state['gen_state'] = 'idle'
                # 이전 자막 캐시 초기화
                st.session_state.pop('transcript_text', None)
                db_set_cache('__last_target_video', video)
                st.rerun()
    st.divider()


# ──────────────────────────────────────────────
# Streamlit 앱
# ──────────────────────────────────────────────

st.set_page_config(page_title="법인 콘텐츠 파이프라인", page_icon="📋", layout="wide")
init_db()

# 세션 시작 시 마지막 키워드 복원 (DB → session_state)
if 'keyword_input' not in st.session_state:
    last_kw = db_get_cache('__last_keyword')
    if last_kw:
        st.session_state['keyword_input'] = last_kw

# 세션 시작 시 마지막 선택 영상 복원 (DB → session_state)
if 'target_video' not in st.session_state:
    last_target = db_get_cache('__last_target_video')
    if last_target:
        st.session_state['target_video'] = last_target

st.title("📋 법인 컨설팅 콘텐츠 파이프라인")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["채널 관리", "영상 수집", "콘텐츠 생성", "처리 이력", "예약 관리"])


# ══════════════════════════════════════════════
# 탭1: 채널 관리
# ══════════════════════════════════════════════

with tab1:
    st.subheader("유튜브 채널 등록")
    st.caption("특정 채널을 구독해두면 최신 영상을 자동으로 수집합니다.")

    with st.form("add_channel_form"):
        new_channel_url = st.text_input(
            "YouTube 채널 URL",
            placeholder="https://www.youtube.com/@채널명  또는  /channel/UCxx...  등 모든 형식 가능",
        )
        submitted = st.form_submit_button("채널 추가")
        if submitted:
            if new_channel_url.strip():
                with st.spinner("채널 정보 조회 중..."):
                    result = resolve_channel_from_url(new_channel_url.strip())
                if result:
                    db_add_channel(result['channel_id'], result['channel_name'])
                    st.success(f"채널 추가 완료: **{result['channel_name']}** (`{result['channel_id']}`)")
                    st.rerun()
                else:
                    st.error("채널 정보를 찾을 수 없습니다. URL을 다시 확인해주세요.")
            else:
                st.error("YouTube 채널 URL을 입력해주세요.")

    st.caption("지원 형식: `youtube.com/@채널명` · `youtube.com/channel/UCxx...` · `youtube.com/c/...` · `youtube.com/user/...`")

    st.divider()
    channels = db_get_channels()
    if not channels:
        st.info("등록된 채널이 없습니다.")
    else:
        st.write(f"**등록된 채널 {len(channels)}개**")
        for ch in channels:
            col1, col2, col3, col4 = st.columns([3, 2, 1, 1])
            with col1:
                st.write(f"**{ch['channel_name']}**")
                st.caption(ch['channel_id'])
            with col2:
                added = str(ch['added_at'] or '')[:10] or '알 수 없음'
                st.caption(f"추가일: {added}")
            with col3:
                label = "✅ 활성" if ch['is_active'] else "⏸ 비활성"
                if st.button(label, key=f"toggle_{ch['channel_id']}"):
                    db_toggle_channel(ch['channel_id'], 0 if ch['is_active'] else 1)
                    st.rerun()
            with col4:
                if st.button("삭제", key=f"del_{ch['channel_id']}"):
                    db_delete_channel(ch['channel_id'])
                    st.rerun()


# ══════════════════════════════════════════════
# 탭2: 영상 수집
# ══════════════════════════════════════════════

with tab2:
    if st.session_state.get('target_video') and st.session_state.get('auto_generate'):
        st.success(f"✅ **{st.session_state['target_video']['title']}** 선택 완료! **'콘텐츠 생성' 탭**을 클릭하면 자동으로 생성이 시작됩니다.")

    collect_tab1, collect_tab2 = st.tabs(["📡 채널 구독 영상", "🔍 키워드 검색"])

    # ── 채널 구독 영상 ──
    with collect_tab1:
        st.subheader("등록 채널 최신 영상 (최근 1주일)")

        channels = db_get_channels()
        active_channels = [ch for ch in channels if ch['is_active']]

        if not active_channels:
            st.info("활성화된 채널이 없습니다. '채널 관리' 탭에서 채널을 추가하세요.")
        else:
            col_info, col_btn = st.columns([4, 1])
            with col_info:
                st.caption(f"활성 채널 {len(active_channels)}개 모니터링 중")
            with col_btn:
                collect_now = st.button("📡 수집하기", key="collect_channel")

            # DB 캐시 확인 (이전 수집 결과)
            all_videos = db_get_cache('channels')

            # 수집 버튼 클릭 시 새로 수집
            if collect_now:
                db_clear_cache('channels')
                all_videos = []
                progress_bar = st.progress(0, text="채널 불러오는 중...")
                for i, ch in enumerate(active_channels):
                    progress_bar.progress(
                        i / len(active_channels),
                        text=f"({i+1}/{len(active_channels)}) {ch['channel_name']} 수집 중..."
                    )
                    videos = fetch_recent_videos(ch['channel_id'])
                    all_videos.extend(videos)
                progress_bar.progress(1.0, text="완료!")
                progress_bar.empty()
                db_set_cache('channels', all_videos)

            if all_videos is None:
                st.info("📡 **수집하기** 버튼을 눌러 최신 영상을 가져오세요.")
            elif all_videos:
                st.caption("이전 수집 결과입니다. 최신 영상을 보려면 📡 수집하기를 클릭하세요.")

            if all_videos is not None and len(all_videos) == 0:
                st.info("최근 1주일 내 새 영상이 없습니다.")
            elif all_videos:
                all_videos.sort(key=lambda v: v['published'], reverse=True)
                st.write(f"**{len(all_videos)}개 영상 발견**")
                for video in all_videos:
                    render_video_card(video, key_suffix="ch")

    # ── 키워드 검색 ──
    with collect_tab2:
        st.subheader("키워드로 관련 영상 검색")
        st.caption("YouTube RSS 검색을 사용합니다. API 키 불필요, 무료.")

        # 키워드 프리셋 버튼
        st.write("**빠른 검색 키워드**")
        preset_cols = st.columns(3)
        for i, kw in enumerate(DEFAULT_KEYWORDS):
            with preset_cols[i % 3]:
                if st.button(kw, key=f"preset_{i}"):
                    st.session_state['keyword_input'] = kw

        st.divider()

        keyword = st.text_input(
            "검색어 직접 입력",
            value=st.session_state.get('keyword_input', ''),
            placeholder="예) 법인세 절세 방법",
            key="keyword_input",
        )

        col1, col2 = st.columns([2, 1])
        with col2:
            max_results = st.selectbox("최대 결과 수", [10, 20, 30], index=1)

        if keyword:
            kw_cache_key = f'keyword_{keyword}'
            cached_kw_results = db_get_cache(kw_cache_key)

            col_search, col_refresh = st.columns([1, 1])
            with col_search:
                do_search = st.button("🔍 검색", key="do_search")
            with col_refresh:
                if cached_kw_results is not None:
                    do_refresh_kw = st.button("🔄 재검색", key="refresh_kw")
                else:
                    do_refresh_kw = False

            if do_refresh_kw:
                db_clear_cache(kw_cache_key)
                cached_kw_results = None

            if do_search or do_refresh_kw:
                with st.spinner(f'"{keyword}" 검색 중...'):
                    results = search_videos_by_keyword(keyword, max_results=max_results)
                if not results:
                    st.warning("검색 결과가 없습니다. 다른 키워드를 시도해보세요.")
                else:
                    db_set_cache(kw_cache_key, results)
                    db_set_cache('__last_keyword', keyword)
                    cached_kw_results = results

            if cached_kw_results:
                cached_kw_results.sort(key=lambda v: v.get('published', ''), reverse=True)
                st.caption("오늘 수집된 캐시 데이터입니다.")
                st.write(f"**{len(cached_kw_results)}개 영상 발견**")
                for video in cached_kw_results:
                    render_video_card(video, key_suffix="kw")

        # 여러 키워드 일괄 검색
        st.divider()
        st.write("**여러 키워드 일괄 검색**")
        bulk_keywords = st.text_area(
            "키워드 목록 (한 줄에 하나씩)",
            placeholder="법인세 절세\n법인 노무관리\n중소기업 세금",
            height=120,
        )
        if bulk_keywords:
            bulk_key = 'bulk_' + hashlib.md5(bulk_keywords.strip().encode()).hexdigest()[:8]
            cached_bulk = db_get_cache(bulk_key)

            col_bulk, col_bulk_refresh = st.columns([1, 1])
            with col_bulk:
                do_bulk = st.button("📋 일괄 검색", key="bulk_search")
            with col_bulk_refresh:
                if cached_bulk is not None:
                    do_bulk_refresh = st.button("🔄 재검색", key="refresh_bulk")
                else:
                    do_bulk_refresh = False

            if do_bulk_refresh:
                db_clear_cache(bulk_key)
                cached_bulk = None

            if do_bulk or do_bulk_refresh:
                kw_list = [k.strip() for k in bulk_keywords.strip().splitlines() if k.strip()]
                all_results = []
                seen_ids = set()
                progress = st.progress(0)
                for idx, kw in enumerate(kw_list):
                    with st.spinner(f'"{kw}" 검색 중... ({idx + 1}/{len(kw_list)})'):
                        videos = search_videos_by_keyword(kw, max_results=10)
                        for v in videos:
                            if v['video_id'] not in seen_ids:
                                seen_ids.add(v['video_id'])
                                all_results.append(v)
                    progress.progress((idx + 1) / len(kw_list))
                progress.empty()
                if all_results:
                    db_set_cache(bulk_key, all_results)
                    cached_bulk = all_results
                else:
                    st.warning("검색 결과가 없습니다.")

            if cached_bulk:
                cached_bulk.sort(key=lambda v: v.get('published', ''), reverse=True)
                st.caption("오늘 수집된 캐시 데이터입니다.")
                st.write(f"**총 {len(cached_bulk)}개 영상 발견 (중복 제거됨)**")
                for video in cached_bulk:
                    render_video_card(video, key_suffix="bulk")


# ══════════════════════════════════════════════
# 탭3: 콘텐츠 생성
# ══════════════════════════════════════════════

with tab3:
    st.subheader("콘텐츠 생성")

    target = st.session_state.get('target_video')
    auto_generate = st.session_state.get('auto_generate', False)
    gen_state = st.session_state.get('gen_state', 'idle')

    if target:
        col_info, col_btn = st.columns([6, 1])
        with col_info:
            st.info(f"선택된 영상: **{target['title']}** — {target['url']}")
        with col_btn:
            if st.button("✕ 해제"):
                del st.session_state['target_video']
                st.session_state['auto_generate'] = False
                st.session_state['gen_state'] = 'idle'
                st.session_state.pop('transcript_text', None)
                st.rerun()
        url_input = target['url']
    else:
        url_input = st.text_input("YouTube URL 직접 입력", placeholder="https://www.youtube.com/watch?v=...")

    if url_input:
        video_id = extract_video_id(url_input)
        if not video_id:
            st.error("유효한 YouTube URL이 아닙니다.")
        else:
            video_title = target['title'] if target else url_input
            channel_name = target.get('channel_name', '') if target else ''
            already_done = db_is_processed(video_id)

            # DB에서 콘텐츠 자동 복원 (새로고침 후에도 에디터 유지)
            if f'content_{video_id}' not in st.session_state and already_done:
                saved = db_get_content(video_id)
                if saved:
                    st.session_state[f'content_{video_id}'] = saved

            # ── 상태: 자막 추출 실패 → 수동 입력 ──
            if gen_state == 'transcript_error':
                st.error("자막을 자동으로 추출할 수 없습니다. 영상 내용을 직접 입력해주세요.")
                manual_text = st.text_area("자막 직접 입력", height=200, key="manual_transcript")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("이 내용으로 콘텐츠 생성"):
                        if manual_text.strip():
                            st.session_state['transcript_text'] = manual_text.strip()
                            st.session_state['gen_state'] = 'generating'
                            st.session_state['auto_generate'] = True
                            st.rerun()
                        else:
                            st.warning("내용을 입력해주세요.")
                with col2:
                    if st.button("취소", key="cancel_manual"):
                        st.session_state['gen_state'] = 'idle'
                        st.rerun()

            # ── 상태: 자막이 짧음 → 계속 여부 확인 ──
            elif gen_state == 'short_warning':
                st.warning("자막이 매우 짧습니다 (2분 미만 영상일 수 있음). 계속 진행할까요?")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("계속 진행"):
                        st.session_state['gen_state'] = 'generating'
                        st.session_state['auto_generate'] = True
                        st.rerun()
                with col2:
                    if st.button("취소", key="cancel_short"):
                        st.session_state['gen_state'] = 'idle'
                        st.session_state['auto_generate'] = False
                        st.rerun()

            # ── 상태: 자동/수동 생성 실행 ──
            elif auto_generate and gen_state in ('idle', 'generating'):
                if gen_state == 'idle':
                    # 블로그만 자동 생성 (문자는 블로그 완성 후 수동)
                    st.session_state['selected_formats'] = ['blog']

                    # Step 1: 자막 추출
                    with st.status("Step 1: 자막 추출 중...", expanded=True) as s1:
                        import logging, io
                        _log_stream = io.StringIO()
                        _log_handler = logging.StreamHandler(_log_stream)
                        _log_handler.setLevel(logging.DEBUG)
                        _log_handler.setFormatter(logging.Formatter('%(message)s'))
                        _yt_logger = logging.getLogger('modules.youtube')
                        _yt_logger.setLevel(logging.DEBUG)
                        _yt_logger.addHandler(_log_handler)

                        result = get_transcript(video_id)

                        _yt_logger.removeHandler(_log_handler)
                        _debug_log = _log_stream.getvalue()
                        if _debug_log:
                            with st.expander("자막 추출 디버그 로그", expanded=True):
                                st.code(_debug_log, language="text")

                        if 'error' in result:
                            s1.update(label="Step 1: 자막 추출 실패", state="error")
                            st.session_state['gen_state'] = 'transcript_error'
                            st.session_state['auto_generate'] = False
                            st.rerun()

                        transcript_text = result['text']
                        lang_info = f"{result['language']} ({result['source']})"
                        st.write(f"자막 언어: {lang_info} | {len(transcript_text):,}자")

                        if is_transcript_too_short(transcript_text):
                            s1.update(label="Step 1: 자막이 짧음 — 확인 필요", state="running")
                            st.session_state['transcript_text'] = transcript_text
                            st.session_state['gen_state'] = 'short_warning'
                            st.session_state['auto_generate'] = False
                            st.rerun()

                        st.session_state['transcript_text'] = transcript_text
                        s1.update(label="Step 1: 자막 추출 완료 ✅", state="complete")

                # Step 2: AI 콘텐츠 생성
                with st.status("Step 2: AI 콘텐츠 생성 중...", expanded=True) as s2:
                    transcript_for_gen = st.session_state.get('transcript_text', '')
                    selected_formats = st.session_state.get('selected_formats', ['sms', 'blog'])
                    content = generate_content(transcript_for_gen, formats=selected_formats)
                    if 'error' in content:
                        s2.update(label="Step 2: AI 생성 실패", state="error")
                        st.error(content['error'])
                        st.session_state['gen_state'] = 'idle'
                        st.session_state['auto_generate'] = False
                    else:
                        db_save_content(video_id, video_title, channel_name, content)
                        st.session_state[f'content_{video_id}'] = content
                        st.session_state['auto_generate'] = False
                        st.session_state['gen_state'] = 'done'
                        s2.update(label="Step 2: 콘텐츠 생성 완료 ✅", state="complete")

            # ── 상태: 대기 중 (생성 버튼 표시) ──
            elif not st.session_state.get(f'content_{video_id}'):
                if already_done:
                    choice = st.radio(
                        "이미 처리된 영상입니다.",
                        ["기존 결과 사용", "재생성"],
                        horizontal=True,
                    )
                    if choice == "기존 결과 사용":
                        existing = db_get_content(video_id)
                        if existing:
                            st.session_state[f'content_{video_id}'] = existing
                            st.rerun()
                    else:
                        if st.button("▶ 재생성 시작"):
                            st.session_state['auto_generate'] = True
                            st.session_state['gen_state'] = 'idle'
                            st.session_state.pop('transcript_text', None)
                            st.rerun()
                else:
                    if st.button("▶ 콘텐츠 생성 시작"):
                        st.session_state['auto_generate'] = True
                        st.session_state['gen_state'] = 'idle'
                        st.rerun()

            # ── Step 3: 인라인 에디터 (콘텐츠가 있을 때 항상 표시) ──
            content = st.session_state.get(f'content_{video_id}')
            if content and isinstance(content, dict) and any(k in content for k in ('sms', 'blog')):
                st.divider()
                st.subheader("Step 3: 콘텐츠 편집 및 저장")

                available_tabs = ["📝 블로그"]
                tab_keys = ['blog']
                if 'sms' in content:
                    available_tabs.append("📱 문자 (SMS/LMS)")
                    tab_keys.append('sms')

                rendered_tabs = st.tabs(available_tabs)
                tab_map = dict(zip(tab_keys, rendered_tabs))

                if 'blog' in tab_map:
                    with tab_map['blog']:
                        blog = content.get('blog', {})
                        blog_title = st.text_input("블로그 제목", value=blog.get('title', ''), key=f"blog_title_{video_id}")
                        blog_meta_title = st.text_input(
                            f"메타 타이틀 (SEO, 60자 이내) — 현재 {len(blog.get('meta_title', blog.get('title', '')))}자",
                            value=blog.get('meta_title', blog.get('title', '')),
                            key=f"blog_meta_title_{video_id}",
                        )
                        blog_excerpt = st.text_area(
                            "발췌문 (Excerpt) — 블로그 목록에 표시되는 요약",
                            value=blog.get('excerpt', blog.get('meta_description', '')),
                            height=80,
                            key=f"blog_excerpt_{video_id}",
                        )
                        blog_meta = st.text_input("메타 설명 (SEO)", value=blog.get('meta_description', ''), key=f"blog_meta_{video_id}")
                        _tag_options = ["value-up", "finence"]
                        _current_tag = blog.get('tags', ['value-up'])[0] if blog.get('tags') else 'value-up'
                        _tag_index = _tag_options.index(_current_tag) if _current_tag in _tag_options else 0
                        blog_tag = st.selectbox("태그", options=_tag_options, index=_tag_index, key=f"blog_tag_{video_id}")
                        col_edit, col_preview = st.columns(2)
                        with col_edit:
                            st.caption("마크다운 편집")
                            blog_content = st.text_area(
                                "본문 (마크다운)",
                                value=blog.get('content', ''),
                                height=400,
                                key=f"blog_content_{video_id}",
                            )
                        with col_preview:
                            st.caption("미리보기")
                            st.markdown(blog_content)
                        schema_faq = content.get('blog', {}).get('schema_faq', [])
                        if schema_faq:
                            with st.expander("📋 FAQ Schema 확인 (Google 구조화 데이터)"):
                                for idx, faq in enumerate(schema_faq):
                                    st.markdown(f"**Q{idx + 1}. {faq.get('question', '')}**")
                                    st.markdown(f"A. {faq.get('answer', '')}")
                                    if idx < len(schema_faq) - 1:
                                        st.divider()
                                st.divider()
                                import json
                                jsonld = json.dumps({
                                    "@context": "https://schema.org",
                                    "@type": "FAQPage",
                                    "mainEntity": [
                                        {
                                            "@type": "Question",
                                            "name": faq.get("question", ""),
                                            "acceptedAnswer": {
                                                "@type": "Answer",
                                                "text": faq.get("answer", "")
                                            }
                                        }
                                        for faq in schema_faq
                                    ]
                                }, ensure_ascii=False, indent=2)
                                jsonld_code = f'<script type="application/ld+json">\n{jsonld}\n</script>'
                                st.code(jsonld_code, language="html")
                                st.caption("👆 위 코드를 복사하여 Ghost 포스트 설정 → Code Injection (Header)에 붙여넣으세요.")
                        if st.button("💾 블로그 내용 저장", key=f"save_blog_{video_id}"):
                            content['blog']['title'] = blog_title
                            content['blog']['meta_title'] = blog_meta_title
                            content['blog']['excerpt'] = blog_excerpt
                            content['blog']['meta_description'] = blog_meta
                            content['blog']['content'] = blog_content
                            content['blog']['tags'] = [blog_tag]
                            db_save_content(video_id, video_title, channel_name, content)
                            st.success("저장 완료!")

                        st.divider()
                        st.markdown("**✏️ 내용 보완 요청**")
                        st.caption("추가하고 싶은 내용, 수정 방향, 강조할 포인트 등을 자유롭게 적어주세요. 기존 글에 자연스럽게 반영해 다시 작성합니다.")
                        user_notes = st.text_area(
                            "보완 메모",
                            placeholder="예) 가족법인 설립 시 주의해야 할 명의신탁 리스크 내용을 추가해줘\n예) 3번째 섹션을 더 구체적인 사례 위주로 바꿔줘\n예) 마지막에 비즈파트너즈 무료 상담 CTA 문구를 넣어줘",
                            height=120,
                            key=f"blog_notes_{video_id}",
                        )
                        if st.button("🔄 보완 재작성", key=f"refine_blog_{video_id}", type="primary"):
                            if user_notes.strip():
                                current_blog = {
                                    'title': st.session_state.get(f"blog_title_{video_id}", blog_title),
                                    'content': st.session_state.get(f"blog_content_{video_id}", blog_content),
                                }
                                with st.spinner("블로그 보완 중... (약 20~30초)"):
                                    refined = refine_blog(current_blog, user_notes.strip())
                                if 'error' in refined:
                                    st.error(refined['error'])
                                else:
                                    content['blog'].update(refined)
                                    db_save_content(video_id, video_title, channel_name, content)
                                    st.session_state[f'content_{video_id}'] = content
                                    # 위젯 키 초기화 → 새 내용으로 재렌더링
                                    for k in [f'blog_title_{video_id}', f'blog_meta_title_{video_id}',
                                              f'blog_excerpt_{video_id}',
                                              f'blog_meta_{video_id}', f'blog_content_{video_id}',
                                              f'blog_tag_{video_id}', f'blog_notes_{video_id}']:
                                        st.session_state.pop(k, None)
                                    st.success("보완 완료! 에디터가 새 내용으로 업데이트됩니다.")
                                    st.rerun()
                            else:
                                st.warning("보완 메모를 입력해주세요.")

                        st.divider()
                        st.markdown("**대표 이미지 생성**")
                        img_col1, img_col2 = st.columns([3, 1])
                        with img_col1:
                            img_category = st.text_input(
                                "카테고리 태그",
                                value=blog.get('tags', ['value-up'])[0] if blog.get('tags') else "value-up",
                                key=f"img_category_{video_id}",
                                help="이미지 우측 상단에 표시되는 태그",
                            )
                        with img_col2:
                            st.write("")
                            gen_img_btn = st.button("🖼️ 이미지 생성", key=f"gen_img_{video_id}", type="primary")

                        if gen_img_btn or f'blog_image_{video_id}' in st.session_state:
                            if gen_img_btn:
                                with st.spinner("블로그 이미지 생성 중..."):
                                    from pathlib import Path as _Path
                                    _out_dir = _Path(__file__).parent / "generated_images"
                                    _out_dir.mkdir(parents=True, exist_ok=True)
                                    import re as _re
                                    _title_for_img = st.session_state.get(f"blog_title_{video_id}", blog_title)
                                    _kw = _re.sub(r'[^\w가-힣a-zA-Z0-9]', ' ', _title_for_img)
                                    _kw = '_'.join(_kw.split()[:4])[:30]
                                    _file_name = f"비즈인사이트_{_kw}.png" if _kw else f"비즈인사이트_{video_id}.png"
                                    blog_img_bytes = generate_card_image(
                                        title=_title_for_img,
                                        category=img_category,
                                        size="blog",
                                        save_path=_out_dir / _file_name,
                                    )
                                st.session_state[f'blog_image_{video_id}'] = blog_img_bytes
                                st.session_state[f'blog_image_fname_{video_id}'] = _file_name

                            blog_img_bytes = st.session_state.get(f'blog_image_{video_id}')
                            if blog_img_bytes:
                                _file_name = st.session_state.get(f'blog_image_fname_{video_id}', f"비즈인사이트_{video_id}.png")
                                st.caption("📰 블로그 피처 이미지 (1200×630)")
                                st.image(blog_img_bytes, use_container_width=True)
                                st.download_button(
                                    "⬇️ 블로그 이미지 저장",
                                    data=blog_img_bytes,
                                    file_name=_file_name,
                                    mime="image/png",
                                    key=f"dl_blog_{video_id}",
                                )


                # ── 문자 생성 (블로그 기반) ──
                if 'sms' not in tab_map:
                    # 아직 문자가 없으면 생성 버튼 표시
                    st.divider()
                    st.markdown("**📱 문자 콘텐츠 생성** — 위 블로그 내용을 바탕으로 문자를 생성합니다.")
                    col_sms1, col_sms2 = st.columns(2)
                    with col_sms1:
                        if st.button("📱 간단 추출 (API 미사용)", key=f"sms_local_{video_id}"):
                            blog_data_for_sms = {
                                'title': st.session_state.get(f"blog_title_{video_id}", content.get('blog', {}).get('title', '')),
                                'content': st.session_state.get(f"blog_content_{video_id}", content.get('blog', {}).get('content', '')),
                            }
                            sms_result = extract_sms_from_blog(blog_data_for_sms)
                            content['sms'] = sms_result
                            db_save_content(video_id, video_title, channel_name, content)
                            st.session_state[f'content_{video_id}'] = content
                            st.rerun()
                    with col_sms2:
                        if st.button("🤖 AI 요약 (Claude API)", key=f"sms_ai_{video_id}"):
                            blog_data_for_sms = {
                                'title': st.session_state.get(f"blog_title_{video_id}", content.get('blog', {}).get('title', '')),
                                'content': st.session_state.get(f"blog_content_{video_id}", content.get('blog', {}).get('content', '')),
                            }
                            with st.spinner("문자 콘텐츠 생성 중..."):
                                sms_result = generate_sms_from_blog(blog_data_for_sms)
                            if 'error' in sms_result:
                                st.error(sms_result['error'])
                            else:
                                content['sms'] = sms_result
                                db_save_content(video_id, video_title, channel_name, content)
                                st.session_state[f'content_{video_id}'] = content
                                st.rerun()

                if 'sms' in tab_map:
                    with tab_map['sms']:
                        sms = content.get('sms', {})
                        sms_title = st.text_input("제목", value=sms.get('title', ''), key=f"sms_title_{video_id}")
                        sms_body = st.text_area("본문", value=sms.get('body', ''), height=150, key=f"sms_body_{video_id}")
                        try:
                            byte_count = len(sms_body.encode('euc-kr'))
                        except Exception:
                            byte_count = len(sms_body.encode('utf-8'))
                        msg_type = "SMS" if byte_count <= 90 else "LMS"
                        color = "green" if byte_count <= 90 else "orange"
                        st.markdown(f":{color}[**{msg_type}** | {byte_count}바이트]")
                        col_save_sms, col_regen_sms = st.columns([1, 1])
                        with col_save_sms:
                            if st.button("💾 문자 내용 저장", key=f"save_sms_{video_id}"):
                                content['sms']['title'] = sms_title
                                content['sms']['body'] = sms_body
                                content['sms']['byte_count'] = byte_count
                                db_save_content(video_id, video_title, channel_name, content)
                                st.success("저장 완료!")
                        with col_regen_sms:
                            if st.button("🔄 문자 재생성 (AI)", key=f"regen_sms_{video_id}"):
                                blog_data_for_sms = {
                                    'title': st.session_state.get(f"blog_title_{video_id}", content.get('blog', {}).get('title', '')),
                                    'content': st.session_state.get(f"blog_content_{video_id}", content.get('blog', {}).get('content', '')),
                                }
                                with st.spinner("문자 재생성 중..."):
                                    sms_result = generate_sms_from_blog(blog_data_for_sms)
                                if 'error' in sms_result:
                                    st.error(sms_result['error'])
                                else:
                                    content['sms'] = sms_result
                                    db_save_content(video_id, video_title, channel_name, content)
                                    st.session_state[f'content_{video_id}'] = content
                                    for k in [f'sms_title_{video_id}', f'sms_body_{video_id}']:
                                        st.session_state.pop(k, None)
                                    st.rerun()

                st.divider()
                st.subheader("배포")

                if 'blog' in content:
                    deploy_tab1, deploy_tab2 = st.tabs(["📰 즉시 발행 (Ghost)", "⏰ 예약 발행"])

                    with deploy_tab1:
                        st.caption("Ghost 블로그에 즉시 발행합니다.")
                        if st.button("📰 지금 발행", key=f"publish_now_{video_id}", type="primary"):
                            blog_data = {
                                'title': st.session_state.get(f"blog_title_{video_id}", content['blog'].get('title', '')),
                                'meta_title': st.session_state.get(f"blog_meta_title_{video_id}", content['blog'].get('meta_title', content['blog'].get('title', ''))),
                                'meta_description': st.session_state.get(f"blog_meta_{video_id}", content['blog'].get('meta_description', '')),
                                'excerpt': st.session_state.get(f"blog_excerpt_{video_id}", content['blog'].get('excerpt', '')),
                                'content': st.session_state.get(f"blog_content_{video_id}", content['blog'].get('content', '')),
                                'tags': [st.session_state.get(f"blog_tag_{video_id}", content['blog'].get('tags', ['value-up'])[0])],
                                'schema_faq': content['blog'].get('schema_faq', []),
                            }
                            with st.spinner("Ghost에 발행 중..."):
                                # 이미지 업로드
                                feature_url = None
                                img_bytes = st.session_state.get(f'blog_image_{video_id}')
                                if img_bytes:
                                    img_fname = st.session_state.get(f'blog_image_fname_{video_id}', 'feature.png')
                                    img_result = upload_image(img_bytes, img_fname)
                                    if 'url' in img_result:
                                        feature_url = img_result['url']

                                result = publish_post(blog=blog_data, feature_image_url=feature_url)

                            if 'error' in result:
                                st.error(f"발행 실패: {result['error']}")
                                db_add_publication(video_id, 'ghost', '', 'failed')
                            else:
                                st.success(f"발행 완료! {result.get('url', '')}")
                                db_add_publication(video_id, 'ghost', result.get('url', ''), 'published')

                    with deploy_tab2:
                        st.caption("예약 시간에 자동으로 Ghost에 발행됩니다.")
                        sched_col1, sched_col2 = st.columns(2)
                        with sched_col1:
                            sched_date = st.date_input("발행 날짜", key=f"sched_date_{video_id}")
                        with sched_col2:
                            sched_time = st.time_input("발행 시간", value=datetime.strptime("09:00", "%H:%M").time(), key=f"sched_time_{video_id}")

                        if st.button("⏰ 예약 등록", key=f"schedule_{video_id}"):
                            sched_dt = datetime.combine(sched_date, sched_time)
                            if sched_dt <= datetime.now():
                                st.error("예약 시간은 현재 시간 이후여야 합니다.")
                            else:
                                sched_iso = sched_dt.strftime('%Y-%m-%dT%H:%M:%S')
                                db_add_scheduled(video_id, 'ghost', sched_iso)
                                st.success(f"예약 등록 완료: {sched_dt.strftime('%Y-%m-%d %H:%M')}")

                else:
                    st.info("블로그 콘텐츠를 먼저 생성해주세요.")


# ══════════════════════════════════════════════
# 탭4: 처리 이력
# ══════════════════════════════════════════════

with tab4:
    st.subheader("처리 이력")

    if st.session_state.pop('open_from_history', False):
        target = st.session_state.get('target_video', {})
        st.success(f"✅ **{target.get('title', '')}** 선택 완료! **'콘텐츠 생성' 탭**을 클릭하면 에디터가 바로 열립니다.")

    conn = get_db()
    rows = _fetchall(conn,
        "SELECT video_id, title, channel_name, processed_at, status FROM processed_videos ORDER BY processed_at DESC LIMIT 50"
    )
    conn.close()

    if not rows:
        st.info("처리된 영상이 없습니다.")
    else:
        st.write(f"**총 {len(rows)}개**")
        for row in rows:
            status_icon = "✅" if row['status'] == 'completed' else "❌"
            col1, col2 = st.columns([6, 1])
            with col1:
                st.markdown(f"{status_icon} **{row['title'] or row['video_id']}**")
                st.caption(f"{row['channel_name']} | {row['processed_at'][:16]} | https://www.youtube.com/watch?v={row['video_id']}")
            with col2:
                if st.button("열기", key=f"open_{row['video_id']}"):
                    vid = row['video_id']
                    video = {
                        'video_id': vid,
                        'title': row['title'] or vid,
                        'channel_name': row['channel_name'] or '',
                        'url': f"https://www.youtube.com/watch?v={vid}",
                    }
                    st.session_state['target_video'] = video
                    st.session_state['auto_generate'] = False
                    st.session_state['gen_state'] = 'idle'
                    st.session_state.pop('transcript_text', None)
                    # 콘텐츠 미리 로드 (탭3에서 바로 에디터 표시)
                    saved = db_get_content(vid)
                    if saved:
                        st.session_state[f'content_{vid}'] = saved
                    db_set_cache('__last_target_video', video)
                    st.session_state['open_from_history'] = True
                    st.rerun()
            st.divider()


# ══════════════════════════════════════════════
# 탭5: 예약 관리
# ══════════════════════════════════════════════

with tab5:
    st.subheader("예약 발행 관리")

    # Ghost 연결 상태 확인
    with st.expander("Ghost 연결 상태 확인"):
        if st.button("연결 테스트"):
            with st.spinner("Ghost API 연결 확인 중..."):
                conn_result = test_connection()
            if 'error' in conn_result:
                st.error(f"연결 실패: {conn_result['error']}")
            else:
                st.success(f"연결 성공! 사이트: **{conn_result.get('title', '')}** ({conn_result.get('url', '')})")

    # 예약 시간 도래한 포스트 자동 발행
    st.divider()
    col_auto, col_refresh = st.columns([3, 1])
    with col_auto:
        st.caption("페이지 로드 시 예약 시간이 지난 포스트를 자동으로 발행합니다.")
    with col_refresh:
        run_now = st.button("🔄 지금 실행")

    if run_now:
        with st.spinner("예약 포스트 발행 중..."):
            count = process_pending_schedules()
        if count > 0:
            st.success(f"{count}개 포스트 발행 완료!")
            st.rerun()
        else:
            st.info("발행할 예약 포스트가 없습니다.")

    # 예약 목록
    st.divider()
    schedules = db_get_scheduled()
    if not schedules:
        st.info("예약된 포스트가 없습니다.")
    else:
        st.write(f"**총 {len(schedules)}개 예약**")
        for sched in schedules:
            status_map = {
                'pending': '⏳ 대기',
                'published': '✅ 발행됨',
                'failed': '❌ 실패',
                'cancelled': '🚫 취소',
            }
            status_label = status_map.get(sched['status'], sched['status'])
            col1, col2, col3 = st.columns([4, 2, 1])
            with col1:
                st.markdown(f"{status_label} **{sched.get('title', sched['video_id'])}**")
                st.caption(f"예약: {sched['scheduled_at'][:16]}")
                if sched.get('ghost_url'):
                    st.caption(f"URL: {sched['ghost_url']}")
                if sched.get('error_msg'):
                    st.caption(f"오류: {sched['error_msg']}")
            with col2:
                st.caption(f"등록일: {sched['created_at'][:16]}")
            with col3:
                if sched['status'] == 'pending':
                    if st.button("취소", key=f"cancel_sched_{sched['id']}"):
                        db_update_scheduled(sched['id'], 'cancelled')
                        st.rerun()
                elif sched['status'] == 'failed':
                    if st.button("재시도", key=f"retry_sched_{sched['id']}"):
                        db_update_scheduled(sched['id'], 'pending')
                        st.rerun()
            st.divider()


# ══════════════════════════════════════════════
# 페이지 로드 시 예약 자동 처리
# ══════════════════════════════════════════════
process_pending_schedules()
