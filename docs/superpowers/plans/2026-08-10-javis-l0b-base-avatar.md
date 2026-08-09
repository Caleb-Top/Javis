# Javis L0-B Base Avatar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Javis 增加精致、轻量、离线、可降级的基础 3D 身体，并通过 `ExpressionIntent v1` 表达注意、倾听、思考、说话、执行、授权、错误与离线状态。

**Architecture:** 在现有 `PetSurface` 外层交互和 `RuntimeStateCoordinator` fallback 上增加动态加载的 Three.js/VRM avatar 管线。身体内部用语义映射器和单一 ExpressionMixer 仲裁姿态、视线、眨眼、表情、嘴部与动作；PerformanceGovernor 和 FallbackCoordinator 保证 3D High → 3D Low → 程序化 3D → 2D → Orb 的可靠降级。

**Tech Stack:** TypeScript、Vite 8、Tauri 2、WebView2、WebGL、Three.js 0.185.1、@pixiv/three-vrm 3.5.5、@types/three 0.185.1、Node test runner、Python 3.11 资产校验、现有 pnpm/npm 构建链。

**Source Design:** `docs/superpowers/specs/2026-08-09-javis-l0b-base-avatar-design.md`。本计划必须覆盖该规格，视觉效果、真实 WebView2 证据、fallback 和无障碍均不能用单张预览图替代。

## Global Constraints

- `G:\Javis` 是唯一正式源码与集成区；实现必须先发生在 `G:\Javis-worktrees`。
- G 盘保存依赖、缓存、构建和中间产物；D 盘只执行真实用户安装与验收。
- 不重新打包 R1 或其他大模型；3D 角色资产不是推理模型附加包。
- 任何依赖或资产联网获取必须在执行时获得用户明确许可，并把缓存放在 `G:\Javis-build-cache`。
- 固定 `three@0.185.1`、`@pixiv/three-vrm@3.5.5`、`@types/three@0.185.1`，不得在实现时自动漂移到更新版本。
- `app/package-lock.json` 是发布版本契约的权威锁文件；已有 `app/pnpm-lock.yaml` 必须同步同一精确版本，不引入第三种锁格式。
- 运行时代码和角色资产不得依赖 CDN、远程脚本、远程字体或远程贴图。
- 保持 Tauri CSP 的 `script-src 'self'`，不得为 3D 放宽为任意远程源。
- 默认角色资产总大小不超过 25 MB，最大单纹理 2048×2048，活跃材质不超过 8，三角面不超过 80,000。
- 3D 失败不能阻止 Live、Code、语音、设置或 Pet fallback 启动。
- 身体只消费 `ExpressionIntent v1`；不得写 LifeState、身份、关系、权限或长期记忆。
- `VoiceCapture.onAudio` 是输入音频，禁止作为 Javis 说话口型源。
- 用户打断必须立即清空嘴部包络和说话手势。
- 2D `javis-anime` 与 CSS `javis-orb` 必须永久保留为 fallback。
- Windows 100%、125%、150%、175% 缩放与 132×158 最小窗口必须纳入 D 盘验收。
- 浏览器截图和自动测试不能替代 D 盘透明 WebView2 真机证据。

---

## Dependency Evidence

- `three@0.185.1`：官方 npm 包页确认的固定版本。
- `@pixiv/three-vrm@3.5.5`：官方 npm 包页确认，peer dependency 为 `three >=0.137`。
- `@types/three@0.185.1`：与 Three.js 精确版本对齐。

依赖获取任务必须同时更新 `package.json`、`package-lock.json` 和现有 `pnpm-lock.yaml`，并通过锁文件一致性测试。若联网许可未获得，Task 1 停在红灯，不手工伪造 lockfile。

## File Structure

### New avatar files

- `app/src/pet/avatar/avatarTypes.ts`：manifest、capability、expression target、performance tier 类型。
- `app/src/pet/avatar/AvatarManifest.ts`：manifest 解析、校验与能力查询。
- `app/src/pet/avatar/AvatarAssetRegistry.ts`：3D/2D/Orb 资产注册与 fallback 解析。
- `app/src/pet/avatar/AvatarSurface.ts`：canvas、renderer、scene、render loop、resize 和 dispose。
- `app/src/pet/avatar/AvatarScene.ts`：camera、lighting、model anchor 和模型加载。
- `app/src/pet/avatar/ProceduralAvatar.ts`：零外部资产 `javis-lightform` 3D 兜底。
- `app/src/pet/avatar/AvatarController.ts`：接收稳定表达意图并把单一 mixer 输出应用到身体能力句柄。
- `app/src/pet/avatar/ExpressionMapper.ts`：ExpressionIntent 到模型无关表达目标。
- `app/src/pet/avatar/ExpressionMixer.ts`：通道权重与可中断过渡仲裁。
- `app/src/pet/avatar/SpeakingEnvelope.ts`：诚实的基础说话包络和停止语义。
- `app/src/pet/avatar/PerformanceGovernor.ts`：性能档位和渲染频率。
- `app/src/pet/avatar/AvatarFallbackCoordinator.ts`：故障降级和手动恢复。
- `app/src/pet/avatar/AvatarPreview.ts`：状态、背景、窗口和风格预览。

### New public assets and validation

