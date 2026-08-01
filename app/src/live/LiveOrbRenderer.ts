import type { LiveState } from "./liveState";
import { LIVE_ORB_RENDER_SCALE } from "../desktop/surfaceDimensions.ts";

export type OrbPaletteName = "cobalt" | "arctic" | "indigo" | "graphite";

export type LiveOrbController = {
  setState(state: LiveState): void;
  setAudioLevel(level: number): void;
  setPalette(name: OrbPaletteName): void;
  destroy(): void;
};

type Palette = {
  accentDark: Float32Array;
  accentMain: Float32Array;
  accentLight: Float32Array;
};

const palettes: Record<OrbPaletteName, Palette> = {
  cobalt: {
    accentDark: new Float32Array([0.003, 0.025, 0.095]),
    accentMain: new Float32Array([0.016, 0.205, 0.78]),
    accentLight: new Float32Array([0.18, 0.59, 0.98]),
  },
  arctic: {
    accentDark: new Float32Array([0.003, 0.05, 0.065]),
    accentMain: new Float32Array([0.018, 0.36, 0.47]),
    accentLight: new Float32Array([0.34, 0.84, 0.9]),
  },
  indigo: {
    accentDark: new Float32Array([0.01, 0.018, 0.09]),
    accentMain: new Float32Array([0.07, 0.13, 0.62]),
    accentLight: new Float32Array([0.47, 0.56, 0.98]),
  },
  graphite: {
    accentDark: new Float32Array([0.018, 0.026, 0.036]),
    accentMain: new Float32Array([0.12, 0.22, 0.31]),
    accentLight: new Float32Array([0.56, 0.7, 0.8]),
  },
};

const stateIndex: Record<LiveState, number> = {
  idle: 0,
  listening: 1,
  thinking: 2,
  speaking: 3,
  executing: 4,
  blocked: 4,
  error: 4,
  offline: 0,
};

const stateEnergy: Record<LiveState, number> = {
  idle: 0.14,
  listening: 0.68,
  thinking: 0.38,
  speaking: 0.82,
  executing: 0.48,
  blocked: 0.28,
  error: 0.22,
  offline: 0.04,
};

const vertexSource = `
  attribute vec2 position;
  void main() {
    gl_Position = vec4(position, 0.0, 1.0);
  }
`;

