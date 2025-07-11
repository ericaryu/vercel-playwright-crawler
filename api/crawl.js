import { chromium } from 'playwright';

export default async function handler(req, res) {
  const { keyword = '화장품 일본' } = req.body;
  const searchUrl = `https://www.jobkorea.co.kr/Search/?stext=${encodeURIComponent(keyword)}`;

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    // User-Agent 우회
    await page.setUserAgent(
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/114 Safari/537.36'
    );

    await page.goto(searchUrl, { timeout: 60000 });
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000); // lazy load 기다림

    const jobList = await page.$$eval('a[href^="/Recruit/GI_Read"]', (links) =>
      links.slice(0, 10).map((a) => ({
        title: a.innerText.trim(),
        link: 'https://www.jobkorea.co.kr' + a.getAttribute('href'),
      }))
    );

    await browser.close();

    return res.status(200).json({
      keyword,
      count: jobList.length,
      results: jobList,
    });
  } catch (err) {
    await browser.close();
    return res.status(500).json({ error: err.message });
  }
}
