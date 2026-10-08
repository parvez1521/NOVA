from app.database.repository import search_terms
from app.memory.repository import MemoryRepository

_STOP = frozenset("user i me my you your we a an the is are am to for of and or in on with about should how what do does use uses prefer prefers like likes replies responses answers please help create tell that it this want wants".split())
_CATEGORIES = {"workflow": {"workflow", "routine", "schedule"}, "project": {"project", "projects"}, "goal": {"goal", "goals"},
               "technical": {"technical", "tech", "software"}, "work": {"work", "job"}, "profile": {"profile", "name"},
               "preference": {"preference", "preferences"}, "communication": {"language", "length"}}


class MemoryRetriever:
    def __init__(self, repository: MemoryRepository) -> None:
        self.repository = repository

    def retrieve(self, query: str, *, top_k: int = 5, preferences: bool = True, mark_used: bool = True) -> list[dict]:
        words = set(search_terms(query)) - _STOP
        categories = [category for category, keywords in _CATEGORIES.items() if words & keywords]
        candidates = self.repository.candidates(" ".join(sorted(words)), preferences=preferences, categories=categories)
        results = []
        for memory in candidates:
            content_words = set(search_terms(memory["content"])) - _STOP
            overlap = words & content_words
            key = memory["metadata"].get("fact_key", "")
            communication = preferences and key in {"communication:response_language", "communication:response_length"}
            category_match = memory["category"] in categories
            if not overlap and not communication and not category_match:
                continue
            score = 0.9 if communication else 0.35 + 0.5 * len(overlap) / max(1, min(len(words), len(content_words)))
            if category_match:
                score += 0.05
            if overlap and memory["category"] in {"work", "workflow", "technical", "project"}:
                score += 0.05
            score += memory["confidence"] * 0.02
            results.append({"memory_id": memory["id"], "content": memory["content"], "category": memory["category"], "relevance_score": round(min(1.0, score), 4)})
        results.sort(key=lambda item: (-item["relevance_score"], item["content"]))
        results = results[:top_k]
        if mark_used:
            self.repository.used([item["memory_id"] for item in results])
        return results

    @staticmethod
    def context(memories: list[dict]) -> str:
        if not memories:
            return ""
        context = "Relevant user memories:\n" + "\n".join("- " + memory["content"].replace("\n", " ")[:500] for memory in memories)
        contents = {memory["content"] for memory in memories}
        if "User prefers Hinglish replies." in contents:
            context += "\nReply style for this turn: write Hindi words in Latin letters mixed naturally with English. Example tone: 'Haan bhai, seedha aur short mein samjhata hoon.' Answer the user's actual question in this style."
        elif "User prefers Hindi replies." in contents:
            context += "\nReply style for this turn: इस प्रश्न का जवाब हिंदी में लिखें।"
        elif "User prefers English replies." in contents:
            context += "\nReply style for this turn: write the answer in English."
        return context
