"""
ai_processor.py — Claude API 호출, 3포맷 동시 생성
"""
import json
import os
from pathlib import Path
import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 8000
CHUNK_SIZE = 8000  # 자막 청크 단위 (자)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# tool_use로 JSON 구조를 강제 (파싱 실패 완전 방지)
CONTENT_TOOL = {
    "name": "publish_content",
    "description": "생성된 3가지 콘텐츠(SMS, 블로그, 인스타그램)를 구조화된 형식으로 반환합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sms": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body":  {"type": "string"},
                    "byte_count": {"type": "integer"}
                },
                "required": ["title", "body", "byte_count"]
            },
            "blog": {
                "type": "object",
                "properties": {
                    "slug":             {"type": "string", "description": "URL 슬러그. 핵심 단어만 영문 소문자와 하이픈으로 간결하게 조합. 예: corporate-tax-savings"},
                    "title":            {"type": "string", "description": "블로그 포스트 제목. 60자 이내, 핵심 키워드 앞쪽 배치."},
                    "meta_title":       {"type": "string", "description": "검색엔진에 표시되는 메타 타이틀. 60자(characters) 이내. title과 다르게 SEO에 최적화된 형태로 작성."},
                    "excerpt":          {"type": "string", "description": "Ghost 블로그 목록에 표시되는 요약문. 2~3문장, 독자의 클릭을 유도하는 문장으로 작성."},
                    "meta_description": {"type": "string", "description": "검색 결과에 표시되는 설명. 145자(characters) 이내."},
                    "focus_keyword":    {"type": "string"},
                    "schema_faq": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string"},
                                "answer":   {"type": "string"}
                            },
                            "required": ["question", "answer"]
                        }
                    },
                    "content": {"type": "string"},
                    "tags":    {"type": "array", "items": {"type": "string"}}
                },
                "required": ["slug", "title", "meta_title", "excerpt", "meta_description", "focus_keyword", "schema_faq", "content", "tags"]
            },
            "instagram": {
                "type": "object",
                "properties": {
                    "caption":      {"type": "string"},
                    "hashtags":     {"type": "array", "items": {"type": "string"}},
                    "image_prompt": {"type": "string"}
                },
                "required": ["caption", "hashtags", "image_prompt"]
            }
        },
        "required": ["sms", "blog", "instagram"]
    }
}


def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    return path.read_text(encoding='utf-8')


def _get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
    return anthropic.Anthropic(api_key=api_key)


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """텍스트를 chunk_size 단위로 분할"""
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def _summarize_chunk(client: anthropic.Anthropic, chunk: str, chunk_idx: int, total: int) -> str:
    """긴 자막 청크를 핵심 내용으로 요약"""
    system = _load_prompt("system_prompt.txt")
    prompt = (
        f"아래는 유튜브 영상 자막의 {chunk_idx + 1}/{total} 부분입니다. "
        "핵심 내용을 500자 이내로 요약해주세요.\n\n"
        "주의사항:\n"
        "- 자막에 없는 수치(세율, 금액, 기간)는 절대 추가하지 말 것\n"
        "- 법령 조항 번호는 자막에 명확히 나온 것만 포함할 것\n"
        "- 가상 사례를 만들지 말 것\n\n"
        f"{chunk}"
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _build_tool(formats: list[str]) -> dict:
    """선택된 포맷만 포함하는 tool 스키마 생성"""
    props = {}
    if 'sms' in formats:
        props['sms'] = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body":  {"type": "string"},
                "byte_count": {"type": "integer"}
            },
            "required": ["title", "body", "byte_count"]
        }
    if 'blog' in formats:
        props['blog'] = CONTENT_TOOL['input_schema']['properties']['blog']
    if 'instagram' in formats:
        props['instagram'] = CONTENT_TOOL['input_schema']['properties']['instagram']

    tool = {
        "name": "publish_content",
        "description": f"생성된 콘텐츠({', '.join(formats)})를 구조화된 형식으로 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": formats
        }
    }
    return tool


