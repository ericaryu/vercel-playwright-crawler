import { chromium } from 'playwright';

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const { keyword } = req.body;
  if (!keyword) {
    return res.status(400).json({ error: 'Missing keyword in request body' });
  }

  const searchUrl = `https://www.jobkorea.co.kr/Search/?stext=${encodeURIComponent(keyword)}`;

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const page = await browser.newPage();

  try {
    await page.goto(searchUrl, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('[data-sentry-component="CardCommon"]', { timeout: 10000 });

    const results = await page.$$eval('[data-sentry-component="CardCommon"]', (cards) => {
      return cards.slice(0, 5).map((card) => {
        const titleElement = card.querySelector('span[class*="Typography_variant_size18"]');
        const title = titleElement?.innerText.trim();

        const linkElement = card.querySelector('a[href*="Recruit/GI_Read"]');
        const link = linkElement?.href;

        const company = card.querySelector('a[class*="company"]')?.innerText.trim() || null;

        return { title, link, company };
      });
    });

    res.status(200).json({ keyword, count: results.length, results });
  } catch (err) {
    res.status(500).json({ error: 'Failed to crawl', details: err.message });
  } finally {
    await browser.close();
  }
}