- `app/public/pets/javis-3d/avatar-manifest.json`：默认身体清单。
- `app/public/pets/javis-3d/ATTRIBUTION.md`：许可证与来源记录。
- `scripts/validate_avatar_asset.py`：manifest、GLB/VRM header、哈希、大小、纹理和预算校验。

### Modified files

- `app/package.json`
- `app/package-lock.json`
- `app/pnpm-lock.yaml`
- `app/src/pet/petTypes.ts`
- `app/src/pet/PetSkinRegistry.ts`
- `app/src/pet/PetSurface.ts`
- `app/src/styles.css`
- `app/src/main.ts`

### Tests

- `tests/test_avatar_dependency_contract.py`
- `tests/test_avatar_asset_validation.py`
- `app/tests/avatarManifest.test.ts`
- `app/tests/avatarTestFakes.ts`：共享的确定性 DOM、renderer、loader、intent 与 capability fixtures；不访问网络或真实 GPU。
- `app/tests/avatarSurface.test.ts`
- `app/tests/proceduralAvatar.test.ts`
- `app/tests/avatarPetIntegration.test.ts`
- `app/tests/expressionMapper.test.ts`
- `app/tests/expressionMixer.test.ts`
- `app/tests/avatarController.test.ts`
- `app/tests/speakingEnvelope.test.ts`
- `app/tests/avatarPerformance.test.ts`
- `app/tests/avatarFallback.test.ts`
- `app/tests/avatarPreview.test.ts`
- `docs/verification/JAVIS_L0B_D_DRIVE_ACCEPTANCE.md`

---

## Cross-Plan Dependency and Integration Order

1. L0-A Task 1 is the owner of these exact `ExpressionIntent v1` fields: `schema_version`, `revision`, `base_state`, `intensity`, `gaze_target`, `voice_activity`, `transition_ms`, `interrupt`, `source_snapshot_revision`, `generated_at`, `expires_at`, `explanation_code`.
2. L0-B Tasks 1-5 may proceed independently, but Task 6 must wait until L0-A Task 9 has committed `app/src/life/lifeTypes.ts` and its strict runtime guard.
3. L0-B imports that type and guard; it must not copy the wire interface into a second authority or add model-specific fields to it.
4. L0-A Task 9 owns the first `app/src/main.ts` integration. Rebase L0-B onto that commit before L0-B Task 7, and rebase again before Task 9 if main has moved.
5. After each rebase, run `pnpm.cmd test` and `pnpm.cmd build` before resolving further tasks. Preserve the single backend `onEvent` chain and the existing RuntimeStateCoordinator fallback.

---

### Task 1: Pin the 3D Dependencies and Lockfile Contract

**Files:**
- Modify: `app/package.json`
- Modify: `app/package-lock.json`
- Modify: `app/pnpm-lock.yaml`
- Create: `tests/test_avatar_dependency_contract.py`

**Interfaces:**
- Consumes: repository package management and release version checks.
- Produces: exact, synchronized Three.js/VRM dependencies available to Vite and TypeScript.

- [ ] **Step 1: Write the failing dependency contract test**

```python
import json
from pathlib import Path

import yaml


def test_avatar_dependencies_are_exact_and_lockfiles_agree():
    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / "app/package.json").read_text(encoding="utf-8"))
    npm_lock = json.loads((root / "app/package-lock.json").read_text(encoding="utf-8"))
    pnpm_lock = yaml.safe_load((root / "app/pnpm-lock.yaml").read_text(encoding="utf-8"))
    assert package["dependencies"]["three"] == "0.185.1"
    assert package["dependencies"]["@pixiv/three-vrm"] == "3.5.5"
    assert package["devDependencies"]["@types/three"] == "0.185.1"
    assert npm_lock["packages"][""]["dependencies"]["three"] == "0.185.1"
    assert npm_lock["packages"][""]["dependencies"]["@pixiv/three-vrm"] == "3.5.5"
    assert npm_lock["packages"][""]["devDependencies"]["@types/three"] == "0.185.1"
    importer = pnpm_lock["importers"]["."]
    assert importer["dependencies"]["three"]["specifier"] == "0.185.1"
    assert importer["dependencies"]["three"]["version"].split("(", 1)[0] == "0.185.1"
    assert importer["dependencies"]["@pixiv/three-vrm"]["specifier"] == "3.5.5"
    assert importer["dependencies"]["@pixiv/three-vrm"]["version"].split("(", 1)[0] == "3.5.5"
    assert importer["devDependencies"]["@types/three"]["specifier"] == "0.185.1"
```

