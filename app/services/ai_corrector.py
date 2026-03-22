"""
ai_corrector.py
Two-tier word correction + emoji suggestion for EyeTyper.

Tier 1 — Local (offline, instant):
    pyspellchecker — fast dictionary-based correction
    Returns up to 3 spelling candidates

Tier 2 — Cloud (online, contextual):
    LanguageTool public API — free, no API key required
    Sends the full typed sentence for grammar + spelling context
    Returns corrected word in context

Emoji suggestions:
    Built-in keyword→emoji map (no API needed, always offline)
    Triggered when the current word matches an emotion/concept keyword

Install:
    pip install pyspellchecker requests

Usage:
    corrector = AiCorrector()
    suggestions = corrector.suggest("helo")   # ["hello", "help", "helo"]
    emojis      = corrector.emoji_suggest("helo")  # []
    corrector.correct_async("helo", callback)  # fires cloud correction async
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

# ── local spellcheck ──────────────────────────────────────────────────────────
try:
    from spellchecker import SpellChecker
    _SPELL_OK = True
except ImportError:
    _SPELL_OK = False
    print("[Corrector] pyspellchecker not installed — local correction disabled")

# ── cloud correction ──────────────────────────────────────────────────────────
try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    print("[Corrector] requests not installed — cloud correction disabled")

# ── emoji map ─────────────────────────────────────────────────────────────────
_EMOJI_MAP: dict[str, list[str]] = {
    # emotions
    "happy":     ["😊", "😄", "🎉"],
    "sad":       ["😢", "😔", "💔"],
    "love":      ["❤️",  "😍", "💕"],
    "angry":     ["😠", "🔥", "💢"],
    "laugh":     ["😂", "🤣", "😆"],
    "cry":       ["😭", "😢", "💧"],
    "cool":      ["😎", "🆒", "✨"],
    "good":      ["👍", "✅", "😊"],
    "bad":       ["👎", "❌", "😔"],
    "yes":       ["✅", "👍", "🙌"],
    "no":        ["❌", "👎", "🚫"],
    "ok":        ["👌", "✅", "👍"],
    "thanks":    ["🙏", "😊", "💙"],
    "thank":     ["🙏", "😊", "💙"],
    "please":    ["🙏", "💙", "😊"],
    "sorry":     ["😔", "🙏", "💔"],
    "hello":     ["👋", "😊", "🙌"],
    "hi":        ["👋", "😊", "✨"],
    "bye":       ["👋", "😊", "💙"],
    "help":      ["🆘", "🙏", "💙"],
    "great":     ["🎉", "👍", "✨"],
    "work":      ["💼", "💪", "🖥️"],
    "home":      ["🏠", "🏡", "❤️"],
    "food":      ["🍕", "😋", "🍽️"],
    "water":     ["💧", "🥤", "🌊"],
    "sleep":     ["😴", "💤", "🌙"],
    "pain":      ["😣", "🤕", "💊"],
    "doctor":    ["👨‍⚕️", "🏥", "💊"],
    "hot":       ["🔥", "☀️", "😅"],
    "cold":      ["🥶", "❄️", "🌨️"],
    "music":     ["🎵", "🎶", "🎧"],
    "phone":     ["📱", "☎️", "📞"],
    "money":     ["💰", "💵", "🤑"],
    "time":      ["⏰", "🕐", "⌚"],
    "fire":      ["🔥", "🚨", "⚠️"],
    "star":      ["⭐", "🌟", "✨"],
    "heart":     ["❤️",  "💙", "💕"],
    "sun":       ["☀️",  "🌞", "🌤️"],
    "rain":      ["🌧️", "☔", "💧"],
    "car":       ["🚗", "🚙", "🏎️"],
    "cat":       ["🐱", "🐈", "😺"],
    "dog":       ["🐶", "🐕", "🦴"],
    "book":      ["📚", "📖", "✏️"],
    "computer":  ["💻", "🖥️", "⌨️"],
    "game":      ["🎮", "🕹️", "🎯"],
    "sport":     ["⚽", "🏀", "🏃"],
    "coffee":    ["☕", "😊", "🍵"],
    "birthday":  ["🎂", "🎉", "🎈"],
    "tomorrow":  ["📅", "⏰", "🌅"],
    "today":     ["📅", "🕐", "✅"],
    "urgent":    ["🚨", "⚠️", "🆘"],
    "question":  ["❓", "🤔", "💭"],
    "idea":      ["💡", "🤔", "✨"],
    "stop":      ["🛑", "⛔", "❌"],
    "go":        ["✅", "🚀", "👍"],
    "wait":      ["⏳", "🕐", "✋"],
    "look":      ["👀", "🔍", "👁️"],
    "hear":      ["👂", "🔊", "🎵"],
    "eat":       ["🍽️", "😋", "🥘"],
    "drink":     ["🥤", "💧", "🍵"],
}

_LANGUAGETOOL_URL = "https://api.languagetool.org/v2/check"
_CLOUD_TIMEOUT    = 3.0   # seconds
_MIN_WORD_LEN     = 3     # don't correct very short words


class AiCorrector:
    """
    Two-tier word corrector with emoji suggestions.
    Thread-safe — cloud correction runs in background thread.
    """

    def __init__(self, language: str = "en-US") -> None:
        self._language = language
        self._spell = None
        if _SPELL_OK:
            try:
                self._spell = SpellChecker(language="en")
            except Exception as e:
                print(f"[Corrector] SpellChecker init failed: {e} — local correction disabled")
        self._pending: Optional[threading.Thread] = None
        self._last_cloud_req: float = 0.0
        self._cloud_cooldown: float = 1.5   # minimum seconds between cloud calls

    # ── public API ────────────────────────────────────────────────────────────

    def suggest(self, word: str) -> list[str]:
        """
        Instant local suggestions for `word`.
        Returns up to 3 candidates, or [] if word looks correct.
        """
        if not self._spell or len(word) < _MIN_WORD_LEN:
            return []
        word_lower = word.lower()
        if word_lower in self._spell:
            return []   # already correct
        candidates = self._spell.candidates(word_lower)
        if not candidates:
            return []
        # Sort by edit distance (SpellChecker already does this implicitly)
        return list(candidates)[:3]

    def emoji_suggest(self, word: str) -> list[str]:
        """Return emoji suggestions for a word, or [] if none match."""
        return _EMOJI_MAP.get(word.lower(), [])

    def correct_async(
        self,
        sentence:  str,
        on_result: Callable[[list[str]], None],
    ) -> None:
        """
        Fire-and-forget cloud correction of the full sentence.
        Calls `on_result(suggestions)` on the main thread when done.
        Skips if a request is already in flight or cooldown hasn't passed.
        """
        if not _REQUESTS_OK:
            return
        now = time.time()
        if now - self._last_cloud_req < self._cloud_cooldown:
            return
        if self._pending and self._pending.is_alive():
            return
        self._last_cloud_req = now
        self._pending = threading.Thread(
            target=self._cloud_request,
            args=(sentence, on_result),
            daemon=True,
        )
        self._pending.start()

    def set_language(self, lang: str) -> None:
        """Switch correction language (e.g. 'fr', 'ar')."""
        self._language = lang
        if _SPELL_OK:
            try:
                self._spell = SpellChecker(language=lang.split("-")[0])
            except Exception:
                pass   # language not available locally, cloud still works

    # ── cloud correction ──────────────────────────────────────────────────────

    def _cloud_request(
        self,
        sentence:  str,
        on_result: Callable[[list[str]], None],
    ) -> None:
        try:
            resp = _requests.post(
                _LANGUAGETOOL_URL,
                data={
                    "text":     sentence,
                    "language": self._language,
                },
                timeout=_CLOUD_TIMEOUT,
            )
            if resp.status_code != 200:
                return

            data    = resp.json()
            matches = data.get("matches", [])
            suggestions: list[str] = []

            for m in matches:
                for rep in m.get("replacements", [])[:2]:
                    val = rep.get("value", "").strip()
                    if val and val not in suggestions:
                        suggestions.append(val)
                if len(suggestions) >= 3:
                    break

            if suggestions:
                on_result(suggestions)

        except Exception as e:
            print(f"[Corrector] Cloud request failed: {e}")