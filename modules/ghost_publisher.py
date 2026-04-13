"""
ghost_publisher.py — Ghost Admin API 발행 모듈
- JWT 인증 (매 요청마다 신규 생성, 만료 5분)
- 이미지 업로드 → feature_image 설정
- 포스트 생성 (즉시 발행 / 예약 발행)
"""
import os
import json
import datetime
import requests
import jwt
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _get_config() -> tuple[str, str]:
    """Ghost API URL과 Admin API Key 반환"""
    api_url = os.getenv("GHOST_API_URL", "").rstrip("/")
    api_key = os.getenv("GHOST_ADMIN_API_KEY", "")
    if not api_url or not api_key:
        raise ValueError("GHOST_API_URL 또는 GHOST_ADMIN_API_KEY가 설정되지 않았습니다.")
    return api_url, api_key


def _generate_token(api_key: str) -> str:
    """Ghost Admin API JWT 토큰 생성 (만료 5분)"""
    key_id, secret = api_key.split(":")
    iat = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    payload = {"iat": iat, "exp": iat + 300, "aud": "/admin/"}
    return jwt.encode(payload, bytes.fromhex(secret), algorithm="HS256", headers={"kid": key_id})


def _headers(api_key: str) -> dict:
    token = _generate_token(api_key)
    return {"Authorization": f"Ghost {token}"}


def upload_image(image_bytes: bytes, filename: str = "feature.png") -> dict:
    """
    Ghost에 이미지 업로드.
    반환: {"url": "https://..."} 또는 {"error": str}
    """
    try:
        api_url, api_key = _get_config()
    except ValueError as e:
        return {"error": str(e)}

    url = f"{api_url}/ghost/api/admin/images/upload/"
    files = {"file": (filename, image_bytes, "image/png")}
    try:
        resp = requests.post(url, headers=_headers(api_key), files=files, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return {"url": data["images"][0]["url"]}
    except Exception as e:
        return {"error": f"이미지 업로드 실패: {e}"}


def _build_mobiledoc(markdown_content: str) -> str:
    """마크다운 콘텐츠를 Ghost mobiledoc JSON 형식으로 변환"""
    card = ["markdown", {"markdown": markdown_content}]
    doc = {
        "version": "0.3.1",
        "markups": [],
        "atoms": [],
        "cards": [card],
        "sections": [[10, 0]],
    }
    return json.dumps(doc, ensure_ascii=False)


def _get_or_create_tag(api_url: str, api_key: str, tag_name: str) -> str | None:
    """태그 slug 반환. 없으면 생성."""
    headers = _headers(api_key)
    # 검색
    resp = requests.get(
        f"{api_url}/ghost/api/admin/tags/slug/{tag_name}/",
        headers=headers, timeout=10,
    )
    if resp.status_code == 200:
        return resp.json()["tags"][0]["id"]
    # 생성
    resp = requests.post(
        f"{api_url}/ghost/api/admin/tags/",
        headers=headers, json={"tags": [{"name": tag_name}]}, timeout=10,
    )
    if resp.status_code in (200, 201):
        return resp.json()["tags"][0]["id"]
    return None


def publish_post(
    blog: dict,
    feature_image_url: str | None = None,
    scheduled_at: str | None = None,
) -> dict:
    """
    Ghost에 포스트 발행.

    Args:
        blog: AI가 생성한 blog dict (title, meta_title, meta_description, excerpt, content, tags, schema_faq)
        feature_image_url: 업로드된 이미지 URL (None이면 이미지 없이 발행)
        scheduled_at: ISO 8601 예약 시간 (None이면 즉시 발행)
                      예: "2026-04-11T09:00:00+09:00"

    반환: {"url": str, "id": str, "status": str} 또는 {"error": str}
    """
    try:
        api_url, api_key = _get_config()
    except ValueError as e:
        return {"error": str(e)}

    # 포스트 데이터 구성
    post = {
        "title": blog.get("title", ""),
        "mobiledoc": _build_mobiledoc(blog.get("content", "")),
        "status": "published",
        "meta_title": blog.get("meta_title", blog.get("title", "")),
        "meta_description": blog.get("meta_description", ""),
        "custom_excerpt": blog.get("excerpt", blog.get("meta_description", "")),
    }

    # 태그 설정
    tags = blog.get("tags", [])
    if tags:
        post["tags"] = [{"name": t} for t in tags]

    # 피처 이미지
    if feature_image_url:
        post["feature_image"] = feature_image_url

    # 예약 발행
    if scheduled_at:
        post["status"] = "scheduled"
        post["published_at"] = scheduled_at

    # FAQ schema (code injection)
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
        post["codeinjection_head"] = f'<script type="application/ld+json">\n{jsonld}\n</script>'

    try:
        resp = requests.post(
            f"{api_url}/ghost/api/admin/posts/",
            headers=_headers(api_key),
            json={"posts": [post]},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()["posts"][0]
        return {
            "id": result["id"],
            "url": result.get("url", ""),
            "status": result.get("status", ""),
            "published_at": result.get("published_at", ""),
        }
    except requests.exceptions.HTTPError as e:
        error_body = ""
        try:
            error_body = e.response.json()
        except Exception:
            error_body = e.response.text[:500]
        return {"error": f"Ghost API 오류: {e}\n{error_body}"}
    except Exception as e:
        return {"error": f"발행 실패: {e}"}


def test_connection() -> dict:
    """Ghost API 연결 테스트. 성공 시 사이트 정보 반환."""
    try:
        api_url, api_key = _get_config()
    except ValueError as e:
        return {"error": str(e)}

    try:
        resp = requests.get(
            f"{api_url}/ghost/api/admin/site/",
            headers=_headers(api_key),
            timeout=10,
        )
        resp.raise_for_status()
        site = resp.json().get("site", {})
        return {"title": site.get("title", ""), "url": site.get("url", ""), "connected": True}
    except Exception as e:
        return {"error": f"Ghost 연결 실패: {e}"}