- [ ] **Step 2: Run and verify red**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_avatar_dependency_contract.py -q
```

- [ ] **Step 3: Obtain explicit network approval and configure G-drive caches**

Before any install command, record the user's approval in the execution log. Then set:

```powershell
$env:npm_config_cache='G:\Javis-build-cache\npm-cache'
$env:PNPM_STORE_DIR='G:\Javis-build-cache\pnpm-store'
```

- [ ] **Step 4: Install exact dependencies and synchronize both existing locks**

```powershell
Set-Location app
npm.cmd install --save-exact three@0.185.1 @pixiv/three-vrm@3.5.5
npm.cmd install --save-dev --save-exact @types/three@0.185.1
pnpm.cmd install --lockfile-only
```

Do not continue if either lock command fails or resolves a different direct version.

- [ ] **Step 5: Run dependency contract, App tests and build**

```powershell
Set-Location ..
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_avatar_dependency_contract.py -q
Set-Location app
pnpm.cmd test
pnpm.cmd build
```

- [ ] **Step 6: Commit dependency pins only**

```powershell
git add app/package.json app/package-lock.json app/pnpm-lock.yaml tests/test_avatar_dependency_contract.py
git commit -m "build(app): pin local avatar dependencies"
```

---

### Task 2: Define and Validate Avatar Manifests

**Files:**
- Create: `app/src/pet/avatar/avatarTypes.ts`
- Create: `app/src/pet/avatar/AvatarManifest.ts`
- Create: `app/src/pet/avatar/AvatarAssetRegistry.ts`
- Create: `app/public/pets/javis-3d/avatar-manifest.json`
- Create: `app/public/pets/javis-3d/ATTRIBUTION.md`
- Create: `scripts/validate_avatar_asset.py`
- Test: `app/tests/avatarManifest.test.ts`
- Test: `tests/test_avatar_asset_validation.py`

**Interfaces:**
- Consumes: local JSON manifest and local assets.
- Produces: `AvatarManifest`, `parseAvatarManifest()`, `AvatarAssetRegistry.get(id)`, `resolveFallback(id)` and CLI validator.

- [ ] **Step 1: Write failing TypeScript manifest tests**

```typescript
test("accepts the built-in procedural avatar and rejects remote assets", () => {
  const manifest = parseAvatarManifest({
    schemaVersion: 1,
    id: "javis-lightform",
    kind: "procedural3d",
    name: "Javis Lightform",
    accent: "#61e9f0",
    license: { id: "Javis-Original", author: "Javis Project", source: "local" },
    capabilities: ["idle", "blink", "gaze", "mouth"],
    fallbackId: "javis-anime",
  });
  assert.equal(manifest.id, "javis-lightform");
  assert.throws(() => parseAvatarManifest({ ...manifest, model: "https://example.com/a.vrm" }), /local asset/);
});
```

- [ ] **Step 2: Write failing Python budget and hash tests**

```python
import json

from scripts.validate_avatar_asset import validate_avatar_manifest


def valid_manifest(*, kind, model):
    return {
        "schemaVersion": 1,
        "id": "fixture-avatar",
        "version": "1.0.0",
        "kind": kind,
        "name": "Fixture Avatar",
        "model": model,
        "modelSha256": None,
        "license": {
            "id": "Javis-Test-Only",
            "author": "Javis tests",
            "source": "local fixture",
            "attributionFile": "ATTRIBUTION.md",
        },
        "capabilities": ["idle", "blink", "gaze", "mouth"],
        "assetBudget": {
            "maxBytes": 25 * 1024 * 1024,
            "maxTriangles": 80_000,
            "maxMaterials": 8,
            "maxTextureSize": 2048,
        },
        "fallbackId": "javis-anime",
    }


def write_manifest(root, manifest):
    (root / "ATTRIBUTION.md").write_text("test-only asset", encoding="utf-8")
    path = root / "avatar-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_validator_rejects_oversized_or_unlicensed_manifest(tmp_path):
    manifest = valid_manifest(kind="vrm", model="avatar.vrm")
    (tmp_path / "avatar.vrm").write_bytes(b"x" * 1024)
    manifest["assetBudget"]["maxBytes"] = 32
    path = write_manifest(tmp_path, manifest)
    report = validate_avatar_manifest(path)
    assert report["ok"] is False
    assert "asset exceeds max_bytes" in report["errors"]
```

- [ ] **Step 3: Run both tests and verify red**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_avatar_asset_validation.py -q
Set-Location app
pnpm.cmd test
```

- [ ] **Step 4: Implement manifest types and registry**

Use discriminated unions for `vrm`, `gltf`, `procedural3d`, `sprite2d` and `orb`. Reject `http:`, `https:`, protocol-relative paths and paths escaping `/pets/`. Require license metadata and explicit fallback.

The first committed default manifest is `javis-lightform` with `kind: procedural3d`; it provides an attractive zero-asset baseline while the user-approved VRM is selected in Task 9.

- [ ] **Step 5: Implement the Python validator**

The validator must emit deterministic JSON with `ok`, `errors`, `warnings`, `asset_sha256`, `asset_bytes` and budget facts. For GLB/VRM, verify the `glTF` magic and JSON chunk without importing unapproved packages.

- [ ] **Step 6: Run focused tests and commit**

```powershell
git add app/src/pet/avatar/avatarTypes.ts app/src/pet/avatar/AvatarManifest.ts app/src/pet/avatar/AvatarAssetRegistry.ts app/public/pets/javis-3d/avatar-manifest.json app/public/pets/javis-3d/ATTRIBUTION.md scripts/validate_avatar_asset.py app/tests/avatarManifest.test.ts tests/test_avatar_asset_validation.py
git commit -m "feat(avatar): define governed local avatar assets"
```

---

### Task 3: Build the Transparent Three.js Surface

