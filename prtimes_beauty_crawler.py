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
import asyncio
import csv
import datetime as dt
import os
import random
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError


class PRTimesBeautyCrawler:
    def __init__(
        self,
        target_url: str,
        headless: bool = True,
        batch_size: int = 5,
    ) -> None:
        self.target_url = target_url
        self.base_url = "https://prtimes.jp"
        self.headless = headless
        self.batch_size = batch_size
        self.default_value = "Null"
        self.data_buffer: List[Dict[str, str]] = []
        self.scraped_count = 0

        now_str = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_file = f"prtimes_beauty_{now_str}.csv"

        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }

        self.relative_time_pattern = re.compile(
            r"\d+\s*(\u5206\u524d|\u6642\u9593\u524d|"
            r"\ubd84\s*\uc804|\uc2dc\uac04\s*\uc804)"
        )

        self.article_selector = (
            "article.list-article__item, li.list-article__item, "
            "div.list-article__item, .item-main, .item"
        )
        self.time_selectors = [
            "time.time",
            "time",
            ".time",
            ".list-article__time",
            ".item__time",
        ]
        self.title_selectors = [
            ".link-title",
            ".list-article__title",
            ".list-article__title a",
            ".item__title",
            "a.item__title",
        ]
        self.company_selectors = [
            ".link-company",
            ".list-article__company",
            ".list-article__company a",
            ".item__company",
            "a[href*='/company/']",
            "a[href*='/main/html/company']",
        ]
        self.load_more_selectors = [
            "a.link-more",
            "button.link-more",
            "a:has-text('\u3082\u3063\u3068\u898b\u308b')",
            "button:has-text('\u3082\u3063\u3068\u898b\u308b')",
            "a:has-text('\ub354\ubcf4\uae30')",
            "button:has-text('\ub354\ubcf4\uae30')",
        ]
        self.intro_selectors = [
            ".company-description-text",
            ".company-description",
            ".company-profile__description",
            ".company-profile__text",
            ".pr-company__description",
            ".company-profile",
        ]

        self.label_map = {
            "industry": [
                "\u696d\u7a2e",
                "\u4e8b\u696d\u5185\u5bb9",
                "\u4e8b\u696d",
            ],
            "headquarters": [
                "\u672c\u793e\u6240\u5728\u5730",
                "\u6240\u5728\u5730",
            ],
            "phone": [
                "\u96fb\u8a71\u756a\u53f7",
                "TEL",
                "\u96fb\u8a71",
            ],
            "representative": [
                "\u4ee3\u8868\u8005",
                "\u4ee3\u8868",
                "\u4ee3\u8868\u53d6\u7de0\u5f79",
            ],
            "listing_status": [
                "\u4e0a\u5834",
                "\u4e0a\u5834\u533a\u5206",
            ],
            "capital": [
                "\u8cc7\u672c\u91d1",
            ],
            "established": [
                "\u8a2d\u7acb",
            ],
            "company_website": [
                "URL",
                "\u30db\u30fc\u30e0\u30da\u30fc\u30b8",
                "\u30a6\u30a7\u30d6\u30b5\u30a4\u30c8",
                "Website",
            ],
        }

        self.output_fields = [
            "collected_at",
            "title",
            "company_name",
            "company_intro",
            "industry",
            "headquarters",
            "phone",
            "representative",
            "listing_status",
            "capital",
            "established",
            "company_website",
            "sns_x",
            "sns_facebook",
            "sns_youtube",
            "sns_instagram",
            "sns_linkedin",
        ]

    def _random_wait(self, start: float = 1.2, end: float = 2.8) -> float:
        return random.uniform(start, end)

    def _clean_text(self, text: Optional[str]) -> str:
        if not text:
            return self.default_value
        cleaned = " ".join(text.split())
        return cleaned if cleaned else self.default_value

    def _is_today_relative_time(self, text: str) -> bool:
        if not text:
            return False
        return bool(self.relative_time_pattern.search(text.strip()))

    def _normalize_url(self, href: Optional[str]) -> str:
        if not href:
            return ""
        return urljoin(self.base_url, href)

    def _default_company_info(self) -> Dict[str, str]:
        return {
            "industry": self.default_value,
            "headquarters": self.default_value,
            "phone": self.default_value,
            "representative": self.default_value,
            "listing_status": self.default_value,
            "capital": self.default_value,
            "established": self.default_value,
            "company_website": self.default_value,
            "sns_x": self.default_value,
            "sns_facebook": self.default_value,
            "sns_youtube": self.default_value,
            "sns_instagram": self.default_value,
            "sns_linkedin": self.default_value,
        }

    async def _get_first_text(self, page: Page, selectors: List[str]) -> str:
        for selector in selectors:
            element = await page.query_selector(selector)
            if element:
                text = await element.inner_text()
                text = self._clean_text(text)
                if text != self.default_value:
                    return text
        return self.default_value

    async def _extract_time_text(self, item) -> str:
        for selector in self.time_selectors:
            element = await item.query_selector(selector)
            if element:
                text = await element.inner_text()
                text = text.strip()
                if text:
                    return text
        return ""

    async def _extract_title_and_company(self, item) -> Dict[str, str]:
        title_text = ""
        company_text = ""
        company_href = ""

        for selector in self.title_selectors:
            element = await item.query_selector(selector)
            if element:
                title_text = (await element.inner_text()).strip()
                if title_text:
                    break

        for selector in self.company_selectors:
            element = await item.query_selector(selector)
            if element:
                company_text = (await element.inner_text()).strip()
                company_href = await element.get_attribute("href") or ""
                if not company_href:
                    anchor = await element.query_selector("a")
                    if anchor:
                        company_href = await anchor.get_attribute("href") or ""
                if company_text:
                    break

        if not company_href:
            anchor = await item.query_selector("a[href*='/company/'], a[href*='/main/html/company']")
            if anchor:
                company_href = await anchor.get_attribute("href") or ""
                if not company_text:
                    company_text = (await anchor.inner_text()).strip()

        return {
            "title": title_text,
            "company_name": company_text,
            "company_profile_url": self._normalize_url(company_href),
        }

    async def _find_load_more(self, page: Page):
        for selector in self.load_more_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    return element
            except Exception:
                continue
        return None

    async def _load_more_until_old(self, page: Page, max_clicks: int = 30) -> None:
        clicks = 0
        while clicks < max_clicks:
            items = await page.query_selector_all(self.article_selector)
            if not items:
                break

            last_time = await self._extract_time_text(items[-1])
            if last_time and not self._is_today_relative_time(last_time):
                break

            load_more = await self._find_load_more(page)
            if not load_more:
                break

            await asyncio.sleep(self._random_wait(1.5, 3.0))
            try:
                await load_more.click()
            except Exception:
                break

            try:
                await page.wait_for_function(
                    "(selector, prev) => document.querySelectorAll(selector).length > prev",
                    arg=[self.article_selector, len(items)],
                    timeout=10000,
                )
            except PlaywrightTimeoutError:
                pass

            await asyncio.sleep(self._random_wait(1.0, 2.0))
            clicks += 1

    async def _collect_today_articles(self, page: Page) -> List[Dict[str, str]]:
        articles: List[Dict[str, str]] = []
        items = await page.query_selector_all(self.article_selector)
        for item in items:
            time_text = await self._extract_time_text(item)
            if not self._is_today_relative_time(time_text):
                continue
            article = await self._extract_title_and_company(item)
            if not article["title"] or not article["company_name"]:
                continue
            articles.append(article)
        return articles

    def _assign_info(self, info: Dict[str, str], label: str, value: str) -> None:
        if not label or not value or value == self.default_value:
            return
        for key, keywords in self.label_map.items():
            if any(keyword in label for keyword in keywords):
                if info.get(key, self.default_value) == self.default_value:
                    info[key] = value
                return

    async def _extract_value_from_cell(self, cell) -> str:
        link = await cell.query_selector("a[href]")
        if link:
            href = await link.get_attribute("href")
            if href and href.startswith(("http://", "https://")):
                return href
        text = await cell.inner_text()
        return self._clean_text(text)

    async def get_enterprise_info(self, page: Page, detail_url: str) -> Dict[str, str]:
        info = self._default_company_info()
        if not detail_url:
            return info

        try:
            await asyncio.sleep(self._random_wait(1.0, 2.0))
            await page.goto(detail_url, wait_until="domcontentloaded")
            await asyncio.sleep(self._random_wait(1.0, 2.2))

            rows = await page.query_selector_all(
                "table tr, .company-profile__table tr, .company-profile__row"
            )
            for row in rows:
                th = await row.query_selector("th, .company-profile__label")
                td = await row.query_selector("td, .company-profile__value")
                if not th or not td:
                    continue
                label = self._clean_text(await th.inner_text())
                value = await self._extract_value_from_cell(td)
                self._assign_info(info, label, value)

            dls = await page.query_selector_all("dl")
            for dl in dls:
                dts = await dl.query_selector_all("dt")
                dds = await dl.query_selector_all("dd")
                for dt_element, dd_element in zip(dts, dds):
                    label = self._clean_text(await dt_element.inner_text())
                    value = await self._extract_value_from_cell(dd_element)
                    self._assign_info(info, label, value)

            sns_links = await page.query_selector_all("a[href]")
            for link in sns_links:
                href = await link.get_attribute("href")
                if not href:
                    continue
                lower = href.lower()
                if ("x.com" in lower or "twitter.com" in lower) and info["sns_x"] == self.default_value:
                    info["sns_x"] = href
                elif "facebook.com" in lower and info["sns_facebook"] == self.default_value:
                    info["sns_facebook"] = href
                elif "youtube.com" in lower and info["sns_youtube"] == self.default_value:
                    info["sns_youtube"] = href
                elif "instagram.com" in lower and info["sns_instagram"] == self.default_value:
                    info["sns_instagram"] = href
                elif "linkedin.com" in lower and info["sns_linkedin"] == self.default_value:
                    info["sns_linkedin"] = href

        except Exception as exc:
            message = (
                "\uc0c1\uc138 \ud398\uc774\uc9c0 \uc624\ub958: "
                f"{detail_url} ({exc})"
            )
            print(message)

        return info

    def save_to_csv(self) -> None:
        if not self.data_buffer:
            return
        file_exists = os.path.isfile(self.output_file)
        with open(self.output_file, "a", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=self.output_fields)
            if not file_exists:
                writer.writeheader()
            writer.writerows(self.data_buffer)
        self.data_buffer = []
        print(
            "\ucd5c\uc2e0\uae4c\uc9c0 "
            f"{self.scraped_count}\uac1c \ub370\uc774\ud130 \uae30\ub85d \uc644\ub8cc"
        )

    async def run(self) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = await browser.new_context(
                user_agent=self.headers["User-Agent"],
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
            )
            page = await context.new_page()

            print(f"Target: {self.target_url}")
            await page.goto(self.target_url, wait_until="domcontentloaded")
            await asyncio.sleep(self._random_wait(1.2, 2.4))

            await self._load_more_until_old(page)
            articles = await self._collect_today_articles(page)
            print(f"Today articles: {len(articles)}")

            for index, item in enumerate(articles, start=1):
                self.scraped_count = index
                print(
                    "\ud604\uc7ac "
                    f"{self.scraped_count}\ubc88\uc9f8 \ub370\uc774\ud130 \uc218\uc9d1 \uc911..."
                )

                company_info = await self.get_enterprise_info(
                    page, item["company_profile_url"]
                )
                company_intro = await self._get_first_text(page, self.intro_selectors)

                record = {
                    "collected_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "title": self._clean_text(item.get("title")),
                    "company_name": self._clean_text(item.get("company_name")),
                    "company_intro": company_intro,
                    **company_info,
                }
                self.data_buffer.append(record)

                if len(self.data_buffer) >= self.batch_size:
                    self.save_to_csv()

            self.save_to_csv()
            await browser.close()
            print(f"Done. Output: {self.output_file}")


if __name__ == "__main__":
    crawler = PRTimesBeautyCrawler("https://prtimes.jp/beauty/", headless=True)
    asyncio.run(crawler.run())