const fragmentSource = `
  precision highp float;

  uniform vec2 resolution;
  uniform vec2 pointer;
  uniform float time;
  uniform float state;
  uniform float energy;
  uniform vec3 accentDark;
  uniform vec3 accentMain;
  uniform vec3 accentLight;

  float gaussian(float value, float width) {
    return exp(-(value * value) / max(width, 0.00001));
  }

  float ring(float radius, float target, float width) {
    return gaussian(radius - target, width);
  }

  void main() {
    vec2 frag = gl_FragCoord.xy;
    vec2 center = resolution * 0.5;
    vec2 p = (frag - center) / min(resolution.x, resolution.y) / ${LIVE_ORB_RENDER_SCALE.toFixed(2)};
    float radius = length(p);
    float angle = atan(p.y, p.x);
    float t = time;

    float idleBreath = sin(t * 2.24) * 0.004;
    float listeningWave = state == 1.0
      ? (sin(angle * 5.0 - t * 5.2) + sin(angle * 3.0 + t * 3.1) * 0.42) * 0.009 * energy
      : 0.0;
    float thinkingWave = state == 2.0 ? sin(angle * 3.0 - t * 1.7) * 0.006 : 0.0;
    float speakingWave = state == 3.0
      ? (sin(angle * 4.0 + t * 7.2) + sin(angle * 7.0 - t * 4.4) * 0.3) * 0.013 * energy
      : 0.0;
    float executingWave = state == 4.0 ? sin(angle * 2.0 - t * 3.0) * 0.003 : 0.0;
    float deformation = listeningWave + thinkingWave + speakingWave + executingWave;

    float coreAmplitude = 0.0018;
    float coreSpeed = 1.15;
    if (state == 1.0) { coreAmplitude = 0.0068; coreSpeed = 3.9; }
    if (state == 2.0) { coreAmplitude = 0.0052; coreSpeed = 2.15; }
    if (state == 3.0) { coreAmplitude = 0.0084; coreSpeed = 5.4; }
    if (state == 4.0) { coreAmplitude = 0.0042; coreSpeed = 2.8; }
    float coreDeformation = (
      sin(angle * 3.0 - t * coreSpeed)
      + sin(angle * 5.0 + t * coreSpeed * 0.63) * 0.34
    ) * coreAmplitude;
    float coreBaseRadius = 0.073 + idleBreath * 0.22 + energy * 0.002;
    float coreRadius = coreBaseRadius + coreDeformation;
    float mainRadius = 0.154 + idleBreath + deformation;
    float mainWidth = 0.052 + energy * 0.006;
    float outerRadius = 0.235 + deformation * 0.42;

    vec3 color = vec3(0.0015, 0.004, 0.012);
    float discMask = smoothstep(outerRadius + 0.034, outerRadius - 0.018, radius);
    color += accentDark * exp(-radius * 5.4) * 0.28;

    float coreMask = smoothstep(coreRadius + 0.006, coreRadius - 0.006, radius);
    float coreEdge = ring(radius, coreRadius, 0.0003);
    vec2 lightDirection = normalize(vec2(-0.32 + pointer.x * 0.08, 0.42 + pointer.y * 0.06));
    vec2 normal = normalize(p + vec2(0.0001));
    float highlight = pow(max(0.0, dot(normal, lightDirection)), 9.0);
    vec2 coreUv = p / max(coreBaseRadius, 0.001);
    float waveY = sin(coreUv.x * 3.4 - t * coreSpeed) * (0.10 + energy * 0.08);
    waveY += sin(coreUv.x * 6.2 + t * coreSpeed * 0.54) * 0.045;
    float coreWaveBand = gaussian(coreUv.y - waveY, 0.018);
    float coreWaveBandBack = gaussian(
      coreUv.y + waveY * 0.66 + sin(t * 1.6) * 0.08,
      0.032
    );
    float coreFlow = 0.5 + 0.5 * sin(
      coreUv.x * 2.6 + coreUv.y * 3.2 - t * coreSpeed * 0.82
    );
    float coreDepth = clamp(0.46 + coreUv.y * 0.30 + coreFlow * 0.11, 0.0, 1.0);
    vec3 coreColor = mix(accentDark, mix(accentDark, accentMain, 0.82), coreDepth);
    coreColor = mix(coreColor, accentMain, coreWaveBandBack * (0.12 + energy * 0.08));
    coreColor += accentLight * coreWaveBand * (0.09 + energy * 0.16);
    coreColor += accentLight * highlight * 0.24;
    coreColor += accentMain * coreEdge * 0.18;
    color = mix(color, coreColor, coreMask);

    float torusDistance = abs(radius - mainRadius);
    float torusMask = smoothstep(mainWidth + 0.012, mainWidth - 0.01, torusDistance);
    float torusBody = sqrt(max(0.0, 1.0 - pow(torusDistance / mainWidth, 2.0)));
    float torusEdge = ring(torusDistance, mainWidth, 0.00042);
    float innerShade = smoothstep(0.0, mainWidth, torusDistance);

    vec3 stateTint = accentMain;
    if (state == 1.0) stateTint = mix(accentMain, vec3(0.05, 0.56, 0.66), 0.34);
    if (state == 2.0) stateTint = mix(accentMain, vec3(0.16, 0.18, 0.62), 0.28);
    if (state == 3.0) stateTint = mix(accentMain, accentLight, 0.32);

    vec3 torusColor = mix(accentDark, stateTint, pow(torusBody, 0.72));
    torusColor = mix(torusColor, accentLight, pow(torusBody, 4.2) * (0.18 + highlight * 0.34));
    torusColor *= 1.0 - innerShade * 0.12;
    torusColor += accentMain * torusEdge * 0.12;

    float cyanArc = gaussian(angle - 1.88, 0.075);
    float violetArc = gaussian(angle + 1.08, 0.12);
    torusColor += vec3(0.08, 0.55, 0.76) * cyanArc * pow(torusBody, 5.0) * 0.10;
    torusColor += vec3(0.20, 0.10, 0.46) * violetArc * pow(torusBody, 4.0) * 0.06;
    color = mix(color, torusColor, torusMask * 0.985);

    float outerSharp = ring(radius, outerRadius, 0.0005);
    float outerSoft = ring(radius, outerRadius, 0.0055);
    color += accentMain * outerSoft * (0.19 + energy * 0.10);
    color += accentLight * outerSharp * (0.12 + energy * 0.05);

    float phaseA = fract(t * (state == 3.0 ? 0.54 : 0.22));
    float phaseB = fract(t * (state == 1.0 ? 0.42 : 0.16) + 0.48);
    float rippleA = ring(radius, 0.255 + phaseA * 0.065, 0.00055) * (1.0 - phaseA);
    float rippleB = ring(radius, 0.245 + phaseB * 0.085, 0.0005) * (1.0 - phaseB);
    float rippleStrength = state == 1.0 ? 0.095 : (state == 3.0 ? 0.12 : 0.016);
    color += accentMain * (rippleA + rippleB) * rippleStrength;

    if (state == 2.0) {
      float thoughtArc = ring(radius, 0.198, 0.00042);
      float movingArc = smoothstep(0.55, 0.98, cos(angle - t * 1.1));
      color += vec3(0.10, 0.25, 0.82) * thoughtArc * movingArc * 0.44;
    }

    if (state == 4.0) {
      float progressRing = ring(radius, 0.266, 0.00034);
      float progressHead = smoothstep(0.74, 0.99, cos(angle - t * 1.75));
      color += vec3(0.08, 0.48, 0.90) * progressRing * (0.10 + progressHead * 0.76);
    }

    color = pow(max(color, vec3(0.0)), vec3(0.84));
    float visual = max(coreMask, max(torusMask, max(outerSoft * 0.72, outerSharp)));
    visual = max(visual, (rippleA + rippleB) * rippleStrength * 2.0);
    float alpha = clamp(max(visual, discMask * 0.93), 0.0, 1.0);
    gl_FragColor = vec4(color * alpha, alpha);
  }
`;

