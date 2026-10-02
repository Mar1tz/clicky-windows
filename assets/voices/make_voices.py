"""Records a short hello in each of Tora's voices, for Make Tora yours.

    python assets/voices/make_voices.py

Once, by whoever releases: the clips ship with the app, so choosing a voice
in the first launch never spends the user's talk messages. Needs a working
Gemini key or HeyTora sign-in on this machine; refuses to write anything if
Gemini's voice is unavailable (it would record the Windows fallback voice).
"""
import asyncio
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LINE = "Hi, I'm Tora. What are we working on today?"


async def main() -> int:
    from audio.tts.gemini_tts import VOICES, GeminiTTS
    here = Path(__file__).parent
    for voice in VOICES:
        if (here / f"{voice.lower()}.wav").exists():
            continue                                  # recorded already
        tts = GeminiTTS(voice)
        if not tts.gemini_ok():
            print("Gemini's voice is not available here - nothing recorded.")
            return 1
        pcm, rate = await tts.synthesize(LINE)
        if not pcm or not tts.gemini_ok():
            print(f"{voice}: failed - nothing written")
            return 1
        out = here / f"{voice.lower()}.wav"
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        print(f"{out.name}  {len(pcm) / 2 / rate:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
