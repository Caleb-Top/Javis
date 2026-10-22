// Run with Playwright and sharp available through NODE_PATH.
const { chromium } = require('playwright');
const sharp = require('sharp');
const fs = require('node:fs/promises');
const path = require('node:path');

async function main() {
  const url = process.argv[2] || 'http://127.0.0.1:5181/?avatar-preview=1';
  const output = path.resolve(process.argv[3] || 'tmp/avatar-preview-evidence');
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.AVATAR_BROWSER_CHANNEL || 'msedge' });
  const results = [];
  try {
    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(url);
      await page.locator('.avatar-preview-stage[data-fallback="none"] canvas').waitFor();
      await page.locator('[data-control="background"]').selectOption('light');
      const canvas = page.locator('.avatar-preview-host canvas');
      const idle = await canvas.screenshot();
      await page.locator('[data-control="state"]').selectOption('speaking');
      await page.waitForTimeout(400);
      const speaking = await canvas.screenshot();
      const first = await sharp(idle).ensureAlpha().raw().toBuffer();
      const second = await sharp(speaking).ensureAlpha().raw().toBuffer();
      let colored = 0;
      let changed = 0;
      for (let i = 0; i < first.length; i += 4) {
        if (Math.max(first[i], first[i + 1], first[i + 2]) - Math.min(first[i], first[i + 1], first[i + 2]) > 25) colored++;
        if (Math.abs(first[i] - second[i]) + Math.abs(first[i + 1] - second[i + 1]) + Math.abs(first[i + 2] - second[i + 2]) > 15) changed++;
      }
      await page.locator('.avatar-preview-interrupt').click();
      const interrupted = await page.locator('[data-control="state"]').inputValue() === 'listening'
        && !(await page.locator('[data-control="speaking"]').isChecked())
        && (await page.locator('.avatar-preview-status').textContent()).startsWith('listening');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      await page.screenshot({ path: path.join(output, `preview-${viewport.width}.png`), fullPage: true });
      results.push({ viewport, colored, changed, interrupted, overflow, errors });
      await page.close();
    }
  } finally {
    await browser.close();
  }
  await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
  if (results.some(result => !result.interrupted || result.overflow || result.errors.length || result.colored < 100 || result.changed < 10)) {
    process.exitCode = 1;
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
