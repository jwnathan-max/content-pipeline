"""
기존 발행 글 14건을 published_posts 테이블에 시드하는 스크립트.
DB 연결 후 한 번만 실행하면 됩니다.

사용법: python seed_published_posts.py
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

POSTS = [
    ("overseas-trust-strategy", "2026년 해외 신탁 제도, 법인 대표가 알아야 할 자산 이전 전략", "value-up"),
    ("rental-loan-suspension", "2026년 다주택 임대사업자 대출 연장 중단 — 법인 대표가 알아야 할 3가지 시나리오", "value-up"),
    ("family-transfer-gift-tax", "가족 간 송금 메모 작성법 — 증여세 피하는 3가지 원칙", "value-up"),
    ("tax-audit-checklist-2", "세무조사 때 100% 걸리는 통장 내역 5가지 — 2026년 대표가 꼭 알아야 할 계좌이체 리스크", "finance"),
    ("tax-audit-checklist", "2026년 세무조사 대비법 — 법인 대표가 반드시 챙겨야 할 5가지 항목", "value-up"),
    ("welfare-fund-tax", "사내근로복지기금 세무 처리방법 — 법인 대표가 꼭 알아야 할 7가지", "value-up"),
    ("multi-home-capital-gains", "2026년 다주택자 양도세 중과 완전정리 — 시티주택·좀비주택·로즈주택 구분법", "finance"),
    ("stock-option-grant", "스톡옵션 부여 기준 — 법인 대표가 꼭 알아야 할 5가지 원칙", "value-up"),
    ("two-home-tax-strategy", "2026년 1세대 2주택 절세 전략 — 증여·상속·매매 중 현명한 선택은?", "value-up"),
    ("property-tax-surge", "2026년 공시가격 폭등, 서울 아파트 보유세 40~50% 증가 대비법", "finance"),
    ("legal-inheritance-share", "2026년 법정 상속분 계산 실전 가이드 — 배우자는 자녀보다 1.5배, 실제 배분은?", "value-up"),
    ("family-corporation-setup", "가족법인(특정법인) 설립 완벽 가이드 — 2026년 대표가 꼭 알아야 할 활용법", "value-up"),
    ("business-succession-tax", '"준비된 승계와 준비 안 된 승계, 세금 차이가 수십억입니다" — 50년 기업 대표님께 배운 것', "value-up"),
    ("risk-check-01", '"내 회사 돈 내가 썼는데 횡령이라고?" 대표님 목을 조르는 가지급금의 3가지 함정과 합법적 탈출구', "finance"),
]


def main():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS published_posts (
            id           SERIAL PRIMARY KEY,
            slug         TEXT NOT NULL,
            title        TEXT NOT NULL,
            tag          TEXT,
            published_at TIMESTAMP DEFAULT NOW()
        )
    """)

    inserted = 0
    for slug, title, tag in POSTS:
        cur.execute("SELECT id FROM published_posts WHERE slug = %s", (slug,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO published_posts (slug, title, tag) VALUES (%s, %s, %s)",
                (slug, title, tag),
            )
            inserted += 1
            print(f"  + {slug}")
        else:
            print(f"  = {slug} (이미 존재)")

    conn.commit()
    cur.execute("SELECT count(*) FROM published_posts")
    total = cur.fetchone()[0]
    conn.close()
    print(f"\n완료: {inserted}건 추가, 총 {total}건")


if __name__ == "__main__":
    main()