**Files:**
- Create: `app/src/pet/avatar/AvatarSurface.ts`
- Create: `app/src/pet/avatar/AvatarScene.ts`
- Create: `app/tests/avatarTestFakes.ts`
- Test: `app/tests/avatarSurface.test.ts`

**Interfaces:**
- Consumes: host element, validated manifest, injected renderer/clock factories.
- Produces: `createAvatarSurface(options): AvatarSurfaceController` with `setSize()`, `setVisible()`, `renderOnce()`, `setExpressionTarget()`, `diagnostics()`, `dispose()`.

- [ ] **Step 1: Write failing lifecycle tests using injected fakes**

```typescript
test("creates one alpha canvas and disposes every owned resource", async () => {
  const host = createAvatarHostFixture();
  const { resources, counters } = createAvatarResourceFixture();
  const surface = await createAvatarSurface({
    host,
    manifest: proceduralManifestFixture(),
    resources,
  });
  assert.equal(host.querySelectorAll("canvas.avatar-canvas").length, 1);
  surface.setVisible(false);
  assert.equal(counters.cancelledFrameCount, 1);
  surface.dispose();
  assert.equal(counters.rendererDisposeCount, 1);
  assert.equal(counters.textureDisposeCount, counters.textureCount);
  assert.equal(host.querySelectorAll("canvas.avatar-canvas").length, 0);
});
```

- [ ] **Step 2: Run and verify red**

```powershell
Set-Location app
pnpm.cmd test
```

- [ ] **Step 3: Implement renderer and scene boundaries**

Create `WebGLRenderer({ alpha: true, antialias: tier === "3d-high", premultipliedAlpha: true })`. Set a transparent clear color. Keep camera, light and model anchor creation in `AvatarScene.ts`. Run requestAnimationFrame only while visible and in Pet/Live mode.

In `avatarTestFakes.ts`, export exactly `createAvatarHostFixture()`, `createAvatarResourceFixture()`, `proceduralManifestFixture()`, `createDeferredAvatarLoaderFixture()`, `intentFixture()`, `capabilitiesFixture()`, `speakingTargetFixture()` and `expiredSpeakingIntentFixture()`. Every function creates fresh local state. The renderer fixture owns one canvas and increments explicit request/cancel/render/dispose/texture counters; the deferred loader exposes `resolveAll()`; intent fixtures include all 12 L0-A wire fields and accept typed overrides. Later avatar tests import these fixtures instead of inventing hidden globals.

- [ ] **Step 4: Implement deterministic disposal and context listeners**

Store every owned geometry, material, texture, listener and frame ID. `dispose()` is idempotent. Do not dispose externally injected shared assets twice.

- [ ] **Step 5: Run App tests and production build**

```powershell
pnpm.cmd test
pnpm.cmd build
```

- [ ] **Step 6: Commit the surface**

```powershell
git add app/src/pet/avatar/AvatarSurface.ts app/src/pet/avatar/AvatarScene.ts app/tests/avatarTestFakes.ts app/tests/avatarSurface.test.ts
git commit -m "feat(avatar): render an offline transparent 3d surface"
```

---

### Task 4: Create the Polished Zero-Asset `Javis Lightform`

**Files:**
- Create: `app/src/pet/avatar/ProceduralAvatar.ts`
- Test: `app/tests/proceduralAvatar.test.ts`

**Interfaces:**
- Consumes: Three.js scene and `javis-lightform` manifest.
- Produces: a head-and-shoulders procedural avatar with named semantic handles for eyes, mouth, core light, head anchor and body anchor.

- [ ] **Step 1: Write the failing geometry and budget test**

```typescript
import { Scene } from "three";


test("lightform exposes semantic handles within the zero-asset budget", () => {
  const avatar = createProceduralAvatar(new Scene());
  assert.deepEqual(Object.keys(avatar.handles).sort(), [
    "body", "coreLight", "eyes", "head", "mouth",
  ]);
  assert.ok(avatar.diagnostics.triangles <= 20000);
  assert.ok(avatar.diagnostics.materials <= 6);
  avatar.dispose();
  assert.equal(avatar.diagnostics.disposed, true);
});
```

- [ ] **Step 2: Run and verify red**

```powershell
Set-Location app
pnpm.cmd test
```

- [ ] **Step 3: Implement the exact visual composition**

Build a head-and-shoulders silhouette using smooth capsule/sphere geometry, graphite and pearl materials, two soft cyan-violet eye lights, one central core light and a narrow mouth plane. Use no remote textures, random particles or physics. Default motion amplitude is small enough that the face stays inside the 132×158 window.

- [ ] **Step 4: Add deterministic visual-state hooks**

Expose numeric handles only; do not let the avatar read LifeState. `eyes`, `mouth`, `head`, `body` and `coreLight` accept values from ExpressionMixer.

- [ ] **Step 5: Run tests/build and commit**

```powershell
pnpm.cmd test
pnpm.cmd build
git add app/src/pet/avatar/ProceduralAvatar.ts app/tests/proceduralAvatar.test.ts
git commit -m "feat(avatar): add the Javis Lightform fallback body"
```

---

### Task 5: Integrate 3D Skins into `PetSurface` Without Replacing Pet Behavior

