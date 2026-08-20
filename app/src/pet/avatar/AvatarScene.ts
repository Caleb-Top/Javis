import {
  AmbientLight,
  DirectionalLight,
  Group,
  PerspectiveCamera,
  Scene,
  type Light,
} from "three";

export type AvatarSceneController = Readonly<{
  scene: Scene;
  camera: PerspectiveCamera;
  modelAnchor: Group;
  lights: readonly Light[];
  setSize(width: number, height: number): void;
  dispose(): void;
}>;

export function createAvatarScene(): AvatarSceneController {
  const scene = new Scene();
  const camera = new PerspectiveCamera(28, 1, 0.05, 100);
  const modelAnchor = new Group();
  const ambientLight = new AmbientLight(0xd9f8ff, 1.25);
  const keyLight = new DirectionalLight(0xffffff, 2.1);
  const lights: readonly Light[] = [ambientLight, keyLight];
  let disposed = false;

  scene.name = "javis-avatar-scene";
  modelAnchor.name = "javis-avatar-model-anchor";
  modelAnchor.position.set(0, 0.25, 0);

  camera.name = "javis-avatar-camera";
  camera.position.set(0, 0.02, 5.9);
  camera.lookAt(0, -0.08, 0);

  keyLight.name = "javis-avatar-key-light";
  keyLight.position.set(2.5, 3.5, 4);
  scene.add(modelAnchor, ambientLight, keyLight);

  return {
    scene,
    camera,
    modelAnchor,
    lights,
    setSize(width, height) {
      if (disposed) return;
      camera.aspect = width / Math.max(1, height);
      camera.updateProjectionMatrix();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      modelAnchor.clear();
      scene.remove(modelAnchor, ambientLight, keyLight);
      scene.clear();
    },
  };
}
