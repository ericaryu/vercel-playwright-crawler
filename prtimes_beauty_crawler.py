# -*- coding: utf-8 -*-
import asyncio
import csv
import datetime
import os
import random
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page


class PRTimesBeautyCrawler:
    def __init__(
        self,
        target_url: str,
        headless: bool = True,
        batch_size: int = 5,
    ) -> None:
        self.target_url = target_url
        self.base_url = "https://prtimes.jp"
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        self.output_file = f"prtimes_beauty_{now_str}.csv"
        self.batch_size = batch_size
        self.data_buffer: List[Dict[str, str]] = []
        self.scraped_count = 0
        self.headless = headless
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        self.fieldnames = [
            "수집일시",
            "제목",
            "회사명",
            "회사소개",
            "산업",
            "본사 소재지",
            "전화번호",
            "대표자 이름",
            "상장",
            "자본금",
            "설립",
            "URL",
            "X",
            "Facebook",
            "YouTube",
            "Instagram",
            "LinkedIn",
        ]

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _is_today_relative_time(text: str) -> bool:
        if not text:
            return False
        text = text.strip()
        return bool(re.search(r"\d+\s*(분\s*전|시간\s*전|分前|時間前)", text))

    @staticmethod
    def _null_value() -> str:
        return "Null"

    def _default_company_info(self) -> Dict[str, str]:
        return {
            "산업": self._null_value(),
            "본사 소재지": self._null_value(),
            "전화번호": self._null_value(),
            "대표자 이름": self._null_value(),
            "상장": self._null_value(),
            "자본금": self._null_value(),
            "설립": self._null_value(),
            "URL": self._null_value(),
            "X": self._null_value(),
            "Facebook": self._null_value(),
            "YouTube": self._null_value(),
            "Instagram": self._null_value(),
            "LinkedIn": self._null_value(),
        }

    def _random_wait(self, start: float = 1.2, end: float = 2.8) -> float:
        return random.uniform(start, end)

    async def _sleep_random(self, start: float = 1.2, end: float = 2.8) -> None:
        await asyncio.sleep(self._random_wait(start, end))

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
        while True:
            time_elements = await page.query_selector_all("time.time, time")
            if not time_elements:
                break

            last_time_text = self._null_value()
            try:
                last_time_text = await time_elements[-1].inner_text()
            except Exception:
                pass

            if not self._is_today_relative_time(last_time_text):
                break

            load_more_btn = await page.query_selector(
                ".link-more, .btn-more, a:has-text('もっと見る'), button:has-text('もっと見る')"
            )
            if not load_more_btn:
                break

            prev_count = len(
                await page.query_selector_all(".list-press-release .item-main, .item-main")
            )
            wait_time = self._random_wait(1.5, 3.5)
            print(f"다음 페이지 로딩 중... ({wait_time:.2f}초 대기)")
            await asyncio.sleep(wait_time)
            await load_more_btn.click()

            try:
                await page.wait_for_function(
                    """(prev) => {
                        const items = document.querySelectorAll(
                          '.list-press-release .item-main, .item-main'
                        );
                        return items.length > prev;
                    }""",
                    prev_count,
                    timeout=10000,
                )
            except Exception:
                await page.wait_for_timeout(1200)

    async def _collect_today_articles(self, page: Page) -> List[Dict[str, str]]:
        items = await page.query_selector_all(".list-press-release .item-main, .item-main")
        results: List[Dict[str, str]] = []
        seen_urls = set()

        for item in items:
            time_tag = await item.query_selector("time.time, time")
            time_text = ""
            if time_tag:
                try:
                    time_text = await time_tag.inner_text()
                except Exception:
                    time_text = ""

            if not self._is_today_relative_time(time_text):
                continue

            title_elem = await item.query_selector("a.link-title, .link-title")
            company_elem = await item.query_selector("a.link-company, .link-company")

            if not title_elem or not company_elem:
                continue

            title = self._null_value()
            company_name = self._null_value()
            company_href = None

            try:
                title = self._normalize_text(await title_elem.inner_text())
            except Exception:
                pass

            try:
                company_name = self._normalize_text(await company_elem.inner_text())
            except Exception:
                pass

            try:
                company_href = await company_elem.get_attribute("href")
            except Exception:
                company_href = None

            company_url = urljoin(self.base_url, company_href) if company_href else None
            if company_url and company_url in seen_urls:
                continue
            if company_url:
                seen_urls.add(company_url)

            results.append(
                {
                    "title": title,
                    "company_name": company_name,
                    "company_url": company_url,
                }
            )

        return results

    async def _get_company_info(
        self, page: Page, detail_url: Optional[str]
    ) -> Tuple[Dict[str, str], str]:
        info = self._default_company_info()
        intro_text = self._null_value()

        if not detail_url:
            return info, intro_text

        try:
            await page.goto(detail_url, wait_until="domcontentloaded")
            await self._sleep_random(1.0, 2.0)

            intro_selectors = [
                ".company-description-text",
                ".company-description",
                ".company-profile__description",
                ".company-profile__text",
                ".company-about",
            ]
            for selector in intro_selectors:
                node = await page.query_selector(selector)
                if node:
                    text = await node.inner_text()
                    if text and text.strip():
                        intro_text = self._normalize_text(text)
                        break

            rows = await page.query_selector_all(
                "tr.table-row, table.company-profile-table tr, .company-info-table tr"
            )
            for row in rows:
                th = await row.query_selector("th")
                td = await row.query_selector("td")
                if not th or not td:
                    continue

                label = self._normalize_text(await th.inner_text())
                value = self._normalize_text(await td.inner_text())
                if not value:
                    value = self._null_value()

                if "業種" in label or "産業" in label:
                    info["산업"] = value
                elif "本社所在地" in label or "所在地" in label:
                    info["본사 소재지"] = value
                elif "電話番号" in label or "TEL" in label:
                    info["전화번호"] = value
                elif "代表" in label:
                    info["대표자 이름"] = value
                elif "上場" in label:
                    info["상장"] = value
                elif "資本金" in label:
                    info["자본금"] = value
                elif "設立" in label or "創立" in label:
                    info["설립"] = value
                elif "URL" in label or "Web" in label or "ウェブ" in label:
                    info["URL"] = value

            sns_links = await page.query_selector_all(
                "a[href*='twitter.com'], a[href*='x.com'], "
                "a[href*='facebook.com'], a[href*='youtube.com'], "
                "a[href*='instagram.com'], a[href*='linkedin.com']"
            )
            for link in sns_links:
                href = await link.get_attribute("href")
                if not href:
                    continue
                if "twitter.com" in href or "x.com" in href:
                    info["X"] = href
                elif "facebook.com" in href:
                    info["Facebook"] = href
                elif "youtube.com" in href:
                    info["YouTube"] = href
                elif "instagram.com" in href:
                    info["Instagram"] = href
                elif "linkedin.com" in href:
                    info["LinkedIn"] = href

        except Exception as exc:
            print(f"상세 페이지 오류 ({detail_url}): {exc}")

        return info, intro_text

    async def run(self) -> None:
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
                    f"({item.get('company_name', self._null_value())})"
                )

                info, intro_text = await self._get_company_info(
                    page, item.get("company_url")
                )

                row = {
                    "수집일시": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "제목": item.get("title", self._null_value()),
                    "회사명": item.get("company_name", self._null_value()),
                    "회사소개": intro_text,
                    **info,
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