**Files:**
- Modify: `app/src/pet/petTypes.ts`
- Modify: `app/src/pet/PetSkinRegistry.ts`
- Modify: `app/src/pet/PetSurface.ts`
- Modify: `app/src/styles.css`
- Test: `app/tests/avatarPetIntegration.test.ts`

**Interfaces:**
- Consumes: AvatarAssetRegistry and dynamic `createAvatarSurface()` import.
- Produces: `PetSkin.kind`, asynchronous generation-safe avatar mount and existing `PetSurfaceController` API unchanged.

- [ ] **Step 1: Write failing integration/source-contract tests**

```typescript
test("3d skin mounts asynchronously while 2d and orb stay synchronous fallbacks", () => {
  assert.equal(getPetSkin("javis-lightform").kind, "procedural3d");
  assert.equal(getPetSkin("javis-anime").kind, "sprite2d");
  assert.equal(getPetSkin("javis-orb").kind, "orb");
});

test("stale avatar loads cannot replace the user's newer skin", async () => {
  const root = document.createElement("div");
  const deferred = createDeferredAvatarLoaderFixture();
  const surface = createPetSurface({
    root,
    onOpenLive: () => undefined,
    onOpenSettings: () => undefined,
    onContextMenu: () => undefined,
    avatarLoader: deferred.load,
  });
  surface.setSkin("javis-lightform");
  surface.setSkin("javis-orb");
  deferred.resolveAll();
  await Promise.resolve();
  assert.equal(root.querySelector(".pet-surface")?.getAttribute("data-skin"), "javis-orb");
  assert.equal(root.querySelectorAll("canvas.avatar-canvas").length, 0);
});
```

- [ ] **Step 2: Run and verify red**

```powershell
Set-Location app
pnpm.cmd test
```

- [ ] **Step 3: Extend skin types and registry**

Use a discriminated union. Keep `setSkin()`, `toggleSkin()`, drag, click, right-click and settings behavior compatible. Add an optional internal/test `avatarLoader` dependency to `PetSurfaceOptions`, defaulting to the production dynamic import; it is not exposed as a user setting. Add one generation token per skin load so a slow 3D promise cannot overwrite a later 2D choice.

- [ ] **Step 4: Add canvas container and transparent edge styles**

The 3D canvas stays inside `.pet-anchor`, uses the existing scale variables and does not cover `.pet-status`. Pointer events remain on the existing button wrapper.

- [ ] **Step 5: Run Pet/App regressions and build**

```powershell
pnpm.cmd test
pnpm.cmd build
```

- [ ] **Step 6: Commit Pet integration**

```powershell
git add app/src/pet/petTypes.ts app/src/pet/PetSkinRegistry.ts app/src/pet/PetSurface.ts app/src/styles.css app/tests/avatarPetIntegration.test.ts
git commit -m "feat(pet): mount 3d avatars with safe fallbacks"
```

---

### Task 6: Implement Expression Mapping and Single-Writer Mixing

**Files:**
- Create: `app/src/pet/avatar/AvatarController.ts`
- Create: `app/src/pet/avatar/ExpressionMapper.ts`
- Create: `app/src/pet/avatar/ExpressionMixer.ts`
- Test: `app/tests/avatarController.test.ts`
- Test: `app/tests/expressionMapper.test.ts`
- Test: `app/tests/expressionMixer.test.ts`

**Interfaces:**
- Consumes: exact `ExpressionIntent v1` from `app/src/life/lifeTypes.ts` and avatar capability handles.
- Produces: `mapExpressionIntent(intent): ExpressionTarget`; `ExpressionMixer.submit(target)`, `tick(now)`, `interrupt()`, `snapshot()`; `AvatarController.applyIntent(intent)`, `tick(now)`, `dispose()`.

- [ ] **Step 1: Write failing nine-state mapping tests**

```typescript
test("maps life states to restrained model-independent targets", () => {
  assert.deepEqual(mapExpressionIntent(intentFixture("listening")), {
    state: "listening", gaze: "user", mouth: 0,
    blinkRate: "normal", posture: "attentive", color: "cyan",
    gesture: "none", interrupt: false,
  });
  assert.equal(mapExpressionIntent(intentFixture("blocked")).posture, "cautious");
  assert.equal(mapExpressionIntent(intentFixture("error")).gesture, "none");
});
```

- [ ] **Step 2: Write failing mixer priority tests**

```typescript
test("speaking keeps blink but interrupt clears mouth and gestures", () => {
  const mixer = new ExpressionMixer(capabilitiesFixture());
  mixer.submit(speakingTargetFixture());
  mixer.tick(100);
  assert.ok(mixer.snapshot().mouth > 0);
  assert.equal(mixer.snapshot().blinkEnabled, true);
  mixer.interrupt();
  assert.equal(mixer.snapshot().mouth, 0);
  assert.equal(mixer.snapshot().gesture, "none");
});
```

- [ ] **Step 3: Run and verify red**

```powershell
Set-Location app
pnpm.cmd test
```

- [ ] **Step 4: Implement semantic mapping and channel ownership**

Only ExpressionMixer writes final handles. Priority is lifecycle/error, interrupt/listening, speaking mouth, blocked/executing, thinking, idle. Transitions are deterministic and interruptible; stale revision and expired intent are rejected before mapping. `AvatarController` is the only public consumer of `ExpressionIntent`: it validates through the imported L0-A guard, maps once, submits to the mixer, and applies the mixer snapshot to `AvatarSurface` semantic handles. Add a controller test proving an older revision and an expired speaking intent cannot reach a handle.

