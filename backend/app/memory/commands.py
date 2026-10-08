import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MemoryCommand:
    action: Literal["remember", "forget", "clear", "view", "preference"]
    content: str = ""


class MemoryCommandParser:
    @staticmethod
    def normalize(text: str) -> str:
        text = re.sub(r"^(?:hey\s+)?(?:nova|नोवा)[\s,:.!-]+", "", text.strip(), flags=re.I)
        return re.sub(r"^actually[\s,]+", "", text, flags=re.I).strip()

    def parse(self, text: str) -> MemoryCommand | None:
        text = self.normalize(text).strip().rstrip(".!?।")
        if re.fullmatch(r"(?:forget everything (?:you remember|you know|saved) about me|(?:delete|clear|forget|remove) all (?:my |saved |the )?memories)", text, re.I):
            return MemoryCommand("clear")
        if re.fullmatch(r"(?:what do you (?:remember|know) about me|(?:show|list|view) (?:all )?(?:my |saved )?memories)", text, re.I):
            return MemoryCommand("view")
        if re.fullmatch(r"(?:how should you (?:reply|respond)(?: to me)?|(?:what|which) language (?:do I prefer|should you (?:reply|respond)(?: in| to me)?)|what is my (?:response language|reply language|language) preference)", text, re.I):
            return MemoryCommand("preference")
        if match := re.match(r"^(?:remember|don['’]t forget|do not forget|save this|save (?:to|in) memory|keep in mind)(?:\s+(?:that|this))?[\s:,-]+(.+)$", text, re.I | re.S):
            return MemoryCommand("remember", match[1].strip())
        if match := re.match(r"^forget(?:\s+that)?[\s:,-]+(.+)$", text, re.I | re.S):
            return MemoryCommand("forget", match[1].strip())
        if match := re.match(r"^remove\s+(.+?)\s+from (?:my )?memory$", text, re.I | re.S):
            return MemoryCommand("forget", match[1].strip())
        if match := re.match(r"^remove that from memory[\s:,-]+(.+)$", text, re.I | re.S):
            return MemoryCommand("forget", match[1].strip())
        return None
