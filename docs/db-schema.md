# DB 스키마 — pipeline.db (SQLite)

> 필요 시에만 읽으세요. `database/schema.sql`이 정본입니다.

## 테이블 목록

### channels — 모니터링 채널
```sql
CREATE TABLE channels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id   TEXT NOT NULL UNIQUE,
    channel_name TEXT,
    added_at     TEXT DEFAULT (datetime('now')),
    is_active    INTEGER DEFAULT 1
);
```

### processed_videos — 처리 이력 (중복 방지 핵심)
```sql
CREATE TABLE processed_videos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL UNIQUE,
    title        TEXT,
    channel_name TEXT,
    processed_at TEXT DEFAULT (datetime('now')),
    status       TEXT DEFAULT 'completed', -- completed | failed
    content_json TEXT  -- 생성된 3가지 콘텐츠 JSON
);
```

### publications — 발행 이력
```sql
CREATE TABLE publications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL,
    channel      TEXT NOT NULL, -- 'ghost' | 'instagram' | 'sms'
    published_at TEXT DEFAULT (datetime('now')),
    url          TEXT,
    status       TEXT DEFAULT 'pending' -- pending | published | failed
);
```

### video_cache — API 호출 캐시 (당일 기준)
캐시 키 규칙:
- `channels` — 채널 구독 영상 목록
- `keyword_{검색어}` — 키워드 검색 결과
- `bulk_{md5해시}` — 일괄 키워드 검색 결과
- `__last_keyword`, `__last_target_video` — 세션 복원용
