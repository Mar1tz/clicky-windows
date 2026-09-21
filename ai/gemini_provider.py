"""
The cascade's brain — streaming text answers from Gemini.

This is the fallback path. Normally the user talks to gemini-3.8-live and
never comes through here; this runs when the realtime websocket is
unavailable, and for the text-shaped work that has no audio in it at all:
Code Mode, journal questions, quiz mode, anything typed.

Model choice is per question rather than fixed:

  gemini-3.8-flash        the default, with thinking switched off. A tutor
                          answering "what is this button" does not need a
                          reasoning pass, and skipping it takes roughly half
                          a second off every reply.

  gemini-3.1-pro-preview  when the question asks for judgement or the screen
                          needs real reading — best move, what's wrong with
                          this, read this chart. Slower on purpose.

An explicitly chosen model from the tray always wins over both.
"""

from __future__ import annotations

from typing import AsyncIterator, List, Optional

from ai import gemini
from ai.base_provider import BaseLLMProvider, Message
from ai.gemini import Models
from ai.gemini_vision import wants_deep

DEFAULT_MODEL = Models.VISION_FAST


class GeminiProvider(BaseLLMProvider):
    """Streaming text + vision over the shared pooled connection."""

    def __init__(self):
        # No client of its own: everything goes through ai.gemini's pooled
        # HTTP/2 connection, so a question asked ten minutes after the last
        # one still reuses a warm socket.
        pass

    @staticmethod
    def pick_model(user_text: str, has_screens: bool, override: Optional[str]) -> str:
        if override:
            return override
        return Models.VISION_DEEP if wants_deep(user_text) else Models.VISION_FAST

    async def stream_response(
        self,
        user_text: str,
        screenshots_b64: List[str],
        history: List[Message],
        system_prompt: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        chosen = self.pick_model(user_text, bool(screenshots_b64), model)

        contents: list = []
        for msg in history:
            contents.append({
                "role": "user" if msg.role == "user" else "model",
                "parts": [{"text": msg.content}],
            })

        parts: list = [gemini.image_part(b) for b in screenshots_b64]
        parts.append(gemini.text_part(user_text))
        contents.append({"role": "user", "parts": parts})

        async for chunk in gemini.stream(
            chosen,
            contents,
            system=system_prompt,
            max_tokens=1024,
            temperature=0.7,
            # Pro models refuse a zero budget and think regardless; thinking()
            # drops the field for them rather than sending a 400.
            think=0 if chosen == Models.VISION_FAST else None,
        ):
            yield chunk

    async def health_check(self) -> bool:
        return await gemini.health_check()

    async def close(self) -> None:
        """The manager retires providers by calling this.

        The connection pool is shared and deliberately outlives any one
        provider instance, so this is a no-op — closing it here would throw
        away the warm socket the next question wants. ai.gemini.aclose()
        handles the real teardown at shutdown.
        """
        return None