def generate_content(transcript: str, formats: list[str] | None = None, published_posts: list[dict] | None = None) -> dict:
    """
    자막으로 선택된 포맷의 콘텐츠 생성
    formats: 생성할 포맷 목록 ['sms', 'blog', 'instagram'] (None이면 전체)
    반환: { 'sms': {...}, 'blog': {...}, 'instagram': {...} } (선택된 것만)
    에러 시: { 'error': str }
    """
    if formats is None:
        formats = ['sms', 'blog', 'instagram']

    client = _get_client()
    system = _load_prompt("system_prompt.txt")
    format_prompt = _load_prompt("content_format.txt")

    # 긴 자막 처리: 청크 분할 → 요약 → 재통합
    if len(transcript) > CHUNK_SIZE:
        chunks = _chunk_text(transcript)
        summaries = []
        for i, chunk in enumerate(chunks):
            summary = _summarize_chunk(client, chunk, i, len(chunks))
            summaries.append(summary)
        combined = "\n\n".join(summaries)
        if len(combined) > 6000:
            combined = combined[:6000] + "\n\n[이하 생략 — 핵심 내용 위주로 생성]"
        user_content = format_prompt + combined
    else:
        user_content = format_prompt + transcript

    # 선택된 포맷 안내를 프롬프트에 추가
    format_names = {'sms': 'SMS/LMS 문자', 'blog': '블로그', 'instagram': '인스타그램'}
    selected_label = ', '.join(format_names[f] for f in formats)
    user_content = f"[생성할 콘텐츠: {selected_label}]\n\n" + user_content

    # 기존 발행 글 목록 → 내부 링크 삽입용
    if published_posts and 'blog' in formats:
        links = "\n".join(
            f"- [{p['title']}](https://biz-insight.kr/{p['slug']}/) (태그: {p.get('tag', '')})"
            for p in published_posts if p.get('slug')
        )
        if links:
            user_content += (
                "\n\n[기존 발행 글 목록 — 내부 링크 삽입에 활용하세요]\n"
                + links
            )

    tool = _build_tool(formats)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": "publish_content"},
            messages=[{"role": "user", "content": user_content}],
        )

        # tool_use 블록에서 input 추출
        for block in response.content:
            if block.type == "tool_use" and block.name == "publish_content":
                data = block.input
                break
        else:
            return {"error": "AI가 콘텐츠 도구를 호출하지 않았습니다."}

    except Exception as e:
        return {"error": f"API 호출 실패: {e}"}

    # SMS 바이트 카운트 보정
    if "sms" in data and "body" in data["sms"]:
        body = data["sms"]["body"]
        try:
            data["sms"]["byte_count"] = len(body.encode("euc-kr"))
        except Exception:
            data["sms"]["byte_count"] = len(body.encode("utf-8"))

    # blog title / meta_title / meta_description 길이 보정
    if "blog" in data:
        blog = data["blog"]
        title = blog.get("title", "")
        meta_title = blog.get("meta_title", "")
        meta = blog.get("meta_description", "")
        kw = blog.get("focus_keyword", "")

        # title이 60자 미만이면 키워드·연도·부제를 추가해서 늘림
        if len(title) < 60:
            suffixes = [
                f" — 2026년 기준 핵심 정리",
                f" | {kw} 완전 가이드",
                f" — 법인 대표라면 반드시 확인하세요",
            ]
            for s in suffixes:
                if len(title + s) >= 60:
                    title = title + s
                    break
            else:
                title = title + suffixes[0]
            blog["title"] = title

        # meta_title이 60자 초과이면 잘라내기
        if len(meta_title) > 60:
            blog["meta_title"] = meta_title[:60]

        # meta_description이 145자 초과이면 잘라내기
        if len(meta) > 145:
            blog["meta_description"] = meta[:145]

    return data