- [ ] **Step 5: Run focused tests, all App tests and build**

```powershell
pnpm.cmd test
pnpm.cmd build
```

- [ ] **Step 6: Commit expression behavior**

```powershell
git add app/src/pet/avatar/AvatarController.ts app/src/pet/avatar/ExpressionMapper.ts app/src/pet/avatar/ExpressionMixer.ts app/tests/avatarController.test.ts app/tests/expressionMapper.test.ts app/tests/expressionMixer.test.ts
git commit -m "feat(avatar): map life intent through a single expression mixer"
```

---

### Task 7: Add Honest Speaking Envelopes and Barge-In

**Files:**
- Create: `app/src/pet/avatar/SpeakingEnvelope.ts`
- Modify: `app/src/main.ts`
- Test: `app/tests/speakingEnvelope.test.ts`
- Modify: `app/tests/voiceBargeIn.test.ts`

**Interfaces:**
- Consumes: playback start/stop, `duration_ms`, request terminal events and `ExpressionIntent.voice_activity`.
- Produces: deterministic `SpeakingEnvelope.start(durationMs, now)`, `level(now)`, `stop()`, `active()`.

- [ ] **Step 1: Write the failing envelope tests**

```typescript
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";


const mainSource = readFileSync(
  fileURLToPath(new URL("../src/main.ts", import.meta.url)),
  "utf8",
);


test("duration envelope is bounded, deterministic and stops immediately", () => {
  const envelope = new SpeakingEnvelope();
  envelope.start(1000, 0);
  assert.equal(envelope.level(0), 0);
  assert.ok(envelope.level(250) >= 0 && envelope.level(250) <= 0.65);
  envelope.stop();
  assert.equal(envelope.level(251), 0);
  assert.equal(envelope.active(), false);
});

test("microphone input callbacks never feed the speaking envelope", () => {
  assert.doesNotMatch(mainSource, /onAudio[^\n]+SpeakingEnvelope/);
  assert.doesNotMatch(mainSource, /onLevel[^\n]+speakingEnvelope/);
});
```

- [ ] **Step 2: Run and verify red**

```powershell
Set-Location app
pnpm.cmd test
```

- [ ] **Step 3: Implement the bounded playback envelope**

Use playback duration and deterministic syllable-like easing with maximum mouth weight `0.65`. Do not claim phoneme accuracy. If duration is absent, use a minimal speaking-state pulse capped at `0.25`.

- [ ] **Step 4: Wire every stop source**

Call `speakingEnvelope.stop()` from voice barge-in, native playback stop, request cancelled, request failed, new listening state, skin dispose and avatar fallback. Do not wait for the previous duration timeout.

- [ ] **Step 5: Run voice/App regressions and build**

```powershell
pnpm.cmd test
pnpm.cmd build
```

- [ ] **Step 6: Commit speaking and interruption**

```powershell
git add app/src/pet/avatar/SpeakingEnvelope.ts app/src/main.ts app/tests/speakingEnvelope.test.ts app/tests/voiceBargeIn.test.ts
git commit -m "feat(avatar): synchronize basic speaking and barge-in"
```

---

### Task 8: Add Performance Governance, Context Recovery and Fallback

**Files:**
- Create: `app/src/pet/avatar/PerformanceGovernor.ts`
- Create: `app/src/pet/avatar/AvatarFallbackCoordinator.ts`
- Modify: `app/src/pet/avatar/AvatarSurface.ts`
- Test: `app/tests/avatarPerformance.test.ts`
- Test: `app/tests/avatarFallback.test.ts`

**Interfaces:**
- Consumes: sampled frame durations, visibility, WebGL capability, context events and user preference.
- Produces: tiers `3d-high`, `3d-low`, `procedural3d`, `sprite2d`, `orb`; diagnostics and controlled recovery.

- [ ] **Step 1: Write failing tier and hysteresis tests**

```typescript
test("sustained slow frames downgrade once and fast recovery requires hysteresis", () => {
  const governor = new PerformanceGovernor({ initialTier: "3d-high" });
  for (let index = 0; index < 90; index += 1) governor.recordFrame(42);
  assert.equal(governor.tier(), "3d-low");
  for (let index = 0; index < 5; index += 1) governor.recordFrame(10);
  assert.equal(governor.tier(), "3d-low");
});

test("context loss falls back and never replays expired speaking state", () => {
  const fallback = new AvatarFallbackCoordinator({
    initialTier: "3d-high",
    fallbackOrder: ["3d-high", "3d-low", "procedural3d", "sprite2d", "orb"],
    maxRecoveryAttempts: 2,
  });
  fallback.onContextLost();
  assert.equal(fallback.currentTier(), "sprite2d");
  fallback.onContextRestored(expiredSpeakingIntentFixture());
  assert.notEqual(fallback.expression().base_state, "speaking");
});
```

- [ ] **Step 2: Run and verify red**

```powershell
Set-Location app
pnpm.cmd test
```

- [ ] **Step 3: Implement tier logic and visibility suspension**

