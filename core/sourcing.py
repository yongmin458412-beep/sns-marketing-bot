"""
sourcing.py - 상품 소싱 및 키워드 추출 모듈
쿠팡 베스트셀러/골드박스 크롤링 → GPT-4o Vision으로 영문 키워드 추출
"""

import json
import base64
import logging
import asyncio
from typing import Optional
from pathlib import Path

import requests
from openai import OpenAI

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    OPENAI_API_KEY, COUPANG_GOLDBOX_URL, COUPANG_RANKING_URL,
    VISION_PROMPT, MAX_PRODUCTS_PER_RUN, DOWNLOADS_DIR
)
from core.database import insert_product

logger = logging.getLogger(__name__)


class ProductSourcer:
    """쿠팡 상품 소싱 클래스"""

    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    # ──────────────────────────────────────────
    # 크롤링 (Playwright 기반)
    # ──────────────────────────────────────────

    async def crawl_coupang_products(self, url: str = None,
                                     max_items: int = None) -> list[dict]:
        """
        Playwright를 사용하여 쿠팡 페이지에서 상품 정보 크롤링
        Returns: [{"name": str, "image_url": str, "price": str, "link": str}, ...]
        """
        from playwright.async_api import async_playwright

        url = url or COUPANG_GOLDBOX_URL
        max_items = max_items or MAX_PRODUCTS_PER_RUN
        products = []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=self.headers["User-Agent"],
                    locale="ko-KR"
                )
                page = await context.new_page()

                logger.info(f"쿠팡 페이지 접속 중: {url}")
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(3000)

                # 골드박스 상품 셀렉터
                selectors = [
                    "li.baby-product-wrap",          # 골드박스
                    "li.search-product",              # 검색 결과
                    "div.product-item",               # 카테고리
                ]

                items = []
                for selector in selectors:
                    items = await page.query_selector_all(selector)
                    if items:
                        break

                if not items:
                    # 범용 셀렉터 시도
                    items = await page.query_selector_all("a[href*='/vp/products/']")

                for item in items[:max_items]:
                    try:
                        product = await self._extract_product_info(item, page)
                        if product and product.get("name"):
                            products.append(product)
                            logger.info(f"상품 발견: {product['name'][:30]}...")
                    except Exception as e:
                        logger.warning(f"상품 추출 실패: {e}")
                        continue

                await browser.close()

        except Exception as e:
            logger.error(f"쿠팡 크롤링 실패: {e}")
            # 폴백: 샘플 데이터 사용
            products = self._get_sample_products()

        logger.info(f"총 {len(products)}개 상품 소싱 완료")
        return products

    async def _extract_product_info(self, element, page) -> Optional[dict]:
        """개별 상품 요소에서 정보 추출"""
        product = {}

        # 상품명
        name_el = await element.query_selector(
            "div.name, span.name, div.product-name, .baby-product-link"
        )
        if name_el:
            product["name"] = (await name_el.inner_text()).strip()

        # 이미지 URL
        img_el = await element.query_selector("img")
        if img_el:
            src = await img_el.get_attribute("src")
            if src:
                if src.startswith("//"):
                    src = "https:" + src
                product["image_url"] = src

        # 가격
        price_el = await element.query_selector(
            "strong.price-value, em.sale, span.price-info"
        )
        if price_el:
            product["price"] = (await price_el.inner_text()).strip()

        # 링크
        link_el = await element.query_selector("a[href]")
        if link_el:
            href = await link_el.get_attribute("href")
            if href:
                if href.startswith("/"):
                    href = "https://www.coupang.com" + href
                product["link"] = href

        return product if product.get("name") else None

    def _get_sample_products(self) -> list[dict]:
        """크롤링 실패 시 사용할 샘플 데이터"""
        return [
            {
                "name": "에어팟 프로 2세대",
                "image_url": "https://via.placeholder.com/300x300.png?text=AirPods+Pro+2",
                "price": "299,000원",
                "link": "https://www.coupang.com/sample"
            },
            {
                "name": "다이슨 에어랩 멀티 스타일러",
                "image_url": "https://via.placeholder.com/300x300.png?text=Dyson+Airwrap",
                "price": "599,000원",
                "link": "https://www.coupang.com/sample"
            },
            {
                "name": "스탠리 텀블러 1.18L",
                "image_url": "https://via.placeholder.com/300x300.png?text=Stanley+Tumbler",
                "price": "49,900원",
                "link": "https://www.coupang.com/sample"
            },
        ]

    # ──────────────────────────────────────────
    # GPT-4o Vision 분석
    # ──────────────────────────────────────────

    def analyze_product_image(self, image_url: str = None,
                              image_path: str = None) -> dict:
        """
        GPT-4o Vision으로 상품 이미지 분석 → 영문명 + 키워드 추출
        Returns: {"product_name": str, "keywords": [str, ...]}
        """
        try:
            messages = [{"role": "user", "content": []}]

            # 텍스트 프롬프트
            messages[0]["content"].append({
                "type": "text",
                "text": VISION_PROMPT
            })

            # 이미지 (URL 또는 로컬 파일)
            if image_url:
                messages[0]["content"].append({
                    "type": "image_url",
                    "image_url": {"url": image_url}
                })
            elif image_path:
                with open(image_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                messages[0]["content"].append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                })
            else:
                raise ValueError("image_url 또는 image_path 중 하나를 제공해야 합니다.")

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=500,
                response_format={"type": "json_object"}
            )

            result = json.loads(response.choices[0].message.content)
            logger.info(f"Vision 분석 결과: {result}")
            return result

        except Exception as e:
            logger.error(f"Vision 분석 실패: {e}")
            return {"product_name": "", "keywords": []}

    def analyze_product_by_name(self, product_name: str) -> dict:
        """
        상품명으로 영문명 + 키워드 추출 (이미지 없을 때 폴백)
        """
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": (
                        f"다음 한국어 제품명의 정확한 영문 제품명과 "
                        f"해외 쇼핑몰(AliExpress/Amazon) 검색 키워드를 "
                        f"JSON 형식으로 알려주세요.\n"
                        f"제품명: {product_name}\n\n"
                        f'출력 형식: {{"product_name": "...", "keywords": ["...", "..."]}}'
                    )
                }],
                max_tokens=300,
                response_format={"type": "json_object"}
            )

            result = json.loads(response.choices[0].message.content)
            logger.info(f"텍스트 분석 결과: {result}")
            return result

        except Exception as e:
            logger.error(f"텍스트 분석 실패: {e}")
            return {"product_name": product_name, "keywords": [product_name]}

    # ──────────────────────────────────────────
    # 통합 파이프라인
    # ──────────────────────────────────────────

    async def run_sourcing_pipeline(self, url: str = None) -> list[dict]:
        """
        전체 소싱 파이프라인 실행
        1. 쿠팡 크롤링
        2. 각 상품 Vision/텍스트 분석
        3. DB 저장
        Returns: 분석 완료된 상품 리스트
        """
        logger.info("=== 상품 소싱 파이프라인 시작 ===")

        # 1. 크롤링
        raw_products = await self.crawl_coupang_products(url)

        # 2. 분석 및 저장
        analyzed_products = []
        for product in raw_products:
            # Vision 분석 시도 → 실패 시 텍스트 분석
            if product.get("image_url"):
                analysis = self.analyze_product_image(
                    image_url=product["image_url"]
                )
            else:
                analysis = self.analyze_product_by_name(product["name"])

            # 결과 병합
            product["name_en"] = analysis.get("product_name", "")
            product["keywords"] = analysis.get("keywords", [])

            # DB 저장
            product_id = insert_product(
                name=product["name"],
                name_en=product["name_en"],
                keywords=product["keywords"],
                image_url=product.get("image_url", ""),
                price=product.get("price", ""),
                affiliate_link=product.get("link", "")
            )
            product["id"] = product_id
            analyzed_products.append(product)

            logger.info(
                f"상품 저장 완료 [ID:{product_id}]: "
                f"{product['name'][:20]}... → {product['name_en']}"
            )

        logger.info(f"=== 소싱 완료: {len(analyzed_products)}개 상품 ===")
        return analyzed_products


# ──────────────────────────────────────────────
# CLI 실행
# ──────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sourcer = ProductSourcer()
    results = asyncio.run(sourcer.run_sourcing_pipeline())
    for p in results:
        print(f"[{p.get('id')}] {p['name']} → {p.get('name_en', 'N/A')}")
        print(f"    Keywords: {p.get('keywords', [])}")
