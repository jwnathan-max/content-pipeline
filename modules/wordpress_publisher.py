"""
wordpress_publisher.py — WordPress REST API 발행 모듈
- Basic Auth (Application Password) 인증
- 이미지 업로드 → featured_media 설정
- 포스트 생성 (즉시 발행 / 예약 발행)
- Rank Math SEO 메타 설정
- 기존 글 목록 조회 (내부 링크용)
"""
import os
import json
import logging
import datetime
import requests
from base64 import b64encode
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()


def _get_config() -> dict:
    """WordPress API 설정 반환"""
    api_url = os.getenv("WP_API_URL", "").rstrip("/")
    username = os.getenv("WP_USERNAME", "")
    app_password = os.getenv("WP_APP_PASSWORD", "")
    if not api_url or not username or not app_password:
        raise ValueError("WP_API_URL, WP_USERNAME, WP_APP_PASSWORD가 모두 설정되어야 합니다.")
    return {"api_url": api_url, "username": username, "app_password": app_password}


def _auth_header(config: dict) -> dict:
    """Basic Auth 헤더 생성"""
    credentials = f"{config['username']}:{config['app_password']}"
    token = b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _get_or_create_category(config: dict, category_name: str) -> int | None:
    """카테고리 ID 반환. 없으면 생성."""
    headers = _auth_header(config)
    url = f"{config['api_url']}/wp-json/wp/v2/categories"

    # 검색
    resp = requests.get(url, headers=headers, params={"search": category_name}, timeout=10)
    if resp.status_code == 200:
        for cat in resp.json():
            if cat["name"] == category_name:
                return cat["id"]

    # 생성
    resp = requests.post(
        url, headers=headers,
        json={"name": category_name},
        timeout=10,
    )
    if resp.status_code in (200, 201):
        return resp.json()["id"]
    logger.warning(f"카테고리 생성 실패: {category_name} — {resp.text[:200]}")
    return None


def _get_or_create_tags(config: dict, tag_names: list[str]) -> list[int]:
    """태그 ID 목록 반환. 없으면 생성."""
    headers = _auth_header(config)
    url = f"{config['api_url']}/wp-json/wp/v2/tags"
    tag_ids = []

    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        # 검색
        resp = requests.get(url, headers=headers, params={"search": name}, timeout=10)
        found = False
        if resp.status_code == 200:
            for tag in resp.json():
                if tag["name"].lower() == name.lower():
                    tag_ids.append(tag["id"])
                    found = True
                    break
        if not found:
            # 생성
            resp = requests.post(url, headers=headers, json={"name": name}, timeout=10)
            if resp.status_code in (200, 201):
                tag_ids.append(resp.json()["id"])
            else:
                logger.warning(f"태그 생성 실패: {name} — {resp.text[:200]}")

    return tag_ids


