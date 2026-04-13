# API 연동 & 보안 & 이미지 전략

> 필요 시에만 읽으세요.

## 환경변수 목록 (.env.example)

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...              # DALL·E 이미지 생성용
YOUTUBE_API_KEY=AIza...

GHOST_ADMIN_API_KEY=...            # {id}:{secret} 형태
GHOST_API_URL=https://your-site.com

INSTAGRAM_ACCESS_TOKEN=...
INSTAGRAM_ACCOUNT_ID=...

ALIGO_API_KEY=...
ALIGO_USER_ID=...
ALIGO_SENDER=01000000000

UNSPLASH_ACCESS_KEY=...            # DALL·E 실패 시 대안
```

## 보안 원칙
- `.env`는 절대 git 커밋 금지 (`.gitignore`에 명시)
- API 키 코드 하드코딩 절대 금지
- Ghost JWT: 매 요청마다 신규 생성 (만료 5분)
- Instagram 액세스 토큰: 만료일(60일) DB 저장, 7일 전 UI 경고

## Ghost JWT 생성 패턴
```python
import jwt, datetime
def generate_ghost_token(api_key: str) -> str:
    key_id, secret = api_key.split(':')
    iat = int(datetime.datetime.now().timestamp())
    payload = {'iat': iat, 'exp': iat + 300, 'aud': '/admin/'}
    return jwt.encode(payload, bytes.fromhex(secret),
                      algorithm='HS256', headers={'kid': key_id})
```

## Ghost 이미지 삽입 순서
1. `/ghost/api/admin/images/upload/` 로 이미지 먼저 업로드
2. 반환 URL → post의 `feature_image` 또는 본문 마크다운에 삽입

## 이미지 생성 전략
```
1순위: Pillow 카드 이미지 (image_generator.py)
  블로그: 1200×630 (generate_card_image size="blog")
  인스타: 1080×1080 — Phase 2에서 별도 구현 예정

2순위: Unsplash API (비용 절약 or DALL·E 실패 시)
  블로그 키워드 기반 무료 이미지, 저작권 표기 자동 추가
```

## 에러 처리 원칙
| 상황 | 처리 방식 |
|---|---|
| 자막 없는 영상 | 알림 + 수동 입력창 |
| 자막 너무 짧음 (2분 미만) | 경고 후 계속 여부 선택 |
| 영상 비공개/삭제 | 에러 메시지 + 목록 제거 옵션 |
| API 타임아웃 | 3회 자동 재시도 → 실패 시 logs/ 기록 |
| 이미 처리한 영상 | 재생성 or 기존 결과 사용 선택 |
| Ghost 발행 실패 | 에러 메시지 + 재시도 버튼 |
| 인스타 업로드 실패 | 에러 메시지 + 수동 복사 버튼 |
