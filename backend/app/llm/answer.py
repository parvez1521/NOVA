"""Incremental suppression of tagged reasoning, including split stream chunks."""


class AnswerStreamFilter:
    """Separate fields are ignored by providers; this guards inline <think> tags.

    Normal content is returned immediately. Only a possible partial tag is held
    across chunks. Reasoning is never buffered, logged, or exposed to the agent.
    """

    def __init__(self) -> None:
        self.pending = ""
        self.in_thinking = False

    def feed(self, chunk: str) -> str:
        self.pending += chunk
        output: list[str] = []
        while self.pending:
            marker = "</think>" if self.in_thinking else "<think>"
            index = self.pending.lower().find(marker)
            if index >= 0:
                if not self.in_thinking:
                    output.append(self.pending[:index])
                self.pending = self.pending[index + len(marker):]
                self.in_thinking = not self.in_thinking
                continue
            keep = 0
            for length in range(1, min(len(marker), len(self.pending) + 1)):
                if self.pending[-length:].lower() == marker[:length]:
                    keep = length
            safe = self.pending[:-keep] if keep else self.pending
            if not self.in_thinking:
                output.append(safe)
            self.pending = self.pending[-keep:] if keep else ""
            break
        return "".join(output)

    def flush(self) -> str:
        # A trailing incomplete tag is discarded, never shown as a raw marker.
        self.pending = ""
        return ""
