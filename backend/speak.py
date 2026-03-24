import os
import numpy as np
import soundfile as sf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

AUDIO_FILE = os.path.join(STATIC_DIR, "output.wav")

MODEL_PATH = "../piper_models/en_us-lessac-medium.onnx"
CONFIG_PATH = "../piper_models/en_us-lessac-medium.onnx.json"

# FIX: wrap model load in try/except with a clear error message
try:
    from piper.voice import PiperVoice
    voice = PiperVoice.load(
        model_path=MODEL_PATH,
        config_path=CONFIG_PATH
    )
    print("✅ Piper TTS model loaded successfully")
except FileNotFoundError as e:
    print(f"❌ Piper model file not found: {e}")
    print(f"   Expected model at: {MODEL_PATH}")
    print(f"   Expected config at: {CONFIG_PATH}")
    voice = None
except Exception as e:
    print(f"❌ Failed to load Piper TTS model: {e}")
    voice = None


def speak(text: str):
    if not text.strip():
        return

    if voice is None:
        print("⚠️  TTS skipped — Piper model not loaded.")
        return

    audio_chunks = []
    sample_rate = None

    for chunk in voice.synthesize(text):
        audio_chunks.extend(chunk.audio_float_array)
        sample_rate = chunk.sample_rate

    audio = np.array(audio_chunks, dtype=np.float32)
    sf.write(AUDIO_FILE, audio, sample_rate)
    print("🔊 Audio written to static/output.wav")