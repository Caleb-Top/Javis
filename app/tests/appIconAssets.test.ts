import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const appRoot = join(import.meta.dirname, "..");
const sourceIcon = join(appRoot, "public", "icons", "javis-orb-thinking-c.png");
const tauriIcons = join(appRoot, "src-tauri", "icons");

function readPngHeader(path: string) {
  const data = readFileSync(path);
  assert.deepEqual(
    [...data.subarray(0, 8)],
    [137, 80, 78, 71, 13, 10, 26, 10],
    `${path} must be a PNG`,
  );

  return {
    width: data.readUInt32BE(16),
    height: data.readUInt32BE(20),
    colorType: data[25],
  };
}

test("the selected C thinking-state icon is a square RGBA source asset", () => {
  assert.equal(existsSync(sourceIcon), true, "missing selected C icon source");
  const header = readPngHeader(sourceIcon);

  assert.equal(header.width, 512);
  assert.equal(header.height, 512);
  assert.ok(
    header.colorType === 4 || header.colorType === 6,
    "the app icon source must retain transparency",
  );
});

test("Tauri icon family is generated from the selected source", () => {
  for (const fileName of [
    "32x32.png",
    "128x128.png",
    "128x128@2x.png",
    "icon.png",
    "icon.ico",
    "icon.icns",
  ]) {
    const path = join(tauriIcons, fileName);
    assert.equal(existsSync(path), true, `missing ${fileName}`);
    assert.ok(readFileSync(path).length > 128, `${fileName} is unexpectedly small`);
  }
});