Use a 120-sample rolling p95 frame-time window. Downgrade High below 30 FPS-equivalent (`p95 > 33.3 ms`) after 90 consecutive qualifying samples and downgrade Low below 20 FPS-equivalent (`p95 > 50 ms`) after 60; promotion requires 300 consecutive samples at least 20% inside the target plus no recent context/load failure. Hidden, minimized, Code-only and settings-only modes stop continuous rendering. User-selected reduced motion or forced fallback overrides automatic promotion.

- [ ] **Step 4: Implement WebGL context recovery**

On `webglcontextlost`, prevent default, stop rendering and show fallback. Attempt a bounded recovery count. On success, reload assets and apply only the newest unexpired ExpressionIntent. Repeated failure stays degraded until user manually retries.

- [ ] **Step 5: Run all App tests and build**

```powershell
pnpm.cmd test
pnpm.cmd build
```

- [ ] **Step 6: Commit performance and fallback**

```powershell
git add app/src/pet/avatar/PerformanceGovernor.ts app/src/pet/avatar/AvatarFallbackCoordinator.ts app/src/pet/avatar/AvatarSurface.ts app/tests/avatarPerformance.test.ts app/tests/avatarFallback.test.ts
git commit -m "feat(avatar): govern performance and recover through fallbacks"
```

---

### Task 9: Build the Visual Preview and User Asset Selection Gate

**Files:**
- Create: `app/src/pet/avatar/AvatarPreview.ts`
- Modify: `app/src/main.ts`
- Test: `app/tests/avatarPreview.test.ts`
- Modify after user selection: `app/public/pets/javis-3d/avatar-manifest.json`
- Add after user selection: `app/public/pets/javis-3d/avatar.vrm`
- Modify after user selection: `app/public/pets/javis-3d/ATTRIBUTION.md`

**Interfaces:**
- Consumes: all nine states, four backgrounds, four window/scaling presets and three visual profiles.
- Produces: local `?avatar-preview=1` view, evidence screenshots and one user-approved default avatar ID.

- [ ] **Step 1: Write failing preview-state coverage tests**

```typescript
test("preview exposes every state, background and window preset", () => {
  const model = createAvatarPreviewModel();
  assert.deepEqual(model.states, [
    "idle", "attention", "listening", "thinking", "speaking",
    "executing", "blocked", "error", "offline",
  ]);
  assert.deepEqual(model.backgrounds, ["light", "dark", "wallpaper", "checker"]);
  assert.deepEqual(model.scales, [1, 1.25, 1.5, 1.75]);
});
```

- [ ] **Step 2: Run and verify red**

```powershell
Set-Location app
pnpm.cmd test
```

- [ ] **Step 3: Implement the local-only preview**

Add `?avatar-preview=1` routing that does not connect to production conversations or write user memory. Provide deterministic controls for state, intensity, gaze, speaking, interrupt, background, size, scale, tier and context loss.

- [ ] **Step 4: Produce three comparable visual profiles**

Use the same technical budget and framing for:

- `light-tech-human`：轻科技人形；
- `neon-pilot`：风格化动漫数字人；
- `quiet-lifeform`：半抽象数字生命。

Capture idle, listening, thinking and speaking at 200×218 over light and complex backgrounds. Present the 24-image comparison to the user and record the selected profile in the review report.

- [ ] **Step 5: Import the approved VRM only after selection and license review**

Place the approved file at `app/public/pets/javis-3d/avatar.vrm`, record real author/source/license values in `ATTRIBUTION.md`, calculate SHA-256, update the manifest to `kind: vrm`, and run:

```powershell
& 'G:\Javis\venv\Scripts\python.exe' scripts/validate_avatar_asset.py app/public/pets/javis-3d/avatar-manifest.json
```

If no candidate satisfies quality, license and budget simultaneously, retain `javis-lightform` as the default and do not insert an unapproved asset.

- [ ] **Step 6: Run preview/App tests and commit the reviewed selection**

```powershell
Set-Location app
pnpm.cmd test
pnpm.cmd build
Set-Location ..
git add app/src/pet/avatar/AvatarPreview.ts app/src/main.ts app/tests/avatarPreview.test.ts app/public/pets/javis-3d/avatar-manifest.json app/public/pets/javis-3d/avatar.vrm app/public/pets/javis-3d/ATTRIBUTION.md
git commit -m "feat(avatar): approve the default Javis body"
```

When `javis-lightform` remains default, omit the nonexistent `avatar.vrm` from `git add` and commit the reviewed manifest and attribution only.

---

### Task 10: Lock Release, Accessibility and D-Drive Acceptance

**Files:**
- Create: `docs/verification/JAVIS_L0B_D_DRIVE_ACCEPTANCE.md`
- Modify: `tests/test_avatar_dependency_contract.py`
- Modify: `tests/test_avatar_asset_validation.py`
- Modify: `app/tests/surfaceStatus.test.ts`
- Modify: `app/tests/appErrorBoundary.test.ts`

**Interfaces:**
- Consumes: complete L0-B avatar implementation.
- Produces: build/package contracts, accessible fallback evidence and exact D-drive manual matrix.

- [ ] **Step 1: Add failing release and fallback contract assertions**

