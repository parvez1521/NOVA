"""Deterministic computer-goal extraction used to constrain the existing LLM planner."""

from __future__ import annotations

import re

from app.computer.models import ComputerIntent


APPLICATION_TARGETS: dict[str, dict[str, object]] = {
    "Safari": {"bundle_id": "com.apple.Safari", "aliases": ("safari",)},
    "Google Chrome": {"bundle_id": "com.google.Chrome", "aliases": ("chrome", "google chrome")},
    "Arc": {"bundle_id": "company.thebrowser.Browser", "aliases": ("arc",)},
    "Finder": {"bundle_id": "com.apple.finder", "aliases": ("finder",)},
    "Terminal": {"bundle_id": "com.apple.Terminal", "aliases": ("terminal",)},
    "WhatsApp": {"bundle_id": "net.whatsapp.WhatsApp", "aliases": ("whatsapp",)},
    "Telegram": {"bundle_id": "ru.keepcoder.Telegram", "aliases": ("telegram",)},
    "System Settings": {"bundle_id": "com.apple.systempreferences", "aliases": ("system settings", "settings")},
}


def resolve_application(value: str) -> tuple[str, str] | None:
    normalized = re.sub(r"\s+", " ", value.strip().casefold())
    for display, data in APPLICATION_TARGETS.items():
        if normalized == display.casefold() or normalized == str(data["bundle_id"]).casefold() or normalized in data["aliases"]:
            return display, str(data["bundle_id"])
    return None


def _explicit_app(text: str) -> tuple[str, str] | None:
    # Longest aliases first prevents "Chrome" from winning inside "Google Chrome".
    for candidate in sorted(APPLICATION_TARGETS, key=len, reverse=True):
        aliases = (candidate.casefold(), *APPLICATION_TARGETS[candidate]["aliases"])
        if any(re.search(rf"\b{re.escape(alias)}\b", text.casefold()) for alias in aliases):
            return candidate, str(APPLICATION_TARGETS[candidate]["bundle_id"])
    return None


