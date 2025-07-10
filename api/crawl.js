import { chromium } from 'playwright';

export default async function handler(req, res) {
  const { jobUrl } = req.body;

  if (!jobUrl) {
    return res.status(400).json({ error: 'Missing jobUrl' });
  }

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    await page.goto(jobUrl, { timeout: 30000 });

    const company = await page.locator('h6[class*=JobHeader_className__company]').textContent();
    const jobTitle = await page.locator('h2[class*=JobHeader_className__jobPosition]').textContent();

    const responsibilities = await page.locator('div[data-testid="JobDescription"] >> text=주요업무')
      .locator('xpath=following-sibling::div[1]').textContent();

    const qualifications = await page.locator('div[data-testid="JobDescription"] >> text=자격요건')
      .locator('xpath=following-sibling::div[1]').textContent();

    await browser.close();

    return res.status(200).json({
      company: company?.trim(),
      title: jobTitle?.trim(),
      responsibilities: responsibilities?.trim(),
      qualifications: qualifications?.trim()
    });

  } catch (error) {
    await browser.close();
    return res.status(500).json({ error: error.message });
  }
}
