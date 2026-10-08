"""Speech-only visual/structured-content cleanup with bounded streaming state."""

import html
import re

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\uFE0E\uFE0F\u200D\u20E3"
    "\u00A9\u00AE\u203C\u2049\u2122\u2139\u25AA\u25AB\u25B6\u25C0\u25FB-\u25FE"
    "\u2934\u2935\u2B05-\u2B07\u2B1B\u2B1C\u2B50\u2B55\u3030\u303D\u3297\u3299"
    "\U000E0020-\U000E007F]"
)
_MARKERS = re.compile(r"```|~~~|https?://|www\.|[<{\[\]`*#&]", re.I)
_PREFIXES = ("```", "~~~", "https://", "http://", "www.")
_PRIVATE_TAGS = frozenset({"think", "thinking", "analysis", "reasoning", "tool", "tool_call", "tool_calls", "tool_result", "tool_response", "function_call", "event", "metadata", "debug", "script", "style"})


def _visual_cleanup(text: str) -> str:
    text = re.sub(r"[0-9#*][\ufe0e\ufe0f]?\u20e3", "", text)
    return _EMOJI.sub("", text)


class SpeechTextCleaner:
    """Use clean() for complete sentences and feed()/flush() for streamed answers.

    Only ambiguous syntax is held; plain text passes immediately. Code/JSON/
    private blocks are discarded incrementally, so a long block is never stored
    or split into spoken lines. This never modifies the visible answer.
    """

    def __init__(self, *, read_urls: bool = False) -> None:
        self.read_urls = read_urls
        self.pending = ""
        self.block_end: str | None = None
        self.json_closers: list[str] = []
        self.json_string = False
        self.json_escape = False

    @classmethod
    def clean(cls, text: str, *, read_urls: bool = False) -> str:
        parser = cls(read_urls=read_urls)
        result = parser.feed(html.unescape(text)) + parser.flush()
        result = _visual_cleanup(result)
        result = re.sub(r"(?<!\w)_{1,2}(?=\w)|(?<=\w)_{1,2}(?!\w)|~~", "", result)
        result = re.sub(r"(?m)^\s*(?:[-+>]\s+|\d+[.)]\s+)", "", result)
        result = re.sub(r"(?im)^\s*(?:request_id|tool_arguments|event_type|provider|queue_length)\s*:.*$", "", result)
        result = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", result)
        result = re.sub(r"\s+", " ", result)
        result = re.sub(r"\s+([.,!?;:।])", r"\1", result).strip()
        # Visual-only content (including punctuation left around an emoji) is silent.
        return result if any(character.isalnum() for character in result) else ""

    def feed(self, token: str) -> str:
        self.pending += token
        return self._drain(final=False)

    def flush(self) -> str:
        result = self._drain(final=True)
        self.pending = ""
        return result

    def _drain(self, *, final: bool) -> str:
        output: list[str] = []
        while self.pending:
            if self.block_end:
                index = self.pending.lower().find(self.block_end)
                if index < 0:
                    self.pending = self.pending[-(len(self.block_end) - 1):] if not final else ""
                    break
                self.pending = self.pending[index + len(self.block_end):]
                self.block_end = None
                continue
            if self.json_closers:
                character, self.pending = self.pending[0], self.pending[1:]
                if self.json_string:
                    if self.json_escape:
                        self.json_escape = False
                    elif character == "\\":
                        self.json_escape = True
                    elif character == '"':
                        self.json_string = False
                elif character == '"':
                    self.json_string = True
                elif character in "{[":
                    self.json_closers.append("}" if character == "{" else "]")
                elif character == self.json_closers[-1]:
                    self.json_closers.pop()
                continue
            if self.pending.startswith(("```", "~~~")):
                self.block_end = self.pending[:3]
                self.pending = self.pending[3:]
                output.append(" I've displayed the code on screen. ")
                continue
            if not final and (self.pending.lower() in _PREFIXES or self.pending in {"`", "``", "~", "~~"}):
                break
            if self.pending.startswith("[["):
                self.pending = self.pending[2:]
                self.block_end = "]]"
                continue
            if self.pending.startswith("{"):
                self.json_closers = ["}"]
                self.pending = self.pending[1:]
                continue
            if self.pending.startswith("["):
                if len(self.pending) == 1 and not final:
                    break
                after = self.pending[1:].lstrip()
                if after and (after[:1] in '{["0123456789-' or re.match(r"(?:true|false|null)\b", after)):
                    self.json_closers = ["]"]
                    self.pending = self.pending[1:]
                    continue
                end = self.pending.find("]")
                if end < 0 or (end == len(self.pending) - 1 and not final):
                    if not final and len(self.pending) < 4096:
                        break
                    self.pending = ""
                    continue
                label = type(self).clean(self.pending[1:end], read_urls=self.read_urls)
                if self.pending[end + 1:end + 2] == "(":
                    close = self.pending.find(")", end + 2)
                    if close < 0:
                        if not final and len(self.pending) < 4096:
                            break
                        self.pending = ""
                        continue
                    url = self.pending[end + 2:close]
                    output.append(label + (f" {url}" if self.read_urls else ""))
                    self.pending = self.pending[close + 1:]
                else:
                    output.append(label)
                    self.pending = self.pending[end + 1:]
                continue
            if self.pending.startswith("&"):
                entity = re.match(r"&(?:#\d+|#x[0-9a-f]+|[a-z]+);", self.pending, re.I)
                if entity:
                    decoded = html.unescape(entity.group())
                    if decoded != entity.group():
                        self.pending = decoded + self.pending[entity.end():]
                        continue
                if not final and re.fullmatch(r"&[#a-zA-Z0-9]*", self.pending) and len(self.pending) < 32:
                    break
                output.append("&")
                self.pending = self.pending[1:]
                continue
            if self.pending.startswith("<"):
                if len(self.pending) == 1 and not final:
                    break
                if not re.match(r"</?[a-zA-Z!]", self.pending):
                    output.append("<")
                    self.pending = self.pending[1:]
                    continue
                end = self.pending.find(">")
                if end < 0:
                    if not final and len(self.pending) < 512:
                        break
                    self.pending = ""
                    continue
                tag = self.pending[1:end].strip().lower()
                name = re.split(r"\s+", tag)[0].strip("/")
                self.pending = self.pending[end + 1:]
                if name in _PRIVATE_TAGS and not tag.startswith("/") and not tag.endswith("/"):
                    self.block_end = f"</{name}>"
                continue
            if re.match(r"(?:https?://|www\.)", self.pending, re.I):
                match = re.search(r"\s", self.pending)
                if not match and not final and len(self.pending) < 4096:
                    break
                end = match.start() if match else len(self.pending)
                url = self.pending[:end]
                suffix = re.search(r"[.,!?;:।)]+$", url)
                punctuation = suffix.group() if suffix else ""
                output.append(url if self.read_urls else "the link" + punctuation)
                self.pending = self.pending[end:]
                continue
            if self.pending[:1] in "`*#]":
                self.pending = self.pending[1:]
                continue
            marker = _MARKERS.search(self.pending)
            end = marker.start() if marker else len(self.pending)
            if end == 0:
                # Partial prefix: e.g. '<' or the first chunk of 'https://'.
                end = 1
            if not marker and not final:
                if keycap_prefix := re.search(r"[0-9][\ufe0e\ufe0f]?$", self.pending):
                    end = min(end, keycap_prefix.start())
                for prefix in _PREFIXES:
                    for length in range(1, min(len(prefix), end + 1)):
                        if self.pending[-length:].lower() == prefix[:length]:
                            end = min(end, len(self.pending) - length)
            if end == 0:
                break
            output.append(_visual_cleanup(self.pending[:end]))
            self.pending = self.pending[end:]
        return "".join(output)
