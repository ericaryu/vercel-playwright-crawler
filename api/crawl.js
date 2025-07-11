import { chromium } from 'playwright';

export default async function handler(req, res) {
  const { keyword = '화장품 일본' } = req.body;

  const searchUrl = `https://www.jobkorea.co.kr/Search/?stext=${encodeURIComponent(keyword)}`;

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    // 잡코리아는 봇 차단이 있어 User-Agent 우회 추천
    await page.setUserAgent(
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
    );

    await page.goto(searchUrl, { timeout: 60000 });
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000); // lazy load 대기

    // 공고 목록 파싱
    const jobList = await page.$$eval('.list-default > li', (items) =>
      items.slice(0, 5).map((el) => {
        const title = el.querySelector('.title a')?.textContent?.trim() || '';
        const company = el.querySelector('.name')?.textContent?.trim() || '';
        const link = el.querySelector('.title a')?.href || '';
        return { title, company, link };
      })
    );

    await browser.close();

    return res.status(200).json({
      keyword,
      count: jobList.length,
      results: jobList
    });

  } catch (error) {
    await browser.close();
    return res.status(500).json({ error: error.message });
  }
}