def _search_query(text: str) -> str:
    patterns = (
        r"\b(?:google|youtube)\s+(?:par|pe|on)\s+(.+?)\s+search(?:\s+kar(?:o|na)?)?\s*$",
        r"\bsearch\s+(?:for\s+)?(.+?)(?:\s+(?:on|in|using)\s+(?:google|youtube|the web))?\s*$",
        r"\b(?:look up|find)\s+(?:for\s+)?(.+?)\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            query = re.sub(r"\s+", " ", match.group(1)).strip(" .!?।")
            query = re.sub(r"\s+kar(?:o|na)?$", "", query, flags=re.I).strip()
            if query:
                return query
    return ""


def _whatsapp_entity(text: str) -> str:
    patterns = (
        r"\b(.+?)\s+ko\s+(?:message|msg|text|likh|bhej)\b",
        r"\b(?:message|msg)\s+(.+?)(?=\s+(?:saying|that|with|and)\b|\s*$)",
        r"\b(?:find|search for|contact)\s+(.+?)\s*$",
        r"\bto\s+(.+?)(?=\s+(?:saying|that|with)\b|\s*$)",
        r"\bko\s+(.+?)(?=\s+(?:message|msg|text|likh|bhej)\b|\s*$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" .!?।")
            value = re.split(r"\b(?:open|khol(?:o|na)?|aur|and|karke)\b", value, flags=re.I)[-1].strip(" .!?।")
            if value and value.casefold() not in {"a message", "message", "it"}:
                return value
    return ""


def _message_text(text: str) -> str:
    match = re.search(r"\b(?:saying|that says?|with the message|message:|text:|likh(?:o|na)?|bhej(?:o|na)?)\s+(.+)$", text, re.I)
    return re.sub(r"\s+", " ", match.group(1)).strip(" .!?।\"") if match else ""


def parse_goal(goal: str, follow_up: str = "") -> ComputerIntent:
    """Extract only deterministic intent facts; the LLM still chooses each tool action."""

    original = goal.strip()
    text = re.sub(r"^(?:hey\s+)?nova[\s,:.!-]*", "", original, flags=re.I).strip()
    combined = (text + " " + follow_up.strip()).strip()
    connector_goal = bool(re.search(r"\b(?:gmail|github|notion|calendar|drive|linear|slack)\b", combined, re.I))
    file_goal = bool(re.search(r"\b(?:file|files|folder|folders|directory|downloads?|desktop)\b", combined, re.I))
    research_goal = bool(re.search(r"\b(?:research|compare|first\s+(?:three|3)|latest|newest)\b", combined, re.I))
    computer_goal = bool(re.search(r"\b(?:open|launch|click|search|navigate|type|scroll|focus|safari|chrome|finder|whatsapp|telegram)\b|खोल", combined, re.I))
    task_kind = "HYBRID" if connector_goal and computer_goal else "CONNECTOR" if connector_goal else "RESEARCH" if research_goal else "FILE" if file_goal else "COMPUTER_USE"
    app = _explicit_app(text) or _explicit_app(combined)
    target_app, bundle_id = app or ("", "")

    if re.search(r"\bwhatsapp\b", combined, re.I):
        entity = _whatsapp_entity(text) or _whatsapp_entity(combined)
        message = _message_text(text)
        is_message = bool(re.search(r"\b(?:message|msg|text|bhej|likh)\b", combined, re.I))
        if not message and follow_up.strip() and is_message:
            message = re.sub(r"\s+", " ", follow_up.strip()).strip(" .!?।\"")
        if is_message:
            steps = [
                "Open or focus WhatsApp",
                "Observe WhatsApp contact/search state",
                "Resolve the exact requested contact",
                "Open the matching conversation",
            ]
            if message:
                steps += ["Type the requested message", "Ask for confirmation before Send", "Send the message", "Verify the sent state"]
            else:
                steps += ["Ask for the missing message text before typing or sending"]
            return ComputerIntent(
                user_goal=original, task_kind=task_kind, target_app="WhatsApp", target_bundle_id=APPLICATION_TARGETS["WhatsApp"]["bundle_id"],
                target_object=entity, requested_content=message, intended_action="send_message", ordered_steps=steps,
                success_condition="The exact contact conversation contains the requested message in a verified sent state." if message else "The exact contact is resolved and message text is requested before any send.",
                verification_strategy="Observe WhatsApp after each state transition; match the exact contact and verify the sent message in the conversation.",
                risk_level="high", confirmation_requirement="before final Send", missing_information="" if message else "message_text",
                entity_resolution="required" if entity else "ambiguous",
            )
        return ComputerIntent(
            user_goal=original, task_kind=task_kind, target_app="WhatsApp", target_bundle_id=APPLICATION_TARGETS["WhatsApp"]["bundle_id"],
            target_object=entity, intended_action="find_contact", ordered_steps=[
                "Open or focus WhatsApp", "Observe the contact/search state", "Resolve the exact requested contact",
                "Open the matching conversation", "Verify the correct conversation is visible",
            ], success_condition="The exact requested contact conversation is visible.",
            verification_strategy="Observe WhatsApp and require an exact entity match; never choose a similar contact.",
            entity_resolution="required" if entity else "ambiguous",
        )

    query = _search_query(combined)
    if query:
        target_app = target_app or "Google Chrome"
        bundle_id = bundle_id or str(APPLICATION_TARGETS["Google Chrome"]["bundle_id"])
        target_site = "youtube" if re.search(r"\byoutube\b", combined, re.I) else "web"
        return ComputerIntent(
            user_goal=original, task_kind=task_kind, target_app=target_app, target_bundle_id=bundle_id,
            target_object=query, target_site=target_site, intended_action="search", ordered_steps=[
                f"Open or focus {target_app}", "Observe the current window", "Locate the address/search field",
                f"Enter the query: {query}", "Submit the search", "Observe the resulting page", "Verify search results exist",
            ], success_condition="The requested search results are visible in the explicitly requested application.",
            verification_strategy="Observe after submission and verify the requested app remains active with a result page and visible result content.",
        )

    if target_app:
        object_name = "Downloads" if target_app == "Finder" and re.search(r"\bdownloads?\b", combined, re.I) else ""
        action = "open_folder" if object_name else "open_application"
        steps = [f"Open or focus {target_app}", "Observe the active application and window"]
        if object_name:
            steps += [f"Navigate to the {object_name} folder", f"Verify the {object_name} folder is visible"]
        return ComputerIntent(
            user_goal=original, task_kind=task_kind, target_app=target_app, target_bundle_id=bundle_id, target_object=object_name,
            intended_action=action, ordered_steps=steps,
            success_condition=f"{target_app} is visible and focused" + (f" with {object_name} visible." if object_name else "."),
            verification_strategy="Observe active application, bundle identity, window title, and visible target state.",
        )

    return ComputerIntent(
        user_goal=original, intended_action="general_computer_goal",
        task_kind=task_kind,
        ordered_steps=["Interpret the complete goal", "Execute each required action", "Observe and verify the final state"],
        success_condition="Every requested part of the user goal is independently verified.",
        verification_strategy="Observe → act → observe → verify for every meaningful action.",
    )


def entity_is_verified(intent: ComputerIntent, evidence: list[dict]) -> bool:
    return entity_resolution_state(intent, evidence) == "resolved"


def entity_resolution_state(intent: ComputerIntent, evidence: list[dict]) -> str:
    if intent.entity_resolution == "none":
        return "resolved"
    target = intent.target_object.casefold().strip()
    if not target:
        return "missing"
    for item in evidence:
        observed = item.get("observed", {})
        result = item.get("result", {})
        matches = result.get("matches")
        if isinstance(matches, list) and len(matches) > 1:
            if any(target in str(match).casefold() for match in matches):
                return "ambiguous"
        if isinstance(matches, list) and len(matches) == 1 and target in str(matches[0]).casefold() and item.get("verified"):
            return "resolved"
        haystack = " ".join(str(value) for value in (
            observed.get("visible_text", ""), result.get("visible_text", ""), result.get("text", ""),
            result.get("title", ""), result.get("name", ""), result.get("matches", ""),
        )).casefold()
        if target in haystack and item.get("verified"):
            return "resolved"
    return "missing"