def upload_image(image_bytes: bytes, filename: str = "feature.png") -> dict:
    """WordPress에 이미지 업로드. 반환: {"id": int, "url": str} 또는 {"error": str}"""
    try:
        config = _get_config()
    except ValueError as e:
        return {"error": str(e)}

    url = f"{config['api_url']}/wp-json/wp/v2/media"
    headers = _auth_header(config)
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    headers["Content-Type"] = "image/png"

    try:
        resp = requests.post(url, headers=headers, data=image_bytes, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return {"id": data["id"], "url": data.get("source_url", "")}
    except Exception as e:
        return {"error": f"이미지 업로드 실패: {e}"}


def publish_post(
    blog: dict,
    feature_image_id: int | None = None,
    scheduled_at: str | None = None,
) -> dict:
    """
    WordPress에 포스트 발행.

    Args:
        blog: AI가 생성한 blog dict (title, meta_title, meta_description, content, tags, category, schema_faq)
        feature_image_id: 업로드된 이미지의 WordPress media ID (None이면 이미지 없이 발행)
        scheduled_at: ISO 8601 예약 시간 (None이면 즉시 발행)

    반환: {"url": str, "id": int, "status": str} 또는 {"error": str}
    """
    try:
        config = _get_config()
    except ValueError as e:
        return {"error": str(e)}

    headers = _auth_header(config)

    # 카테고리 처리
    category_ids = []
    category_name = blog.get("category", "")
    if category_name:
        cat_id = _get_or_create_category(config, category_name)
        if cat_id:
            category_ids.append(cat_id)

    # 태그 처리
    tag_names = blog.get("tags", [])
    tag_ids = _get_or_create_tags(config, tag_names) if tag_names else []

    # FAQ Schema JSON-LD를 본문 끝에 삽입
    content = blog.get("content", "")
    schema_faq = blog.get("schema_faq", [])
    if schema_faq:
        jsonld = json.dumps({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": faq.get("question", ""),
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": faq.get("answer", ""),
                    },
                }
                for faq in schema_faq
            ],
        }, ensure_ascii=False)
        content += f'\n\n<script type="application/ld+json">\n{jsonld}\n</script>'

    # 포스트 데이터
    post = {
        "title": blog.get("title", ""),
        "content": content,
        "status": "publish",
        "slug": blog.get("slug", ""),
        "categories": category_ids,
        "tags": tag_ids,
        "meta": {
            "rank_math_title": blog.get("meta_title", ""),
            "rank_math_description": blog.get("meta_description", ""),
            "rank_math_focus_keyword": blog.get("focus_keyword", ""),
        },
    }

    # 피처 이미지
    if feature_image_id:
        post["featured_media"] = feature_image_id

    # 예약 발행
    if scheduled_at:
        post["status"] = "future"
        post["date"] = scheduled_at

    try:
        resp = requests.post(
            f"{config['api_url']}/wp-json/wp/v2/posts",
            headers=headers,
            json=post,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()

        return {
            "id": result["id"],
            "url": result.get("link", ""),
            "status": result.get("status", ""),
        }
    except requests.exceptions.HTTPError as e:
        error_body = ""
        try:
            error_body = e.response.json()
        except Exception:
            error_body = e.response.text[:500]
        return {"error": f"WordPress API 오류: {e}\n{error_body}"}
    except Exception as e:
        return {"error": f"발행 실패: {e}"}


def fetch_published_posts(per_page: int = 50) -> list[dict]:
    """
    WordPress에서 기존 발행 글 목록 조회 (내부 링크 삽입용).
    반환: [{"slug": str, "title": str, "categories": [...]}]
    """
    try:
        config = _get_config()
    except ValueError:
        return []

    headers = _auth_header(config)
    url = f"{config['api_url']}/wp-json/wp/v2/posts"

    try:
        resp = requests.get(
            url, headers=headers,
            params={"per_page": per_page, "status": "publish", "_fields": "id,slug,title,categories,tags"},
            timeout=15,
        )
        resp.raise_for_status()
        posts = resp.json()
        return [
            {
                "slug": p["slug"],
                "title": p["title"].get("rendered", ""),
            }
            for p in posts
        ]
    except Exception as e:
        logger.warning(f"WordPress 글 목록 조회 실패: {e}")
        return []


def test_connection() -> dict:
    """WordPress API 연결 테스트."""
    try:
        config = _get_config()
    except ValueError as e:
        return {"error": str(e)}

    try:
        # 인증 확인: /wp-json/wp/v2/users/me
        headers = _auth_header(config)
        resp = requests.get(
            f"{config['api_url']}/wp-json/wp/v2/users/me",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        user = resp.json()
        return {
            "title": user.get("name", ""),
            "url": config["api_url"],
            "connected": True,
        }
    except Exception as e:
        return {"error": f"WordPress 연결 실패: {e}"}


def estimate_pixel_width(text: str) -> int:
    """
    Google 검색 결과 SERP에서의 텍스트 픽셀 폭 추정.
    Rank Math 스니펫 편집기와 유사한 기준 사용.
    - 한글/CJK: 약 11px
    - 영문 대문자: 약 9px
    - 영문 소문자: 약 7px
    - 숫자: 약 7px
    - 공백: 약 4px
    - 기타 기호: 약 7px
    """
    total = 0
    for ch in text:
        if '\uac00' <= ch <= '\ud7a3' or '\u4e00' <= ch <= '\u9fff':
            total += 11
        elif ch.isupper():
            total += 9
        elif ch.islower():
            total += 7
        elif ch.isdigit():
            total += 7
        elif ch == ' ':
            total += 4
        else:
            total += 7
    return total
