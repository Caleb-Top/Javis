import {
  BufferGeometry,
  CapsuleGeometry,
  DoubleSide,
  Group,
  Material,
  Mesh,
  MeshPhysicalMaterial,
  MeshStandardMaterial,
  PlaneGeometry,
  PointLight,
  SphereGeometry,
  TorusGeometry,
  type Object3D,
} from "three";

const HANDLE_MIN = 0;
const HANDLE_MAX = 1;

export type BoundedNumericHandle = Readonly<{
  min: typeof HANDLE_MIN;
  max: typeof HANDLE_MAX;
  value: number;
  set(value: number): number;
}>;

export type ProceduralAvatarHandles = Readonly<{
  body: BoundedNumericHandle;
  coreLight: BoundedNumericHandle;
  eyes: BoundedNumericHandle;
  head: BoundedNumericHandle;
  mouth: BoundedNumericHandle;
}>;

export type ProceduralAvatarDiagnostics = Readonly<{
  triangles: number;
  materials: number;
  disposed: boolean;
}>;

export type ProceduralAvatar = Readonly<{
  object: Object3D;
  handles: ProceduralAvatarHandles;
  diagnostics: ProceduralAvatarDiagnostics;
  dispose(): void;
}>;

function clampHandleValue(value: number): number {
  if (Number.isNaN(value)) return HANDLE_MIN;
  return Math.min(HANDLE_MAX, Math.max(HANDLE_MIN, value));
}

function createNumericHandle(
  initialValue: number,
  apply: (value: number) => void,
): BoundedNumericHandle {
  let currentValue = clampHandleValue(initialValue);
  apply(currentValue);

  return Object.freeze({
    min: HANDLE_MIN,
    max: HANDLE_MAX,
    get value() {
      return currentValue;
    },
    set(value: number) {
      currentValue = clampHandleValue(value);
      apply(currentValue);
      return currentValue;
    },
  });
}

function countGeometryTriangles(geometry: BufferGeometry): number {
  const elementCount = geometry.index?.count
    ?? geometry.getAttribute("position")?.count
    ?? 0;
  return Math.floor(elementCount / 3);
}