def refine_blog(existing_blog: dict, user_notes: str) -> dict:
    """
    기존 블로그 본문 + 사용자 메모로 블로그만 보완 재생성.
    자막 재처리 없이 블로그 하나만 생성하므로 비용이 훨씬 저렴.

    Args:
        existing_blog: 기존 blog dict { title, excerpt, content, ... }
        user_notes: 사용자가 추가로 입력한 내용/지시사항

    Returns:
        새 blog dict 또는 { 'error': str }
    """
    client = _get_client()
    system = _load_prompt("system_prompt.txt")

    blog_tool = {
        "name": "update_blog",
        "description": "보완된 블로그 콘텐츠를 구조화된 형식으로 반환합니다.",
        "input_schema": CONTENT_TOOL['input_schema']['properties']['blog'],
    }
    # input_schema는 object 타입이어야 함
    blog_tool['input_schema'] = {
        "type": "object",
        "properties": CONTENT_TOOL['input_schema']['properties']['blog']['properties'],
        "required": CONTENT_TOOL['input_schema']['properties']['blog']['required'],
    }

    # schema_faq를 "Q. 질문\nA. 답변" 형식으로 변환
    faq_list = existing_blog.get('schema_faq') or []
    if faq_list:
        faq_text = "\n".join(
            f"Q. {item.get('question', '')}\nA. {item.get('answer', '')}"
            for item in faq_list
        )
    else:
        faq_text = "없음"

    prompt = f"""아래는 기존에 작성된 블로그 포스트입니다.
사용자가 추가하거나 수정을 원하는 내용을 반영하여 블로그를 다시 작성해주세요.
기존 글의 톤앤매너와 구조는 유지하되, 사용자 메모의 내용을 자연스럽게 녹여주세요.
excerpt와 FAQ도 기존 맥락을 유지하면서 보완해주세요.

--- 기존 블로그 제목 ---
{existing_blog.get('title', '')}

--- 기존 블로그 요약(excerpt) ---
{existing_blog.get('excerpt', '')}

--- 기존 블로그 본문 ---
{existing_blog.get('content', '')}

--- 기존 FAQ (schema_faq) ---
{faq_text}

--- 사용자 추가 메모 ---
{user_notes}
"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=[blog_tool],
            tool_choice={"type": "tool", "name": "update_blog"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "update_blog":
                return block.input
        return {"error": "AI가 블로그 수정 도구를 호출하지 않았습니다."}
    except Exception as e:
        return {"error": f"API 호출 실패: {e}"}


def extract_sms_from_blog(blog: dict) -> dict:
    """블로그 내용에서 API 없이 문자 콘텐츠를 추출 (로컬 텍스트 처리)"""
    import re

    title = blog.get('title', '')
    content = blog.get('content', '')

    # 마크다운 정리
    clean = re.sub(r'^#{1,6}\s+', '', content, flags=re.MULTILINE)
    clean = re.sub(r'\*\*(.+?)\*\*', r'\1', clean)
    clean = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', clean)
    clean = re.sub(r'!\[.*?\]\(.*?\)', '', clean)
    clean = re.sub(r'\|.*?\|', '', clean)
    clean = re.sub(r'---+', '', clean)
    clean = re.sub(r'<script.*?</script>', '', clean, flags=re.DOTALL)

    lines = [l.strip() for l in clean.split('\n') if l.strip()]
    filtered = []
    for line in lines:
        if any(skip in line for skip in ['비즈파트너즈', '010-8977-7768', '자주 묻는 질문', 'Q.', 'biz-insight']):
            continue
        if line.startswith(('-', '·', '•')):
            filtered.append(line.lstrip('-·• '))
        elif len(line) > 10:
            filtered.append(line)

    sms_title = re.sub(r'\s*[—\-|:]\s*.*$', '', title)[:20]

    key_sentences = []
    for s in filtered[:8]:
        if len(s) > 15:
            key_sentences.append(s)
        if len(key_sentences) >= 4:
            break

    headings = re.findall(r'^#{2,3}\s+(.+)', content, re.MULTILINE)
    topic = headings[0] if headings else title

    body_parts = [
        "대표님,\n",
        f"{key_sentences[0]}\n" if key_sentences else "",
        f"[{topic}]",
    ]
    for s in key_sentences[1:]:
        body_parts.append(f"- {s}")

    body_parts.append("\n궁금한 점 있으시면 편하게 연락 주세요.")
    body_parts.append("\n(주)비즈파트너즈 이규원 팀장 드림")

    body = '\n'.join(body_parts)

    try:
        byte_count = len(body.encode('euc-kr'))
    except Exception:
        byte_count = len(body.encode('utf-8'))

    return {
        'title': sms_title,
        'body': body,
        'byte_count': byte_count,
    }


def generate_sms_from_blog(blog: dict) -> dict:
    """블로그 내용을 바탕으로 Claude API로 문자 콘텐츠 생성"""
    client = _get_client()
    system = _load_prompt("system_prompt.txt")
    format_prompt = _load_prompt("content_format.txt")

    sms_tool = _build_tool(['sms'])

    prompt = f"""아래는 이미 작성된 블로그 포스트입니다.
이 블로그 내용을 바탕으로 SMS/LMS 문자 콘텐츠를 작성해주세요.

블로그 제목: {blog.get('title', '')}
블로그 본문:
{blog.get('content', '')}

아래 SMS 작성 규칙을 반드시 따라주세요:
{format_prompt.split('[SMS]')[1].split('[Newsletter/BLOG]')[0] if '[SMS]' in format_prompt else ''}
"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=system,
            tools=[sms_tool],
            tool_choice={"type": "tool", "name": "publish_content"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "publish_content":
                data = block.input
                if "sms" in data and "body" in data["sms"]:
                    body = data["sms"]["body"]
                    try:
                        data["sms"]["byte_count"] = len(body.encode("euc-kr"))
                    except Exception:
                        data["sms"]["byte_count"] = len(body.encode("utf-8"))
                return data.get('sms', data)
        return {"error": "AI가 문자 도구를 호출하지 않았습니다."}
    except Exception as e:
        return {"error": f"API 호출 실패: {e}"}

