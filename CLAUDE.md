# CLAUDE.md — 법인 컨설팅 자동화 콘텐츠 파이프라인

> 이 파일은 항상 로딩됩니다. 핵심 컨텍스트만 담습니다.
> 상세 내용은 **필요할 때만** `docs/` 파일을 읽으세요.

---

## 프로젝트 개요

**목적:** 법인 세무·노무·재무 관련 유튜브 영상 자동 수집 → AI 가공 → 3채널 배포  
**사용자:** 코딩 지식 없는 내부 마케팅 팀원 (Streamlit 웹 UI)  
**엔드 수신자:** 법인 대표 (30~50대, 세금 절감·리스크에 민감)

---

## 기술 스택

| 레이어 | 선택 |
|---|---|
| Frontend | Streamlit (`app.py`) |
| Backend | Python 3.10+ |
| DB | SQLite (`database/pipeline.db`) |
| AI | Claude claude-sonnet-4-5 — tool_use 방식으로 3포맷 동시 생성 |
| 이미지 | Pillow 카드 생성 (블로그 1200×630) / Unsplash 대안 |
| 영상 수집 | yt-dlp (YouTube RSS 404 문제로 대체) |
| 자막 | youtube-transcript-api |
| 블로그 배포 | Ghost Admin API (JWT 인증) |
| 문자 | 알리고 API (미연동 시 TXT 파일 다운로드) |
| 인스타 | Instagram Graph API (Phase 2) |

---

## 디렉토리 구조

```
content-pipeline/
├── CLAUDE.md               ← 이 파일 (항상 로딩)
├── docs/                   ← 상세 참조 문서 (필요 시만 읽기)
│   ├── db-schema.md        ← DB 테이블 스키마 전체
│   ├── pipeline-flow.md    ← 파이프라인 흐름 + UI 탭 구조
│   ├── api-integrations.md ← 환경변수, 보안, Ghost/인스타/알리고 연동
│   └── code-patterns.md    ← 비자명한 구현 패턴 (tool_use, yt-dlp 등)
├── app.py                  ← Streamlit 메인 진입점
├── .env                    ← API 키 (절대 git 커밋 금지)
├── .env.example
├── requirements.txt
├── database/
│   ├── pipeline.db
│   └── schema.sql          ← DB 초기화 스크립트 (정본)
├── modules/
│   ├── youtube.py          ← yt-dlp 수집 + 자막 추출
│   ├── ai_processor.py     ← Claude tool_use, 3포맷 생성, 청크 처리
│   ├── image_generator.py  ← Pillow 카드 이미지 생성
│   ├── ghost_publisher.py  ← Ghost Admin API
│   ├── instagram.py        ← Instagram Graph API (Phase 2)
│   └── aligo.py            ← 문자 발송 (Phase 2)
├── prompts/
│   ├── system_prompt.txt   ← 브랜드 보이스 프롬프트
│   └── content_format.txt  ← 3포맷 생성 프롬프트 + 저자 프로필 고정
└── logs/                   ← API 응답 로그 (git 제외)
```

---

## 개발 Phase 현황

### Phase 1 — MVP ✅ 완료
- [x] SQLite DB + schema.sql
- [x] Streamlit 탭 레이아웃 (채널관리·영상수집·콘텐츠생성·이력)
- [x] yt-dlp 채널 구독 영상 수집 (최근 168시간)
- [x] 키워드 검색 (yt-dlp, API 키 불필요)
- [x] 자막 추출 + 에러 처리
- [x] Claude tool_use 3포맷 동시 생성
- [x] 인라인 에디터 + DB 저장
- [x] 블로그 이미지 생성 (Pillow, 1200×630)

### Phase 2 — 배포 연동
- [ ] Ghost Admin API 발행
- [ ] 알리고 문자 발송 (또는 TXT 다운로드)
- [ ] Instagram 이미지 생성(1080×1080) + 업로드

### Phase 3 — 운영 안정화
- [ ] 발행 이력 대시보드 (탭4)
- [ ] 프롬프트 버전 관리 UI
- [ ] Instagram 토큰 만료 알림

---

## 핵심 주의사항 (비자명한 것만)

- **AI 응답:** tool_use force 방식 사용 — 일반 텍스트 JSON 파싱 아님
- **영상 수집:** YouTube RSS 404 문제 → yt-dlp로 전체 대체
- **이미지:** 블로그 탭은 `generate_card_image(size="blog")`만 호출. 인스타 이미지는 Phase 2에서 별도 구현
- **자막 우선순위:** 한국어 수동 → 한국어 자동 → 영어(번역)
- **SMS 판정:** `text.encode('euc-kr')` 기준 90바이트 이하=SMS, 초과=LMS
- **Ghost JWT:** 매 요청마다 신규 생성 (만료 5분), `{id}:{secret}` 형태 키
- **보안:** `.env` git 커밋 금지, API 키 하드코딩 금지

---

## 상세 문서 참조 가이드

| 작업 유형 | 읽을 파일 |
|---|---|
| DB 스키마 확인 / 쿼리 작성 | `docs/db-schema.md` |
| 파이프라인 흐름 / UI 탭 이해 | `docs/pipeline-flow.md` |
| 환경변수 / API 연동 / 보안 | `docs/api-integrations.md` |
| 모듈 구현 패턴 / 비자명 동작 | `docs/code-patterns.md` |

---

## 실행

```bash
pip install -r requirements.txt
streamlit run app.py
# Windows: 실행.bat 더블클릭
```