export function createProceduralAvatar(parent: Object3D): ProceduralAvatar {
  const geometries = new Set<BufferGeometry>();
  const materials = new Set<Material>();
  let disposed = false;

  const ownGeometry = <Geometry extends BufferGeometry>(geometry: Geometry): Geometry => {
    geometries.add(geometry);
    return geometry;
  };
  const ownMaterial = <OwnedMaterial extends Material>(material: OwnedMaterial): OwnedMaterial => {
    materials.add(material);
    return material;
  };

  const graphite = ownMaterial(new MeshPhysicalMaterial({
    color: 0x151b23,
    metalness: 0.72,
    roughness: 0.3,
    clearcoat: 0.4,
    clearcoatRoughness: 0.3,
  }));
  graphite.name = "javis-lightform-graphite";

  const pearl = ownMaterial(new MeshPhysicalMaterial({
    color: 0xdce8ea,
    metalness: 0.18,
    roughness: 0.24,
    clearcoat: 0.72,
    clearcoatRoughness: 0.2,
  }));
  pearl.name = "javis-lightform-pearl";

  const cyanEye = ownMaterial(new MeshStandardMaterial({
    color: 0x9bfbff,
    emissive: 0x19dfea,
    emissiveIntensity: 1.8,
    metalness: 0.08,
    roughness: 0.2,
  }));
  cyanEye.name = "javis-lightform-eye-cyan";

  const violetEye = ownMaterial(new MeshStandardMaterial({
    color: 0xe0c3ff,
    emissive: 0x9b5de5,
    emissiveIntensity: 1.8,
    metalness: 0.08,
    roughness: 0.2,
  }));
  violetEye.name = "javis-lightform-eye-violet";

  const coreMaterial = ownMaterial(new MeshStandardMaterial({
    color: 0xcaffff,
    emissive: 0x45f3f1,
    emissiveIntensity: 2,
    metalness: 0.14,
    roughness: 0.18,
  }));
  coreMaterial.name = "javis-lightform-core";

  const mouthMaterial = ownMaterial(new MeshStandardMaterial({
    color: 0x24192d,
    emissive: 0x7546a8,
    emissiveIntensity: 0.38,
    metalness: 0.22,
    roughness: 0.42,
    side: DoubleSide,
  }));
  mouthMaterial.name = "javis-lightform-mouth";

  const root = new Group();
  root.name = "javis-lightform";
  root.userData.avatarKind = "procedural3d";

  const bodyAnchor = new Group();
  bodyAnchor.name = "javis-lightform-body";
  bodyAnchor.userData.semanticHandle = "body";
  root.add(bodyAnchor);

  const shoulderGeometry = ownGeometry(new SphereGeometry(1.02, 32, 16));
  const shoulders = new Mesh(shoulderGeometry, graphite);
  shoulders.name = "javis-lightform-shoulders";
  shoulders.position.set(0, -0.99, -0.04);
  shoulders.scale.set(1, 0.34, 0.52);
  bodyAnchor.add(shoulders);

  const torsoGeometry = ownGeometry(new CapsuleGeometry(0.5, 0.62, 8, 24));
  const torso = new Mesh(torsoGeometry, graphite);
  torso.name = "javis-lightform-torso";
  torso.position.set(0, -0.77, -0.08);
  torso.scale.set(1.13, 1, 0.68);
  bodyAnchor.add(torso);

  const chestGeometry = ownGeometry(new SphereGeometry(0.56, 24, 12));
  const chestPlate = new Mesh(chestGeometry, pearl);
  chestPlate.name = "javis-lightform-chest-plate";
  chestPlate.position.set(0, -0.78, 0.28);
  chestPlate.scale.set(0.68, 0.72, 0.22);
  bodyAnchor.add(chestPlate);

  const neckGeometry = ownGeometry(new CapsuleGeometry(0.19, 0.22, 6, 18));
  const neck = new Mesh(neckGeometry, graphite);
  neck.name = "javis-lightform-neck";
  neck.position.set(0, -0.3, -0.05);
  neck.scale.set(1, 1, 0.82);
  bodyAnchor.add(neck);

  const headAnchor = new Group();
  headAnchor.name = "javis-lightform-head";
  headAnchor.userData.semanticHandle = "head";
  headAnchor.position.set(0, 0.23, 0);
  bodyAnchor.add(headAnchor);

  const headGeometry = ownGeometry(new SphereGeometry(0.66, 32, 24));
  const headShell = new Mesh(headGeometry, graphite);
  headShell.name = "javis-lightform-head-shell";
  headShell.scale.set(0.84, 1.04, 0.78);
  headAnchor.add(headShell);

  const faceGeometry = ownGeometry(new CapsuleGeometry(0.43, 0.38, 10, 28));
  const face = new Mesh(faceGeometry, pearl);
  face.name = "javis-lightform-face";
  face.position.set(0, -0.05, 0.22);
  face.scale.set(0.93, 1, 0.56);
  headAnchor.add(face);

  const templeGeometry = ownGeometry(new SphereGeometry(0.15, 20, 10));
  for (const side of [-1, 1]) {
    const temple = new Mesh(templeGeometry, graphite);
    temple.name = side < 0
      ? "javis-lightform-left-temple"
      : "javis-lightform-right-temple";
    temple.position.set(side * 0.5, 0.01, 0.03);
    temple.scale.set(0.55, 1.55, 0.72);
    headAnchor.add(temple);
  }

  const eyesAnchor = new Group();
  eyesAnchor.name = "javis-lightform-eyes";
  eyesAnchor.userData.semanticHandle = "eyes";
  headAnchor.add(eyesAnchor);

  const eyeGeometry = ownGeometry(new SphereGeometry(0.14, 24, 12));
  const leftEye = new Mesh(eyeGeometry, cyanEye);
  leftEye.name = "javis-lightform-left-eye";
  leftEye.position.set(-0.23, 0.08, 0.52);
  leftEye.scale.set(1, 0.32, 0.3);
  eyesAnchor.add(leftEye);

  const rightEye = new Mesh(eyeGeometry, violetEye);
  rightEye.name = "javis-lightform-right-eye";
  rightEye.position.set(0.23, 0.08, 0.52);
  rightEye.scale.set(1, 0.32, 0.3);
  eyesAnchor.add(rightEye);

  const leftEyeLight = new PointLight(0x57f4f5, 0.42, 0.92, 2);
  leftEyeLight.name = "javis-lightform-left-eye-light";
  leftEyeLight.position.set(-0.23, 0.08, 0.62);
  eyesAnchor.add(leftEyeLight);

  const rightEyeLight = new PointLight(0xa86cf0, 0.42, 0.92, 2);
  rightEyeLight.name = "javis-lightform-right-eye-light";
  rightEyeLight.position.set(0.23, 0.08, 0.62);
  eyesAnchor.add(rightEyeLight);

  const mouthGeometry = ownGeometry(new PlaneGeometry(0.27, 0.035, 4, 1));
  const mouth = new Mesh(mouthGeometry, mouthMaterial);
  mouth.name = "javis-lightform-mouth";
  mouth.userData.semanticHandle = "mouth";
  mouth.position.set(0, -0.22, 0.585);
  headAnchor.add(mouth);

  const coreAnchor = new Group();
  coreAnchor.name = "javis-lightform-core-light";
  coreAnchor.userData.semanticHandle = "coreLight";
  coreAnchor.position.set(0, -0.73, 0.49);
  bodyAnchor.add(coreAnchor);

  const coreGeometry = ownGeometry(new SphereGeometry(0.15, 24, 16));
  const core = new Mesh(coreGeometry, coreMaterial);
  core.name = "javis-lightform-core";
  coreAnchor.add(core);

  const coreRingGeometry = ownGeometry(new TorusGeometry(0.215, 0.018, 8, 32));
  const coreRing = new Mesh(coreRingGeometry, pearl);
  coreRing.name = "javis-lightform-core-ring";
  coreRing.position.z = -0.015;
  coreAnchor.add(coreRing);

  const corePointLight = new PointLight(0x59f5f1, 1.05, 1.6, 2);
  corePointLight.name = "javis-lightform-core-point-light";
  corePointLight.position.z = 0.13;
  coreAnchor.add(corePointLight);

  parent.add(root);

  const handles: ProceduralAvatarHandles = Object.freeze({
    body: createNumericHandle(0.5, (value) => {
      if (disposed) return;
      const centered = value - 0.5;
      bodyAnchor.position.y = centered * 0.035;
      bodyAnchor.rotation.z = centered * 0.018;
      torso.scale.y = 1 + centered * 0.025;
      chestPlate.position.y = -0.78 + centered * 0.018;
    }),
    coreLight: createNumericHandle(0.6, (value) => {
      if (disposed) return;
      coreMaterial.emissiveIntensity = 0.65 + value * 2.35;
      corePointLight.intensity = 0.12 + value * 1.55;
      const scale = 0.86 + value * 0.18;
      core.scale.setScalar(scale);
      coreRing.scale.setScalar(0.94 + value * 0.08);
    }),
    eyes: createNumericHandle(1, (value) => {
      if (disposed) return;
      const eyeHeight = 0.08 + value * 0.24;
      leftEye.scale.y = eyeHeight;
      rightEye.scale.y = eyeHeight;
      cyanEye.emissiveIntensity = 0.2 + value * 2.05;
      violetEye.emissiveIntensity = 0.2 + value * 2.05;
      leftEyeLight.intensity = 0.05 + value * 0.48;
      rightEyeLight.intensity = 0.05 + value * 0.48;
    }),
    head: createNumericHandle(0.5, (value) => {
      if (disposed) return;
      const centered = value - 0.5;
      headAnchor.rotation.x = centered * -0.1;
      headAnchor.position.y = 0.23 + centered * 0.018;
    }),
    mouth: createNumericHandle(0, (value) => {
      if (disposed) return;
      mouth.scale.y = 0.24 + value * 2.5;
      mouthMaterial.emissiveIntensity = 0.22 + value * 0.9;
    }),
  });

  let triangles = 0;
  root.traverse((object) => {
    if (object instanceof Mesh) {
      triangles += countGeometryTriangles(object.geometry);
    }
  });

  const diagnostics: ProceduralAvatarDiagnostics = Object.freeze({
    triangles,
    materials: materials.size,
    get disposed() {
      return disposed;
    },
  });

  return Object.freeze({
    object: root,
    handles,
    diagnostics,
    dispose() {
      if (disposed) return;
      disposed = true;
      parent.remove(root);
      root.clear();
      geometries.forEach((geometry) => geometry.dispose());
      materials.forEach((material) => material.dispose());
    },
  });
}
