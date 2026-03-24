/* ================= IMPORTS ================= */

import * as THREE from "three";
import { GLTFLoader } from "https://cdn.jsdelivr.net/npm/three@0.150.1/examples/jsm/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "https://cdn.jsdelivr.net/npm/@pixiv/three-vrm@1.0.0/lib/three-vrm.module.js";
import { io } from "https://cdn.socket.io/4.7.2/socket.io.esm.min.js";

/* ================= SOCKET ================= */

const socket = io("http://localhost:8000", {
  transports: ["websocket"]
});

socket.on("connect", () => {
  console.log("✅ Connected to backend");
});

socket.on("connect_error", () => {
  console.log("⚠ Backend not running.");
  showStatusText("⚠ Cannot connect to backend.");
});

/* ================= GLOBAL STATE ================= */

let currentState = "idle";
let currentEmotion = "neutral";

let currentViseme = "aa";
let targetValue = 0;
let currentValue = 0;

let systemStarted = false;
let speaking = false;
let silenceTimer = null;

// FIX: single shared AudioContext — creating multiple causes "AudioContext was not allowed to start" errors
let audioContext = null;
let analyser = null;

// FIX: audio element reuse — re-creating MediaElementSource on the same element throws InvalidStateError
let audioElement = null;
let audioSourceNode = null;

let vrm = null;

let blinkTimer = 0;
let nextBlinkTime = Math.random() * 3 + 2;

let thinkingTilt = 0;

let gazeTargetX = 0;
let gazeTargetY = 0;
let gazeCurrentX = 0;
let gazeCurrentY = 0;
let nextGazeChange = 0;

/* ================= STATUS UI ================= */
// FIX: added a simple on-screen status label so users know what state the system is in

const statusEl = document.createElement("div");
statusEl.style.cssText = `
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  color: rgba(255,255,255,0.7);
  font-family: Arial, sans-serif;
  font-size: 14px;
  letter-spacing: 1px;
  pointer-events: none;
  text-transform: uppercase;
  transition: opacity 0.4s;
`;
statusEl.textContent = "Click anywhere to start";
document.body.appendChild(statusEl);

function showStatusText(text) {
  statusEl.textContent = text;
}

/* ================= EMOTION ================= */

function applyEmotion() {
  if (!vrm) return;
  const emotions = ["happy", "angry", "sad", "relaxed", "surprised"];
  emotions.forEach(e => vrm.expressionManager.setValue(e, 0));
  if (currentEmotion && emotions.includes(currentEmotion)) {
    vrm.expressionManager.setValue(currentEmotion, 0.4);
  }
}

/* ================= VISEME MAP ================= */

function getVisemeFromChar(char) {
  char = char.toLowerCase();
  if (char === "a") return "aa";
  if (char === "e") return "ee";
  if (char === "i") return "ih";
  if (char === "o") return "oh";
  if (char === "u") return "ou";
  if (["m", "b", "p"].includes(char)) return "aa";
  return null;
}

let visemeSequence = [];
let visemeIndex = 0;
let visemeInterval = null;

/* ================= MIC STREAMING ================= */

async function startMic() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true
    }
  });

  // FIX: reuse the shared audioContext instead of creating a new inputContext
  const source = audioContext.createMediaStreamSource(stream);
  const processor = audioContext.createScriptProcessor(8192, 1, 1);

  source.connect(processor);
  processor.connect(audioContext.destination);

  processor.onaudioprocess = (event) => {
    // Don't capture mic while AI is thinking or speaking
    if (currentState === "thinking" || currentState === "speaking") return;

    const inputData = event.inputBuffer.getChannelData(0);

    let volume = 0;
    for (let i = 0; i < inputData.length; i++) {
      volume += Math.abs(inputData[i]);
    }
    volume /= inputData.length;

    const downsampled = downsampleBuffer(inputData, audioContext.sampleRate, 16000);
    const pcmData = convertFloatToInt16(downsampled);

    if (volume > 0.015) {
      speaking = true;
      showStatusText("🎤 Listening...");
      socket.emit("audio_chunk", pcmData);

      if (silenceTimer) {
        clearTimeout(silenceTimer);
        silenceTimer = null;
      }

    } else if (speaking) {
      socket.emit("audio_chunk", pcmData);

      if (!silenceTimer) {
        silenceTimer = setTimeout(() => {
          speaking = false;
          silenceTimer = null;
          showStatusText("⏳ Thinking...");
          console.log("🛑 Speech ended");
        }, 1200);
      }
    }
  };

  console.log("🎤 Mic started");
}

/* ================= SOCKET EVENTS ================= */

