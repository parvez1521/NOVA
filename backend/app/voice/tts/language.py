"""Cheap answer-language detection, independent of microphone/STT language."""

import re
from dataclasses import dataclass
from typing import Literal

AnswerLanguage = Literal["en", "hi", "hinglish"]
LanguageMode = Literal["auto", "english", "hindi", "hinglish"]

_HINDI_WORDS = frozenset("main mai mein mera meri mere tum tumhe tumhara aap apka aapka kal aaj kya kaise ho hoon hun hai hain nahi nahin haan bilkul theek thik bhai ji kholo khol karo kar karna karte karta raha rahi chal chalo bata batao bataunga mujhe hum ham ye yeh woh wo ek suna sunao accha achha shukriya namaste kaisa kaisi apne dena laga do".split())
_DISTINCTIVE = frozenset("bhai kholo hoon bilkul theek thik tumhe tumhara bataunga sunao shukriya namaste nahin".split())
_HINDI_WORDS = _HINDI_WORDS | {"yaad", "rakh", "liya", "diya"}
_DISTINCTIVE = _DISTINCTIVE | {"yaad", "rakh", "liya", "diya"}
_AMBIGUOUS = frozenset({"main", "do", "ham", "hum", "ho", "ji", "mai"})
_ENGLISH_WORDS = frozenset("a an the i we you your are is am have can will how what hello done thanks please opened good great code link okay".split())


@dataclass(frozen=True, slots=True)
class LanguageDecision:
    detected_language: AnswerLanguage
    language: AnswerLanguage
    mode: LanguageMode
    uncertain: bool
    method: str

    def as_dict(self) -> dict[str, object]:
        return {
            "detected_answer_language": self.detected_language,
            "answer_language": self.language,
            "tts_language_mode": self.mode,
            "language_uncertain": self.uncertain,
            "language_resolution": self.method,
        }


class LanguageResolver:
    def resolve(self, answer: str, mode: LanguageMode = "auto") -> LanguageDecision:
        devanagari = any(character.isalpha() and "\u0900" <= character <= "\u097f" for character in answer)
        latin = re.findall(r"[a-z]+", answer.casefold())
        hindi_words = {word for word in latin if word in _HINDI_WORDS and word not in _AMBIGUOUS}
        if devanagari:
            # App names like 'Chrome' must not send Devanagari prose to English TTS.
            detected: AnswerLanguage = "hi"
            uncertain, method = False, "devanagari_script"
        elif any(word in _DISTINCTIVE for word in latin) or len(hindi_words) >= 2:
            detected, uncertain, method = "hinglish", True, "roman_hindi_heuristic"
        else:
            detected, uncertain, method = "en", not bool(set(latin) & _ENGLISH_WORDS), "english_default"
        overrides: dict[str, AnswerLanguage] = {"english": "en", "hindi": "hi", "hinglish": "hinglish"}
        return LanguageDecision(detected, overrides.get(mode, detected), mode, uncertain, "configured_override" if mode != "auto" else method)
