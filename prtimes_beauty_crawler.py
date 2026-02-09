# -*- coding: utf-8 -*-
import asyncio
import csv
import os
import random
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin

import ollama
from playwright.async_api import async_playwright, Page


class PRTimesBeautyCrawler:
    def __init__(
        self,
        target_url: str,
        headless: bool = True,
        batch_size: int = 5,
        output_file: str = "prtimes_beauty_today.csv",
        ollama_model: str = "llama3",
    ) -> None:
        # Basic configuration and output naming.
        self.target_url = target_url
        self.base_url = "https://prtimes.jp"
        self.output_file = output_file
        self.batch_size = batch_size
        self.data_buffer: List[Dict[str, str]] = []
        self.scraped_count = 0
        self.headless = headless
        self.ollama_model = ollama_model
        # User-Agent to reduce basic bot detection.
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        # Output schema based on requested fields.
        self.fieldnames = [
            "일어 기사 제목",
            "한국어 번역",
            "링크",
            "게재 일시",
            "관련 회사명(원문)",
            "관련 회사명(한국어발음)",
            "회사 링크",
            "industry",
            "address",
            "phone",
            "ceo",
            "listing",
            "capital",
            "founded",
            "official_url",
            "sns_x",
            "sns_fb",
            "sns_yt",
            "이메일",
        ]

        self.article_selector = (
            "article.item, article.list-article__item, "
            "li.list-article__item, .item-main, .item"
        )
        self.time_selectors = ["time.time", ".time", "time"]
        self.title_selectors = ["h3.title a", "a.link-title", ".link-title a"]
        self.company_selectors = [".company-name", "a.link-company", ".link-company"]
        self.load_more_selectors = [
            ".link-more",
            ".btn-more",
            "a:has-text('もっと見る')",
            "button:has-text('もっと見る')",
        ]

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _clean_text(self, text: Optional[str]) -> str:
        if not text:
            return self._null_value()
        cleaned = self._normalize_text(text)
        return cleaned if cleaned else self._null_value()

    @staticmethod
    def _is_today_relative_time(text: str) -> bool:
        if not text:
            return False
        text = text.strip()
        return bool(re.search(r"\d+\s*(분\s*전|시간\s*전|分前|時間前)", text))

    @staticmethod
    def _null_value() -> str:
        return "NULL"

    def _default_company_info(self) -> Dict[str, str]:
        return {
            "industry": self._null_value(),
            "address": self._null_value(),
            "phone": self._null_value(),
            "ceo": self._null_value(),
            "listing": self._null_value(),
            "capital": self._null_value(),
            "founded": self._null_value(),
            "official_url": self._null_value(),
            "sns_x": self._null_value(),
            "sns_fb": self._null_value(),
            "sns_yt": self._null_value(),
        }

    def _random_wait(self, start: float = 1.2, end: float = 2.8) -> float:
        return random.uniform(start, end)

    async def _sleep_random(self, start: float = 1.2, end: float = 2.8) -> None:
        await asyncio.sleep(self._random_wait(start, end))

    async def _find_first(self, root, selectors: List[str]):
        for selector in selectors:
            element = await root.query_selector(selector)
            if element:
                return element
        return None

    async def _extract_time_text(self, item) -> str:
        for selector in self.time_selectors:
            element = await item.query_selector(selector)
            if element:
                text = await element.inner_text()
                text = self._normalize_text(text) if text else ""
                if text:
                    return text
        return ""

    async def _ask_ollama(self, system_prompt: str, user_content: str) -> str:
        if not user_content or user_content == self._null_value():
            return self._null_value()
        try:
            response = await asyncio.to_thread(
                ollama.chat,
                model=self.ollama_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            return self._clean_text(response["message"]["content"])
        except Exception:
            return self._null_value()

    async def _extract_email(self, page: Page) -> str:
        try:
            body_text = await page.inner_text("body")
        except Exception:
            return self._null_value()

        if not body_text:
            return self._null_value()

        match = re.search(
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            body_text,
        )
        return match.group(0) if match else self._null_value()

    def _append_to_csv(self) -> None:
        if not self.data_buffer:
            return

        file_exists = os.path.isfile(self.output_file)
        with open(self.output_file, "a", newline="", encoding="utf-8-sig") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=self.fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerows(self.data_buffer)

        print(
            f" >>> [기록 완료] 현재까지 총 {self.scraped_count}개 수집됨 "
            f"(파일명: {self.output_file})"
        )
        self.data_buffer = []

    async def _click_load_more_until_old(self, page: Page) -> None:
        # Click "more" while the last item is still "minutes/hours ago".
        while True:
            items = await page.query_selector_all(self.article_selector)
            if not items:
                break

            last_time_text = await self._extract_time_text(items[-1])

            if not self._is_today_relative_time(last_time_text):
                break

            load_more_btn = await self._find_first(page, self.load_more_selectors)
            if not load_more_btn:
                break

            prev_count = len(items)
            wait_time = self._random_wait(1.5, 3.5)
            print(f"다음 페이지 로딩 중... ({wait_time:.2f}초 대기)")
            await asyncio.sleep(wait_time)
            await load_more_btn.click()

            try:
                await page.wait_for_function(
                    """(prev) => {
                        const items = document.querySelectorAll(
                          'article.item, article.list-article__item, li.list-article__item, .item-main, .item'
                        );
                        return items.length > prev;
                    }""",
                    prev_count,
                    timeout=10000,
                )
            except Exception:
                await page.wait_for_timeout(1200)

    async def _collect_today_articles(self, page: Page) -> List[Dict[str, str]]:
        # Collect only articles that show relative time (today).
        items = await page.query_selector_all(self.article_selector)
        results: List[Dict[str, str]] = []
        seen_urls = set()

        for item in items:
            time_text = await self._extract_time_text(item)

            if not self._is_today_relative_time(time_text):
                continue

            title_elem = await self._find_first(item, self.title_selectors)
            company_elem = await self._find_first(item, self.company_selectors)

            if not title_elem:
                continue

            title = self._null_value()
            company_name = self._null_value()
            title_href = None
            company_href = None

            try:
                title = self._clean_text(await title_elem.inner_text())
            except Exception:
                pass
            if title == self._null_value():
                continue

            if company_elem:
                try:
                    company_name = self._clean_text(await company_elem.inner_text())
                except Exception:
                    pass

            try:
                title_href = await title_elem.get_attribute("href")
            except Exception:
                title_href = None

            if company_elem:
                try:
                    company_href = await company_elem.get_attribute("href")
                except Exception:
                    company_href = None

            article_url = urljoin(self.base_url, title_href) if title_href else None
            company_url = (
                urljoin(self.base_url, company_href)
                if company_href
                else self._null_value()
            )
            if not article_url:
                continue
            if article_url and article_url in seen_urls:
                continue
            if article_url:
                seen_urls.add(article_url)

            results.append(
                {
                    "title_jp": title,
                    "link": article_url,
                    "time": time_text,
                    "comp_jp": company_name,
                    "comp_link": company_url,
                }
            )

        return results

    async def _extract_company_data(self, page: Page) -> Dict[str, str]:
        # Extract company info from press release detail page (dl > dt/dd structure).
        data = self._default_company_info()
        dl_elements = await page.query_selector_all("dl.__dl_93dhx_1")
        if not dl_elements:
            dl_elements = await page.query_selector_all("dl")

        for dl in dl_elements:
            dts = await dl.query_selector_all("dt")
            dds = await dl.query_selector_all("dd")

            for dt, dd in zip(dts, dds):
                key = self._clean_text(await dt.inner_text())
                if key == self._null_value():
                    continue

                link_el = await dd.query_selector("a")
                if link_el:
                    val = await link_el.get_attribute("href")
                    if val and val.startswith("/"):
                        val = urljoin(self.base_url, val)
                else:
                    val = await dd.inner_text()
                val = self._clean_text(val.replace("\n", " ") if val else val)

                key_compact = key.strip()

                if "業種" in key_compact:
                    data["industry"] = val
                elif "本社所在地" in key_compact:
                    data["address"] = val
                elif "電話番号" in key_compact:
                    data["phone"] = val
                elif "代表者名" in key_compact or "代表者" in key_compact:
                    data["ceo"] = val
                elif "上場" in key_compact:
                    data["listing"] = val
                elif "資本金" in key_compact:
                    data["capital"] = val
                elif "設立" in key_compact or "創立" in key_compact:
                    data["founded"] = val
                elif "URL" in key_compact:
                    data["official_url"] = val
                elif key_compact.startswith("X"):
                    data["sns_x"] = val
                elif "Facebook" in key_compact:
                    data["sns_fb"] = val
                elif "YouTube" in key_compact:
                    data["sns_yt"] = val

        return data

    async def run(self) -> None:
        # Main workflow: open listing, expand, collect, then crawl detail pages.
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = await browser.new_context(
                user_agent=self.user_agent,
                viewport={"width": 1280, "height": 800},
                locale="ja-JP",
            )
            page = await context.new_page()

            print(f"대상 사이트 접속: {self.target_url}")
            await page.goto(self.target_url, wait_until="domcontentloaded")
            await self._sleep_random(1.0, 2.0)

            await self._click_load_more_until_old(page)
            articles = await self._collect_today_articles(page)

            print(f"수집 대상 기사: {len(articles)}건")

            for idx, item in enumerate(articles, start=1):
                self.scraped_count = idx
                print(
                    f"현재 {self.scraped_count}번째 데이터 수집 중... "
                    f"({item.get('comp_jp', self._null_value())})"
                )

                article_link = item.get("link") or self._null_value()
                if article_link != self._null_value():
                    await page.goto(article_link, wait_until="domcontentloaded")
                    await self._sleep_random(1.0, 2.0)
                    company_info = await self._extract_company_data(page)
                    email = await self._extract_email(page)
                else:
                    company_info = self._default_company_info()
                    email = self._null_value()

                title_ko = await self._ask_ollama(
                    "Translate Japanese beauty news title to Korean. Only output result.",
                    item.get("title_jp", self._null_value()),
                )
                comp_ko = await self._ask_ollama(
                    "Write the Japanese company name in Korean pronunciation. (e.g. 주식회사 XXX).",
                    item.get("comp_jp", self._null_value()),
                )

                row = {
                    "일어 기사 제목": item.get("title_jp", self._null_value()),
                    "한국어 번역": title_ko,
                    "링크": article_link,
                    "게재 일시": item.get("time", self._null_value()),
                    "관련 회사명(원문)": item.get("comp_jp", self._null_value()),
                    "관련 회사명(한국어발음)": comp_ko,
                    "회사 링크": item.get("comp_link", self._null_value()),
                    **company_info,
                    "이메일": email,
                }
                self.data_buffer.append(row)

                if len(self.data_buffer) >= self.batch_size:
                    self._append_to_csv()

                await self._sleep_random(1.2, 2.4)

            self._append_to_csv()
            await browser.close()

            print(f"\n모든 작업이 완료되었습니다. 결과 파일: {self.output_file}")


if __name__ == "__main__":
    crawler = PRTimesBeautyCrawler("https://prtimes.jp/beauty/", headless=True)
    asyncio.run(crawler.run())
