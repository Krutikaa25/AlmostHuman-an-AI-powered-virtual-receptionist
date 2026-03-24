import asyncio
import numpy as np
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000

# Load model once
model = WhisperModel(
    "distil-large-v3",
    device="cuda",
    compute_type="float16"
)

audio_buffer = bytearray()


async def process_audio(data):

    global audio_buffer

    if not data:
        return None

    audio_buffer.extend(data)

    # Wait until we have ~5.5 sec of audio to avoid cutting off speech
    if len(audio_buffer) < SAMPLE_RATE * 2 * 5.5:
        return None

    audio_bytes = bytes(audio_buffer)
    audio_buffer.clear()

    audio_array = (
        np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    )

    loop = asyncio.get_event_loop()

    def run():
        segments, _ = model.transcribe(
            audio_array,
            beam_size=1,
            vad_filter=True
        )

        text = ""
        for seg in segments:
            text += seg.text

        return text.strip().lower()

    result = await loop.run_in_executor(None, run)

    if result:
        print("🎤 FINAL:", result)

    return result