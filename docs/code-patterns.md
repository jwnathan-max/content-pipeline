# 구현 패턴 & 주의사항

> 필요 시에만 읽으세요. 코드와 다른 비자명한 동작들을 기록합니다.

## AI 콘텐츠 생성 — tool_use 방식 (비자명)
`modules/ai_processor.py`는 JSON 파싱 실패를 원천 차단하기 위해
Anthropic **tool_use** (`tool_choice: force`) 방식으로 구조화된 응답을 받습니다.
`CONTENT_TOOL` 스키마가 파일 상단에 정의, `block.input`으로 바로 dict 획득.

```python
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 8000  # tool_use 오버헤드 + 블로그 본문 분량 고려
CHUNK_SIZE = 8000  # 자막 8000자 초과 시 청크 분할 → 요약 → 재통합
```

## 채널 영상 수집 — yt-dlp (비자명)
YouTube RSS가 일부 채널에서 404 반환 → `fetch_recent_videos()`는 yt-dlp로 구현.
기본값: 최근 168시간(1주일), DateRange 필터로 서버측 컷.

```python
fetch_recent_videos(channel_id, hours=168)
```

## 자막 추출 우선순위
```python
TRANSCRIPT_PRIORITY = ['ko', 'ko-KR']  # 수동 자막 우선
TRANSCRIPT_FALLBACK  = ['en', 'en-US']  # 영어 자막 (번역 필요)
```

## SMS/LMS 바이트 카운터
```python
# EUC-KR 인코딩 기준 90바이트 이하 = SMS, 초과 = LMS
byte_count = len(text.encode('euc-kr'))
message_type = "SMS" if byte_count <= 90 else "LMS"
```

## 저자 프로필 (블로그 고정 삽입)
`prompts/content_format.txt`에 고정:
```
이규원 | (주)비즈파트너즈 팀장 · 비즈 인사이트 발행인
```
사이트: biz-insight.kr

## 블로그 이미지 생성 현황
- 블로그 탭에서 `generate_card_image(size="blog")` 호출 → 1200×630 PNG
- 인스타 1080×1080 이미지는 **인스타 탭 구현 시 별도 추가** 예정
- `generate_both()`는 현재 app.py에서 미사용 (블로그 전용으로 전환됨)