// FIX: "heard" event is emitted by the frontend itself, not the backend —
// the backend never emits "heard". This handler was dead code causing
// currentState to get stuck on "thinking" from a manual_response that
// the backend also never handled. Removed entirely.

socket.on("ai_response", (data) => {
  console.log("🤖 Responded:", data.text);

  currentState = "speaking";
  currentEmotion = data.emotion || "neutral";
  showStatusText("🗣 Speaking...");

  // Build viseme sequence from response text
  visemeSequence = [];
  for (let char of data.text) {
    const v = getVisemeFromChar(char);
    if (v) visemeSequence.push(v);
  }
  visemeIndex = 0;

  playAudio(data.audio_url);
});

/* ================= AUDIO PLAYBACK ================= */

// FIX: reuse a single audio element and source node to avoid
// InvalidStateError from calling createMediaElementSource twice
// on the same HTMLAudioElement

async function playAudio(url) {
  if (!audioContext) return;

  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }

  // If no audio element yet, create it once
  if (!audioElement) {
    audioElement = new Audio();
    audioElement.crossOrigin = "anonymous";

    audioSourceNode = audioContext.createMediaElementSource(audioElement);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;

    audioSourceNode.connect(analyser);
    analyser.connect(audioContext.destination);
  } else {
    // Stop current playback before changing src
    audioElement.pause();
  }

  audioElement.src = url;

  // FIX: set viseme interval inside canplaythrough, not onloadedmetadata
  // onloadedmetadata fires before duration is reliably available in all browsers
  audioElement.oncanplaythrough = () => {
    if (visemeInterval) clearInterval(visemeInterval);

    const totalDuration = audioElement.duration;
    if (!visemeSequence.length || !totalDuration) return;

    const stepTime = (totalDuration / visemeSequence.length) * 1000;

    visemeInterval = setInterval(() => {
      if (visemeIndex >= visemeSequence.length) {
        clearInterval(visemeInterval);
        visemeInterval = null;
        return;
      }
      currentViseme = visemeSequence[visemeIndex];
      visemeIndex++;
    }, stepTime);
  };

  audioElement.onended = () => {
    currentState = "cooldown";
    targetValue = 0;
    showStatusText("Ready");

    if (visemeInterval) {
      clearInterval(visemeInterval);
      visemeInterval = null;
    }

    setTimeout(() => {
      currentState = "idle";
    }, 800);
  };

  audioElement.play().catch(err => console.error("Audio play error:", err));
}

/* ================= HELPER FUNCTIONS ================= */

function downsampleBuffer(buffer, inputRate, outputRate) {
  if (outputRate === inputRate) return buffer;

  const ratio = inputRate / outputRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);

  let offsetResult = 0;
  let offsetBuffer = 0;

  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    result[offsetResult] = accum / count;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }

  return result;
}

function convertFloatToInt16(buffer) {
  const l = buffer.length;
  const buf = new Int16Array(l);
  for (let i = 0; i < l; i++) {
    let s = Math.max(-1, Math.min(1, buffer[i]));
    buf[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return buf.buffer;
}

/* ================= THREE SETUP ================= */

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x000000);

const camera = new THREE.PerspectiveCamera(
  30,
  window.innerWidth / window.innerHeight,
  0.1,
  100
);
camera.position.set(0, 1.5, 2.2);
camera.lookAt(0, 1.45, 0);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);

// FIX: added window resize handler — without this the canvas stays the
// original size if the window is resized, distorting the avatar
window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

scene.add(new THREE.AmbientLight(0xffffff, 0.35));

const keyLight = new THREE.DirectionalLight(0xffffff, 0.9);
keyLight.position.set(1, 3, 3);
scene.add(keyLight);

const fillLight = new THREE.PointLight(0xffffff, 0.6);
fillLight.position.set(-1, 1.5, 2);
scene.add(fillLight);

const loader = new GLTFLoader();
loader.register(parser => new VRMLoaderPlugin(parser));

loader.load(
  "./armholo1.vrm",
  (gltf) => {
    vrm = gltf.userData.vrm;
    VRMUtils.removeUnnecessaryJoints(vrm.scene);

    vrm.scene.traverse((obj) => {
      if (!obj.isMesh) return;
      const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
      materials.forEach((material) => {
        const name = material.name?.toLowerCase() || "";
        const isSkin =
          name.includes("body") ||
          name.includes("face") ||
          name.includes("skin") ||
          name.includes("head");
        if (!isSkin) return;
        material.color.setRGB(0.72, 0.52, 0.45);
        material.needsUpdate = true;
      });
    });

    scene.add(vrm.scene);
    vrm.scene.rotation.y = Math.PI;
    console.log("✅ VRM Loaded");
  },
  undefined,
  // FIX: added error callback — without this, a missing VRM file fails silently
  (error) => {
    console.error("❌ Failed to load VRM:", error);
    showStatusText("❌ Avatar failed to load.");
  }
);