```python
def test_avatar_release_is_offline_licensed_and_bounded():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "app/public/pets/javis-3d/avatar-manifest.json").read_text(encoding="utf-8"))
    assert manifest["fallbackId"] in {"javis-anime", "javis-orb"}
    assert manifest["license"]["id"]
    assert not str(manifest.get("model", "")).startswith(("http:", "https:"))
    assert manifest["assetBudget"]["maxBytes"] <= 25 * 1024 * 1024
```

- [ ] **Step 2: Add reduced-motion and renderer-failure App tests**

Assert that reduced motion lowers animation, unknown life states become idle, renderer construction failure keeps the status text, and switching to Orb remains possible.

- [ ] **Step 3: Run all automated gates**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_avatar_*.py tests/test_app_v3_integrated_release.py tests/test_portable_workspace_paths.py -q
Set-Location app
pnpm.cmd test
pnpm.cmd build
```

- [ ] **Step 4: Write and execute the D-drive matrix on the exact package**

Record source commit, package SHA-256, Windows/WebView2 versions, GPU, scale, wallpaper type, selected avatar, tier, active/idle FPS, memory, load time, context-loss behavior, voice interruption, fallback, restart persistence, reinstall and uninstall results.

Each of these combinations needs a screenshot or log reference:

- 100%, 125%, 150%, 175% scale;
- 132×158 and 200×218 windows;
- light, dark and complex wallpaper;
- idle, listening, thinking, speaking, blocked, error and offline;
- 3D High, 3D Low, 2D and Orb;
- normal playback, barge-in and backend disconnect.

- [ ] **Step 5: Return every D failure to the worktree**

Do not edit D-drive installed files. Reproduce the failure in automated or preview tests, fix in `G:\Javis-worktrees`, rebuild from `G:\Javis`, then reinstall the new exact package on D.

- [ ] **Step 6: Commit release and acceptance contracts**

```powershell
git add docs/verification/JAVIS_L0B_D_DRIVE_ACCEPTANCE.md tests/test_avatar_dependency_contract.py tests/test_avatar_asset_validation.py app/tests/surfaceStatus.test.ts app/tests/appErrorBoundary.test.ts
git commit -m "test(avatar): lock accessibility and D-drive acceptance"
```

---

## Spec Coverage Matrix

| Approved L0-B design area | Implementation tasks | Executable or visual evidence |
|---|---:|---|
| Eight-module body architecture including AvatarController | 2, 3, 6, 8 | module tests and single-writer controller test |
| Unified VRM/GLTF/procedural/2D/Orb manifest and governed local assets | 2, 5, 9, 10 | TS parser, Python validator, hash/license contract |
| Exact dependency pins, local bundle and CSP/offline boundary | 1, 2, 10 | synchronized lock test, Vite build and remote-URL scan |
| Transparent renderer, camera, resize, lifecycle and deterministic disposal | 3, 4 | injected renderer counters and build tests |
| Small-window visual quality, three profiles and user approval | 4, 9 | 24-image preview comparison and recorded selection |
| Pet drag/click/right-click/settings compatibility and generation-safe loading | 5 | DOM integration and stale-promise tests |
| Exact 12-field ExpressionIntent consumption, nine states and no second authority | 6 | mapper/controller stale, expiry and schema tests |
| Channel ownership, priority, interruptible transitions and neutral decay | 6 | mixer arbitration/idempotency tests |
| Honest playback-driven mouth envelope and immediate barge-in | 7 | envelope, source-contract and voice regression tests |
| Performance tiers, 30/20 FPS targets, visibility suspension and hysteresis | 8, 10 | governor tests plus D-drive measurements |
| WebGL loss, bounded recovery and 3D → 2D → Orb safety | 8, 10 | context tests and real WebView2 evidence |
| Accessibility, user controls, Windows scaling and no black border | 9, 10 | reduced-motion tests and D-drive screenshot matrix |
| First-body ten-step experience and all declared non-goals | 7-10 | acceptance checklist with explicit PASS/FAIL per step |

---

## Final Verification

- [ ] Run `git diff --check` and confirm no whitespace errors.
- [ ] Verify package.json, package-lock.json and pnpm-lock.yaml contain the exact same three direct versions.
- [ ] Run every Python avatar test and the release/path regression files named in Task 10.
- [ ] Run the complete App test suite and production build.
- [ ] Search avatar implementation files for unfinished placeholder markers and remove them.
- [ ] Verify Vite output and public assets contain no remote script, model, texture, font or CDN URL.
- [ ] Run the Python asset validator and archive its JSON result.
- [ ] Force renderer creation failure and verify 2D/Orb remain usable.
- [ ] Force WebGL context loss and verify no stale speaking state returns.
- [ ] Verify user barge-in clears native playback, body mouth and speaking gesture immediately.
- [ ] Verify closing or hiding Pet/Live stops the continuous render loop.
- [ ] Verify `G:\Javis` remains the build source and D remains an installed-user test environment.
- [ ] Build and test the final user-approved avatar package on D; never infer visual PASS from browser preview alone.

## Execution Boundary

Completing this plan produces L0-B/B0 only. It does not implement full phoneme lip sync, functional emotions, relationship-aware gestures, complex full-body motion, multi-device bodies or L1 inner-state dynamics. L0-B may read `ExpressionIntent v1` but cannot become a second state authority.
