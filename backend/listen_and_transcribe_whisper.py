import asyncio
import logging
import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# Load model once
model = WhisperModel(
    "distil-large-v3",
    device="cuda",
    compute_type="float16"
)

# Minimum RMS energy to bother transcribing
MIN_ENERGY_THRESHOLD = 0.004

audio_buffer = bytearray()


def flush_buffer():
    """Discard any audio accumulated while the AI was speaking/thinking."""
    global audio_buffer
    audio_buffer.clear()
    print("🗑️  Audio buffer flushed")


async def process_audio(data):
    global audio_buffer

    if not data:
        return None

    audio_buffer.extend(data)

    # Wait until we have ~5 sec of audio to avoid cutting off speech
    if len(audio_buffer) < SAMPLE_RATE * 2 * 4:
        return None

    audio_bytes = bytes(audio_buffer)
    audio_buffer.clear()

    audio_array = (
        np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    )

    # ── LAYER 1: RMS energy filter ──
    # Skip transcription entirely if audio is too quiet (silence / background noise)
    rms = np.sqrt(np.mean(audio_array**2))
    if rms < MIN_ENERGY_THRESHOLD:
        logger.info(f"Skipping transcription: audio too quiet (RMS: {rms:.5f})")
        return None

    loop = asyncio.get_event_loop()

    def run():
        segments, info = model.transcribe(
            audio_array,
            beam_size=5,                  # was 1 — higher = more accurate
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
        )

        # ── LAYER 2: Language confidence gate ──
        # Reject if Whisper isn't confident this is even English
        if info.language_probability < 0.88:
            logger.info(f"Low language probability: {info.language_probability:.3f}")
            return None

        valid_text = []

        for segment in segments:
            # ── LAYER 3: Per-segment quality filters ──

            # Reject segments where Whisper thinks there's no real speech
            if segment.no_speech_prob > 0.6:
                continue

            # Reject segments with very low confidence overall
            if segment.avg_logprob < -1.0:
                continue

            # Reject very short segments (usually noise bursts)
            if (segment.end - segment.start) < 0.5:
                continue

            valid_text.append(segment.text)

        return " ".join(valid_text).strip().lower() if valid_text else None

    result = await loop.run_in_executor(None, run)

    if result:
        print("🎤 FINAL:", result)

    return result