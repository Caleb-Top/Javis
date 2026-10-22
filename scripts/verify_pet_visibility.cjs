const { chromium } = require('playwright');

async function main() {
  const browser = await chromium.launch({ headless: true, channel: process.env.AVATAR_BROWSER_CHANNEL || 'msedge' });
  try {
    const page = await browser.newPage();
    await page.goto(process.argv[2] || 'http://127.0.0.1:5181/?avatar-preview=1');
    const result = await page.evaluate(async () => {
      const { createPetSurface } = await import('/src/pet/PetSurface.ts');
      let hidden = true;
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
      const root = document.createElement('div');
      document.body.append(root);
      let visible = null;
      let frames = 0;
      let callbacks;
      const pause = () => new Promise(resolve => setTimeout(resolve, 60));
      const require = (condition, message) => { if (!condition) throw new Error(message); };
      const pet = createPetSurface({
        root, onOpenLive() {}, onOpenSettings() {}, onContextMenu() {},
        avatarLoader: async options => {
          callbacks = options;
          return { setVisible(value) { visible = value; }, setSize() {}, renderOnce() {},
            setExpressionTarget() { frames++; }, diagnostics() { return {}; }, dispose() {} };
        },
      });
      pet.setSkin('javis-lightform');
      await pause();
      require(visible === false, 'hidden mount started rendering');
      let baseline = frames;
      await pause();
      require(frames === baseline, 'controller kept ticking while hidden');
      hidden = false;
      document.dispatchEvent(new Event('visibilitychange'));
      await pause();
      require(visible === true && frames > baseline, 'visible surface failed to resume');
      hidden = true;
      document.dispatchEvent(new Event('visibilitychange'));
      baseline = frames;
      await pause();
      require(visible === false && frames === baseline, 'hiding failed to suspend both loops');
      callbacks.onContextLost();
      hidden = false;
      document.dispatchEvent(new Event('visibilitychange'));
      await pause();
      require(visible === false && frames === baseline, 'visibility bypassed context-loss fallback');
      pet.dispose();
      baseline = frames;
      document.dispatchEvent(new Event('visibilitychange'));
      await pause();
      require(frames === baseline, 'disposed surface resumed');
      root.remove();
      delete document.hidden;
      return { hiddenMount: true, suspended: true, resumed: true, fallbackPreserved: true, disposed: true };
    });
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
