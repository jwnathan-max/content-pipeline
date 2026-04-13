-- 법인 컨설팅 콘텐츠 파이프라인 DB 스키마

-- 모니터링 채널 목록
CREATE TABLE IF NOT EXISTS channels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id   TEXT NOT NULL UNIQUE,
    channel_name TEXT,
    added_at     TEXT DEFAULT (datetime('now')),
    is_active    INTEGER DEFAULT 1
);

-- 처리 이력 (중복 방지 핵심 테이블)
CREATE TABLE IF NOT EXISTS processed_videos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL UNIQUE,
    title        TEXT,
    channel_name TEXT,
    processed_at TEXT DEFAULT (datetime('now')),
    status       TEXT DEFAULT 'completed',  -- completed | failed
    content_json TEXT                       -- 생성된 3가지 콘텐츠 JSON
);

-- 영상 수집 캐시 (당일 재접속 시 재수집 방지)
CREATE TABLE IF NOT EXISTS video_cache (
    cache_key   TEXT NOT NULL PRIMARY KEY,
    data_json   TEXT,
    cached_at   TEXT DEFAULT (datetime('now'))
);

-- 발행 이력
CREATE TABLE IF NOT EXISTS publications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL,
    channel      TEXT NOT NULL,             -- 'ghost' | 'instagram' | 'sms'
    published_at TEXT DEFAULT (datetime('now')),
    url          TEXT,
    status       TEXT DEFAULT 'pending'     -- pending | published | failed
);

-- 예약 발행 큐
CREATE TABLE IF NOT EXISTS scheduled_posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL,
    channel       TEXT NOT NULL DEFAULT 'ghost',  -- 'ghost' | 'instagram' | 'sms'
    scheduled_at  TEXT NOT NULL,                   -- ISO 8601 예약 시간
    created_at    TEXT DEFAULT (datetime('now')),
    status        TEXT DEFAULT 'pending',          -- pending | published | failed | cancelled
    ghost_post_id TEXT,                            -- Ghost 발행 후 반환된 post ID
    ghost_url     TEXT,                            -- Ghost 발행 후 URL
    error_msg     TEXT                             -- 실패 시 에러 메시지
);