/* ================= VISEME ================= */

function setViseme(name, value) {
  currentViseme = name;
  targetValue = value;
}

/* ================= ANIMATION LOOP ================= */

const clock = new THREE.Clock();

function animate() {
  requestAnimationFrame(animate);

  const delta = clock.getDelta();
  const t = clock.elapsedTime;

  if (vrm) {
    vrm.update(delta);

    const head = vrm.humanoid?.getNormalizedBoneNode("head");
    const spine = vrm.humanoid?.getNormalizedBoneNode("spine");

    // Reset rotations each frame (lerp toward zero)
    if (head) {
      head.rotation.x *= 0.9;
      head.rotation.y *= 0.9;
      head.rotation.z *= 0.9;
    }
    if (spine) {
      spine.rotation.x *= 0.9;
    }

    /* ── THINKING ── */
    if (currentState === "thinking") {
      thinkingTilt += (0.25 - thinkingTilt) * 0.05;
      if (head) head.rotation.z = thinkingTilt;

      vrm.expressionManager.setValue("relaxed", 0.35);

      if (t > nextGazeChange) {
        gazeTargetX = (Math.random() - 0.5) * 0.25;
        gazeTargetY = -0.05 + Math.random() * 0.08;
        nextGazeChange = t + (Math.random() * 2 + 1);
      }

      gazeCurrentX += (gazeTargetX - gazeCurrentX) * 0.05;
      gazeCurrentY += (gazeTargetY - gazeCurrentY) * 0.05;

      if (head) {
        head.rotation.y = THREE.MathUtils.clamp(gazeCurrentX, -0.35, 0.35);
        head.rotation.x = THREE.MathUtils.clamp(gazeCurrentY, -0.25, 0.25);
      }
    } else {
      // FIX: reset thinkingTilt when leaving thinking state so head doesn't stay tilted
      thinkingTilt += (0 - thinkingTilt) * 0.05;
    }

    /* ── SPEAKING ── */
    if (currentState === "speaking") {
      if (head) {
        head.rotation.y = Math.sin(t * 3) * 0.08;
        head.rotation.x = Math.sin(t * 4) * 0.03;
      }
    }

    /* ── COOLDOWN ── */
    if (currentState === "cooldown") {
      if (head) {
        head.rotation.x *= 0.95;
        head.rotation.y *= 0.95;
        head.rotation.z *= 0.95;
      }
      if (spine) spine.rotation.x *= 0.95;
      vrm.expressionManager.setValue("relaxed", 0.2);
    }

    /* ── LISTENING ── */
    if (currentState === "listening") {
      if (head) head.rotation.z = 0.15;
      if (spine) spine.rotation.x = 0.05;
      vrm.expressionManager.setValue("relaxed", 0.3);
    }

    /* ── BLINK ── */
    blinkTimer += delta;
    if (blinkTimer > nextBlinkTime) {
      vrm.expressionManager.setValue("blink", 1);
      setTimeout(() => {
        if (vrm) vrm.expressionManager.setValue("blink", 0);
      }, 120);
      blinkTimer = 0;
      nextBlinkTime = Math.random() * 3 + 2;
    }

    /* ── EMOTION ── */
    applyEmotion();

    /* ── VISEME ── */
    const smoothSpeed = 0.15;
    currentValue += (targetValue - currentValue) * smoothSpeed;

    ["aa", "ee", "ih", "oh", "ou"].forEach(v => {
      vrm.expressionManager.setValue(v, 0);
    });
    vrm.expressionManager.setValue(currentViseme, currentValue);

    /* ── BREATHING (idle only) ── */
    if (currentState === "idle") {
      vrm.scene.position.y = Math.sin(t * 1.2) * 0.03;
    }

    /* ── AUDIO AMPLITUDE → LIP SYNC ── */
    if (analyser && currentState === "speaking") {
      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteFrequencyData(dataArray);
      const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length / 255;
      targetValue = Math.min(avg * 3, 1);
    }
  }

  renderer.render(scene, camera);
}

animate();

/* ================= START SYSTEM ================= */

window.addEventListener("click", async () => {
  if (systemStarted) return;
  systemStarted = true;

  // FIX: create AudioContext here, inside a user gesture, to comply with
  // browser autoplay policy — creating it at the top of the file before
  // a user interaction causes it to start in "suspended" state permanently
  // on some browsers (especially Chrome)
  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  await audioContext.resume();

  showStatusText("Starting...");
  await startMic();
  showStatusText("Ready");

  console.log("🚀 System started");
});