"""URL → 본문 텍스트 추출 (직접 입력 모드용)."""
from __future__ import annotations

import requests
import trafilatura
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def fetch_url_content(url: str, timeout: int = 15) -> dict:
    """URL에서 제목과 본문 텍스트를 추출.

    반환: {'title': str, 'text': str, 'url': str}
    실패 시: {'error': str}
    """
    # trafilatura의 fetch_url을 우선 사용 (인코딩 자동 감지가 더 정확)
    html = trafilatura.fetch_url(url) or ""
    if not html:
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
            resp.raise_for_status()
        except Exception as e:
            return {"error": f"페이지 접근 실패: {e}"}
        html = resp.content.decode(resp.apparent_encoding or "utf-8", errors="replace")

    # 1차: trafilatura로 본문 추출
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    ) or ""

    # 제목은 og:title → <title> → trafilatura 메타데이터 순
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            title = meta.title

    # 본문 추출 실패 시 fallback: <body>에서 텍스트만 추출
    if not text or len(text.strip()) < 200:
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        body = soup.body
        text = body.get_text("\n", strip=True) if body else soup.get_text("\n", strip=True)

    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())

    if not text or len(text) < 100:
        return {"error": "본문을 추출하지 못했습니다. 페이지가 동적 로딩 방식이거나 접근이 차단되었을 수 있습니다."}

    return {"title": title, "text": text, "url": url}
