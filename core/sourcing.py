"""
sourcing.py - 상품 소싱 및 키워드 추출 모듈
쿠팡 베스트셀러/골드박스 크롤링 → GPT-4o Vision으로 영문 키워드 추출
"""

import json
import base64
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

import requests
from openai import OpenAI

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    OPENAI_API_KEY, COUPANG_GOLDBOX_URL, COUPANG_RANKING_URL,
    VISION_PROMPT, MAX_PRODUCTS_PER_RUN, DOWNLOADS_DIR,
    ALIEXPRESS_EXCLUDE_KEYWORDS,
    DATA_DIR, BRAND_MODEL_ENRICH, BRAND_MODEL_CACHE_DAYS, GENERIC_KEYWORDS,
)
from core.database import insert_product, update_product_code
from core.aliexpress_api import AliExpressClient

logger = logging.getLogger(__name__)


class ProductSourcer:
    """쿠팡 상품 소싱 클래스"""

    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
        self.aliexpress = AliExpressClient()
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        self._brand_model_cache_path = DATA_DIR / "brand_model_cache.json"

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
    # AliExpress API 검색
    # ──────────────────────────────────────────

    def search_aliexpress_products(self, keyword: str,
                                   max_items: int = None) -> list[dict]:
        """
        AliExpress API로 상품 검색
        Returns: [{"name": str, "image_url": str, "price": str, "link": str,
                   "affiliate_link": str, "source": "aliexpress"}, ...]
        """
        max_items = max_items or MAX_PRODUCTS_PER_RUN
        if not keyword:
            logger.error("AliExpress 검색 키워드가 비어 있습니다.")
            return []

        if not self.aliexpress.is_ready():
            logger.error("AliExpress API가 설정되지 않아 검색할 수 없습니다.")
            return []

        products = self.aliexpress.search_products(keyword, max_items=max_items)
        if not products:
            return []

        filtered = []
        for p in products:
            name = p.get("name", "")
            if self._is_excluded_name(name):
                continue
            filtered.append(p)

        if len(filtered) != len(products):
            logger.info(
                f"의류/제외 키워드 필터링: {len(products)}개 → {len(filtered)}개"
            )
        return filtered

    @staticmethod
    def _is_excluded_name(name: str) -> bool:
        if not name:
            return False
        lowered = name.lower()
        for kw in ALIEXPRESS_EXCLUDE_KEYWORDS:
            if kw and kw.lower() in lowered:
                return True
        return False

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

            if not self.client:
                raise RuntimeError("OpenAI API 키 미설정")
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
            if not self.client:
                raise RuntimeError("OpenAI API 키 미설정")
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

    def expand_brand_model_keywords(self, keyword: str,
                                    max_items: int = 6) -> list[str]:
        """
        일반 키워드를 브랜드/모델명 포함 검색어로 확장
        """
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        if not BRAND_MODEL_ENRICH:
            return [keyword]
        if not self._is_generic_keyword(keyword):
            return [keyword]

        cached = self._load_brand_model_cache(keyword)
        if cached:
            return cached[:max_items]

        if not self.client:
            return [keyword]

        prompt = (
            "다음 키워드를 '브랜드+모델명' 형태의 검색어로 5~8개 만들어줘.\n"
            "조건:\n"
            "- 각 항목은 브랜드명 + 모델명(또는 라인명)이 포함되어야 함\n"
            "- 너무 일반적인 단어는 제외\n"
            "- 한국어/영어 혼용 가능\n"
            "- JSON 배열로만 출력\n\n"
            f"키워드: {keyword}"
        )
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.7
            )
            content = response.choices[0].message.content.strip()
            items = []
            try:
                items = json.loads(content)
            except Exception:
                # 라인 분리 폴백
                items = [line.strip("-• ").strip() for line in content.split("\n") if line.strip()]

            # 정리
            cleaned = []
            seen = set()
            for it in items:
                if not it:
                    continue
                text = str(it).strip()
                if len(text) < 3:
                    continue
                key = text.lower()
                if key in seen:
                    continue
                seen.add(key)
                cleaned.append(text)

            if cleaned:
                self._save_brand_model_cache(keyword, cleaned)
                return cleaned[:max_items]
        except Exception as e:
            logger.warning(f"브랜드/모델 키워드 확장 실패: {e}")

        return [keyword]

    @staticmethod
    def _is_generic_keyword(keyword: str) -> bool:
        key = (keyword or "").strip().lower()
        return key in [k.lower() for k in GENERIC_KEYWORDS if k]

    def _load_brand_model_cache(self, keyword: str) -> list[str]:
        try:
            if not self._brand_model_cache_path.exists():
                return []
            with open(self._brand_model_cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            item = data.get(keyword)
            if not item:
                return []
            ts = item.get("ts")
            if ts:
                dt = datetime.fromisoformat(ts)
                if datetime.now() - dt > timedelta(days=BRAND_MODEL_CACHE_DAYS):
                    return []
            return item.get("items", []) or []
        except Exception:
            return []

    def _save_brand_model_cache(self, keyword: str, items: list[str]):
        try:
            data = {}
            if self._brand_model_cache_path.exists():
                with open(self._brand_model_cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data[keyword] = {
                "ts": datetime.now().isoformat(),
                "items": items
            }
            self._brand_model_cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._brand_model_cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"브랜드/모델 캐시 저장 실패: {e}")

    # ──────────────────────────────────────────
    # 통합 파이프라인
    # ──────────────────────────────────────────

    async def run_sourcing_pipeline(self, url: str = None,
                                    source: str = "coupang",
                                    keyword: str = None,
                                    max_items: int = None) -> list[dict]:
        """
        전체 소싱 파이프라인 실행
        1. 쿠팡 크롤링
        2. 각 상품 Vision/텍스트 분석
        3. DB 저장
        Returns: 분석 완료된 상품 리스트
        """
        logger.info("=== 상품 소싱 파이프라인 시작 ===")

        max_items = max_items or MAX_PRODUCTS_PER_RUN

        # 1. 소싱
        if source == "aliexpress":
            if not keyword:
                logger.error("AliExpress 소싱 키워드가 비어 있습니다.")
                return []
            raw_products = self.search_aliexpress_products(
                keyword=keyword,
                max_items=max_items
            )
        else:
            raw_products = await self.crawl_coupang_products(url, max_items=max_items)

        # 2. 분석 및 저장
        analyzed_products = []
        for product in raw_products:
            # Vision 분석 시도 → 실패 시 텍스트 분석
            if source == "aliexpress":
                analysis = self.analyze_product_by_name(product.get("name", ""))
            else:
                if product.get("image_url"):
                    analysis = self.analyze_product_image(
                        image_url=product["image_url"]
                    )
                else:
                    analysis = self.analyze_product_by_name(product["name"])

            # 결과 병합
            product["name_en"] = analysis.get("product_name", "")
            product["keywords"] = analysis.get("keywords", [])
            product["cta_keyword"] = self.infer_cta_keyword(
                product.get("name_en") or product.get("name", ""),
                product.get("keywords", [])
            )

            # DB 저장
            product_id = insert_product(
                name=product["name"],
                name_en=product["name_en"],
                keywords=product["keywords"],
                image_url=product.get("image_url", ""),
                price=product.get("price", ""),
                affiliate_link=product.get("affiliate_link", product.get("link", "")),
                source=product.get("source", source),
                cta_keyword=product.get("cta_keyword", "")
            )
            product_code = self._generate_product_code(
                product_id,
                product.get("source", source)
            )
            update_product_code(product_id, product_code)

            product["id"] = product_id
            product["product_code"] = product_code
            analyzed_products.append(product)

            logger.info(
                f"상품 저장 완료 [ID:{product_id}]: "
                f"{product['name'][:20]}... → {product['name_en']}"
            )

        logger.info(f"=== 소싱 완료: {len(analyzed_products)}개 상품 ===")
        return analyzed_products

    @staticmethod
    def _generate_product_code(product_id: int, source: str) -> str:
        prefix = "AE" if (source or "").lower().startswith("ali") else "CP"
        return f"{prefix}-{product_id:06d}"

    @staticmethod
    def infer_cta_keyword(product_name: str, keywords: list[str] | None = None) -> str:
        text = f"{product_name} {' '.join(keywords or [])}".lower()
        mapping = [
            (["organizer", "storage", "box", "rack", "shelf", "drawer", "cabinet", "bin", "정리", "수납"], "수납"),
            (["kitchen", "sink", "dish", "spice", "pan", "pot", "주방", "싱크", "설거지"], "주방"),
            (["bath", "shower", "toilet", "soap", "towel", "욕실", "샤워"], "욕실"),
            (["clean", "mop", "brush", "sponge", "dust", "lint", "청소", "먼지"], "청소"),
            (["cable", "wire", "charger", "power", "케이블", "충전"], "케이블"),
            (["water", "leak", "drain", "물", "물튐"], "물튐"),
            (["heat", "warm", "insulation", "보온", "단열"], "보온"),
            (["travel", "lunch", "bottle", "보관"], "보관"),
            (["space", "fold", "compact", "small", "좁은", "공간", "접이"], "공간"),
        ]
        for keys, label in mapping:
            for k in keys:
                if k in text:
                    return label
        return "정보"


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
