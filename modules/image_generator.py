"""
image_generator.py — 블로그/인스타 대표 이미지 자동 생성 (Pillow)

레이아웃:
  - 배경: 딥 네이비 그라디언트 + 골드 액센트 라인
  - 상단: 브랜드명 + 카테고리 태그
  - 중앙: 메인 제목 (자동 줄바꿈)
  - 하단: 저자 정보 + 서브카피
  - 두 사이즈: 1080×1080 (인스타), 1200×630 (블로그 피처 이미지)
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── 색상 팔레트 ──────────────────────────────────
COLOR_BG_TOP    = (13, 27, 42)       # 딥 네이비 (#0D1B2A)
COLOR_BG_BTM    = (22, 44, 66)       # 미드 네이비 (#162C42)
COLOR_GOLD      = (201, 168, 76)     # 골드 (#C9A84C)
COLOR_GOLD_LIGHT= (230, 200, 120)    # 밝은 골드
COLOR_WHITE     = (255, 255, 255)
COLOR_WHITE_70  = (255, 255, 255, 178)  # 70% 흰색
COLOR_WHITE_40  = (255, 255, 255, 102)  # 40% 흰색
COLOR_OVERLAY   = (13, 27, 42, 200)  # 반투명 오버레이

# ── 폰트 경로 ────────────────────────────────────
_PROJECT_FONTS = Path(__file__).parent.parent / "fonts"
_SYSTEM_FONTS  = Path("C:/Windows/Fonts")
FONTS_DIR = _PROJECT_FONTS if _PROJECT_FONTS.exists() else _SYSTEM_FONTS
FONT_BOLD    = FONTS_DIR / "malgunbd.ttf"     # 맑은 고딕 Bold
FONT_REGULAR = FONTS_DIR / "malgun.ttf"       # 맑은 고딕 Regular
FONT_LIGHT   = FONTS_DIR / "malgunsl.ttf"     # 맑은 고딕 SemiLight

OUTPUT_DIR = Path(__file__).parent.parent / "generated_images"


def _get_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


def _draw_vertical_gradient(draw: ImageDraw.ImageDraw, width: int, height: int):
    """세로 방향 그라디언트 배경"""
    for y in range(height):
        t = y / height
        r = int(COLOR_BG_TOP[0] + (COLOR_BG_BTM[0] - COLOR_BG_TOP[0]) * t)
        g = int(COLOR_BG_TOP[1] + (COLOR_BG_BTM[1] - COLOR_BG_TOP[1]) * t)
        b = int(COLOR_BG_TOP[2] + (COLOR_BG_BTM[2] - COLOR_BG_TOP[2]) * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b))


def _draw_decorative_lines(draw: ImageDraw.ImageDraw, width: int, height: int):
    """골드 장식 선 — 상단/하단 영역 구분"""
    lw = max(2, height // 300)
    pad = width // 20

    # 상단 골드 라인 (브랜드 바 하단)
    top_y = int(height * 0.14)
    draw.rectangle([pad, top_y, width - pad, top_y + lw], fill=COLOR_GOLD)

    # 하단 골드 라인 (저자 영역 상단)
    btm_y = int(height * 0.82)
    draw.rectangle([pad, btm_y, width - pad, btm_y + lw], fill=COLOR_GOLD)

    # 좌측 강조 바 (제목 왼쪽)
    bar_x = pad
    bar_top = int(height * 0.28)
    bar_btm = int(height * 0.76)
    bar_w = max(4, width // 130)
    draw.rectangle([bar_x, bar_top, bar_x + bar_w, bar_btm], fill=COLOR_GOLD)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """픽셀 단위로 텍스트 줄바꿈 — 공백 기준 단어 단위, 단어 내 한글은 글자 단위"""
    # 공백으로 토큰 분리 (한글 어절 단위)
    tokens = text.split(' ')
    lines = []
    current = ""
    for token in tokens:
        # 토큰 하나가 max_width를 넘으면 글자 단위로 강제 분리
        test = (current + ' ' + token).strip() if current else token
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] > max_width and current:
            lines.append(current)
            current = token
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _draw_title(draw: ImageDraw.ImageDraw, title: str, width: int, height: int,
                font_size: int, text_area_left: int, text_area_right: int,
                center_y: int):
    """제목 텍스트 — 중앙 정렬, 자동 줄바꿈"""
    font = _get_font(FONT_BOLD, font_size)
    max_w = text_area_right - text_area_left
    lines = _wrap_text(title, font, max_w, draw)

    line_height = font_size + int(font_size * 0.3)
    total_h = len(lines) * line_height
    y = center_y - total_h // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        x = text_area_left + (max_w - lw) // 2
        # 그림자 효과
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 120))
        draw.text((x, y), line, font=font, fill=COLOR_WHITE)
        y += line_height

    return y  # 마지막 줄 끝 y 좌표


def generate_card_image(
    title: str,
    category: str = "법인 컨설팅",
    size: str = "instagram",   # "instagram" (1080×1080) | "blog" (1200×630)
    save_path: Path | None = None,
) -> bytes:
    """
    카드형 대표 이미지 생성

    Args:
        title: 블로그/포스트 제목
        category: 상단 카테고리 태그 텍스트
        size: "instagram" 또는 "blog"
        save_path: 파일로 저장할 경로 (None이면 저장 안 함)

    Returns:
        PNG 이미지 바이트
    """
    if size == "instagram":
        W, H = 1080, 1080
        title_font_size = 62
        brand_font_size = 36
        cat_font_size   = 28
        author_font_size= 30
    else:  # blog
        W, H = 1200, 630
        title_font_size = 56
        brand_font_size = 32
        cat_font_size   = 26
        author_font_size= 28

    img = Image.new("RGBA", (W, H), COLOR_BG_TOP)
    draw = ImageDraw.Draw(img)

    # 1. 배경 그라디언트
    _draw_vertical_gradient(draw, W, H)

    # 2. 미묘한 텍스처 — 오른쪽 하단 원형 빛
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = int(W * 0.82), int(H * 0.75)
    for r in range(300, 0, -10):
        alpha = int(18 * (1 - r / 300))
        gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(201, 168, 76, alpha))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    # 3. 장식 선
    _draw_decorative_lines(draw, W, H)

    pad = W // 8  # 가장자리 여백 (상단 브랜드·하단 저자 좌우 여백)
    bar_offset = max(4, W // 130) + pad + W // 40  # 좌측 바 오른쪽 여백

    # 4. 브랜드명 (상단 좌측)
    font_brand = _get_font(FONT_BOLD, brand_font_size)
    brand_y = int(H * 0.05)
    draw.text((pad, brand_y), "비즈 인사이트", font=font_brand, fill=COLOR_GOLD)

    # 5. 카테고리 태그 (상단 우측)
    font_cat = _get_font(FONT_REGULAR, cat_font_size)
    cat_bbox = draw.textbbox((0, 0), category, font=font_cat)
    cat_w = cat_bbox[2] - cat_bbox[0]
    cat_x = W - pad - cat_w - 20
    cat_tag_pad = 10
    draw.rounded_rectangle(
        [cat_x - cat_tag_pad, brand_y - 4,
         cat_x + cat_w + cat_tag_pad, brand_y + cat_bbox[3] + 4],
        radius=6, outline=COLOR_GOLD, width=1,
    )
    draw.text((cat_x, brand_y), category, font=font_cat, fill=COLOR_GOLD_LIGHT)

    # 6. 메인 제목 (중앙)
    center_y = int(H * 0.50)
    _draw_title(draw, title, W, H,
                title_font_size,
                bar_offset, W - pad,
                center_y)

    # 7. 하단 저자 정보
    font_author = _get_font(FONT_BOLD, author_font_size)
    font_sub    = _get_font(FONT_LIGHT, max(author_font_size - 6, 20))
    author_y = int(H * 0.855)
    draw.text((pad, author_y), "이규원 팀장 · (주)비즈파트너즈",
              font=font_author, fill=COLOR_WHITE)
    sub_y = author_y + author_font_size + 6
    draw.text((pad, sub_y), "법인 세무 · 노무 · 재무 전문 컨설팅",
              font=font_sub, fill=(200, 200, 200))

    # 8. 우측 하단 URL (블로그 사이즈만)
    if size == "blog":
        font_url = _get_font(FONT_LIGHT, 22)
        url_text = "biz-insight.kr"
        url_bbox = draw.textbbox((0, 0), url_text, font=font_url)
        draw.text(
            (W - pad - (url_bbox[2] - url_bbox[0]), sub_y),
            url_text, font=font_url, fill=(160, 160, 160),
        )

    # RGBA → RGB 변환 후 PNG 저장
    final = img.convert("RGB")

    if save_path:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        final.save(save_path, "PNG", quality=95)

    buf = BytesIO()
    final.save(buf, "PNG")
    return buf.getvalue()


def generate_both(title: str, category: str = "법인 컨설팅",
                  video_id: str = "") -> dict[str, bytes]:
    """
    인스타(1080×1080)와 블로그(1200×630) 두 사이즈 동시 생성

    Returns:
        {"instagram": <bytes>, "blog": <bytes>}
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{video_id}" if video_id else ""

    insta_bytes = generate_card_image(
        title, category, size="instagram",
        save_path=OUTPUT_DIR / f"insta{suffix}.png",
    )
    blog_bytes = generate_card_image(
        title, category, size="blog",
        save_path=OUTPUT_DIR / f"blog{suffix}.png",
    )
    return {"instagram": insta_bytes, "blog": blog_bytes}