function compileShader(gl: WebGLRenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) throw new Error("Unable to create Javis orb shader");
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const detail = gl.getShaderInfoLog(shader) || "unknown shader error";
    gl.deleteShader(shader);
    throw new Error(detail);
  }
  return shader;
}

function savedPalette(): OrbPaletteName {
  try {
    const value = localStorage.getItem("javis.app.orbPalette") as OrbPaletteName | null;
    if (value && value in palettes) return value;
  } catch {
    // The default palette remains usable in a locked-down WebView.
  }
  return "cobalt";
}

export function createLiveOrbRenderer(canvas: HTMLCanvasElement): LiveOrbController {
  const preservePreviewFrame = new URLSearchParams(window.location.search).has("orb-preview");
  const gl = canvas.getContext("webgl", {
    alpha: true,
    antialias: true,
    premultipliedAlpha: false,
    preserveDrawingBuffer: preservePreviewFrame,
    powerPreference: "high-performance",
  } satisfies WebGLContextAttributes) as WebGLRenderingContext | null;
  if (!gl) throw new Error("WebGL is required for the Javis Live orb");

  const program = gl.createProgram();
  if (!program) throw new Error("Unable to create Javis orb program");
  gl.attachShader(program, compileShader(gl, gl.VERTEX_SHADER, vertexSource));
  gl.attachShader(program, compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) || "Unable to link Javis orb program");
  }
  gl.useProgram(program);

  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
    gl.STATIC_DRAW,
  );
  const position = gl.getAttribLocation(program, "position");
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

  const uniform = (name: string): WebGLUniformLocation | null => gl.getUniformLocation(program, name);
  const uniforms = {
    resolution: uniform("resolution"),
    pointer: uniform("pointer"),
    time: uniform("time"),
    state: uniform("state"),
    energy: uniform("energy"),
    accentDark: uniform("accentDark"),
    accentMain: uniform("accentMain"),
    accentLight: uniform("accentLight"),
  };

  let state: LiveState = "idle";
  let energy = stateEnergy.idle;
  let energyTarget = energy;
  let audioLevel = 0;
  let palette = palettes[savedPalette()];
  let frame = 0;
  let disposed = false;
  const pointer = { x: 0, y: 0 };

  const resize = (): void => {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.floor(canvas.clientWidth * ratio));
    const height = Math.max(1, Math.floor(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      gl.viewport(0, 0, width, height);
    }
  };

  const onPointerMove = (event: PointerEvent): void => {
    const rect = canvas.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1;
    pointer.y = 1 - ((event.clientY - rect.top) / Math.max(1, rect.height)) * 2;
  };
  canvas.addEventListener("pointermove", onPointerMove);

  const render = (now: number): void => {
    if (disposed) return;
    resize();
    energy += (energyTarget - energy) * 0.055;
    audioLevel *= 0.91;
    let reactiveEnergy = energy;
    if (state === "listening") {
      reactiveEnergy += Math.sin(now * 0.011) * 0.10
        + Math.sin(now * 0.021) * 0.05
        + Math.min(0.26, audioLevel * 0.32);
    } else if (state === "speaking") {
      reactiveEnergy += Math.sin(now * 0.017) * 0.13 + Math.sin(now * 0.031) * 0.06;
    }

    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(uniforms.resolution, canvas.width, canvas.height);
    gl.uniform2f(uniforms.pointer, pointer.x, pointer.y);
    gl.uniform1f(uniforms.time, now * 0.001);
    gl.uniform1f(uniforms.state, stateIndex[state]);
    gl.uniform1f(uniforms.energy, reactiveEnergy);
    gl.uniform3fv(uniforms.accentDark, palette.accentDark);
    gl.uniform3fv(uniforms.accentMain, palette.accentMain);
    gl.uniform3fv(uniforms.accentLight, palette.accentLight);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    frame = requestAnimationFrame(render);
  };
  frame = requestAnimationFrame(render);

  return {
    setState(nextState: LiveState): void {
      state = nextState;
      energyTarget = stateEnergy[nextState];
      canvas.dataset.state = nextState;
    },
    setAudioLevel(level: number): void {
      audioLevel = Math.max(audioLevel, Math.min(1, Math.max(0, Number(level) || 0)));
    },
    setPalette(name: OrbPaletteName): void {
      palette = palettes[name];
      try {
        localStorage.setItem("javis.app.orbPalette", name);
      } catch {
        // Palette persistence is optional.
      }
    },
    destroy(): void {
      disposed = true;
      cancelAnimationFrame(frame);
      canvas.removeEventListener("pointermove", onPointerMove);
      if (buffer) gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
    },
  };
}
