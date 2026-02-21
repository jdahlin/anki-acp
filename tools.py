"""
Claude tool definitions and implementations for the Anki study assistant.

ALL_TOOLS  — list of schema dicts passed to the Claude API.
SYSTEM_PROMPT — system prompt that describes the tools to the model.
ToolHandler   — executes tool calls on the main Anki thread.
"""
from __future__ import annotations
import html
import re
from typing import Callable

# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------

CREATE_CARD_TOOL = {
    "name": "create_card",
    "description": (
        "Create a new Anki flashcard in the user's current deck. "
        "Use this when the user asks you to create a card, or when you identify "
        "something worth memorising that would make a good flashcard."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "front": {
                "type": "string",
                "description": "The question or prompt on the front of the card.",
            },
            "back": {
                "type": "string",
                "description": "The answer on the back of the card. Plain text or simple HTML.",
            },
        },
        "required": ["front", "back"],
    },
}

CREATE_CLOZE_TOOL = {
    "name": "create_cloze",
    "description": (
        "Create a new cloze-deletion Anki card in the current deck. "
        "Embed the blanks using standard Anki cloze syntax: {{c1::term}}, {{c2::term}}, etc."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "Full cloze text with {{c1::...}} markers, "
                    "e.g. 'The mitochondria is the {{c1::powerhouse}} of the cell.'"
                ),
            },
            "extra": {
                "type": "string",
                "description": "Optional extra/hint text for the back of the card.",
            },
        },
        "required": ["text"],
    },
}

SEARCH_CARDS_TOOL = {
    "name": "search_cards",
    "description": "Search the user's Anki collection and return matching cards as clickable links.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Anki search query, e.g. a topic keyword or 'tag:x'.",
            },
        },
        "required": ["query"],
    },
}

CHANGE_DECK_TOOL = {
    "name": "change_deck",
    "description": "Move the current card to a different deck.",
    "input_schema": {
        "type": "object",
        "properties": {
            "deck_name": {
                "type": "string",
                "description": "Name of the target deck (partial match is fine).",
            },
        },
        "required": ["deck_name"],
    },
}

UPDATE_CARD_BACK_TOOL = {
    "name": "update_card_back",
    "description": "Replace the back/answer field of the current card with new content.",
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "New content for the back field. Plain text or markdown.",
            },
        },
        "required": ["content"],
    },
}

ALL_TOOLS = [
    CREATE_CARD_TOOL,
    CREATE_CLOZE_TOOL,
    SEARCH_CARDS_TOOL,
    CHANGE_DECK_TOOL,
    UPDATE_CARD_BACK_TOOL,
]

# ------------------------------------------------------------------
# System prompt
# ------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Du är en studieassistent. Hjälp användaren förstå deras Anki-kort. "
    "Svara alltid på svenska.\n\n"
    "Du har följande verktyg. Använd API-verktyget om det finns tillgängligt, "
    "annars skriv blocket exakt som nedan i ditt svar — det parsas automatiskt:\n\n"
    "Skapa nytt kort:\n"
    "<create_card>{\"front\": \"fråga\", \"back\": \"svar\"}</create_card>\n\n"
    "Sök efter kort i samlingen:\n"
    "<search_cards>sökterm</search_cards>\n\n"
    "Flytta nuvarande kort till annan lek:\n"
    "<change_deck>lekens namn</change_deck>\n\n"
    "Uppdatera baksidan på nuvarande kort:\n"
    "<update_card_back>nytt innehåll</update_card_back>\n\n"
    "Skapa cloze-kort (använd {{c1::term}}, {{c2::term}} etc.):\n"
    "<create_cloze>{\"text\": \"Hjärtat har {{c1::4}} kammare.\", \"extra\": \"valfri extra\"}</create_cloze>\n\n"
    "Förklara aldrig formatet för användaren — bara använd det."
)

# ------------------------------------------------------------------
# Handler
# ------------------------------------------------------------------

class ToolHandler:
    """Executes tool calls on the main Anki thread.

    Parameters
    ----------
    mw              Anki main window (aqt.mw)
    get_current_card  Callable returning the currently reviewed card (or None)
    chat_tab        ChatTab instance for posting status messages
    md_to_card_html Callable(str) -> str — converts markdown to compact Anki HTML
    """

    def __init__(self, mw, get_current_card: Callable, chat_tab, md_to_card_html: Callable):
        self._mw = mw
        self._get_card = get_current_card
        self._chat = chat_tab
        self._md_to_card_html = md_to_card_html

    def dispatch(self, tool_name: str, tool_input: dict):
        """Route a tool call by name. Must be called on the main thread."""
        if tool_name == "create_card":
            self.create_card(tool_input.get("front", ""), tool_input.get("back", ""))
        elif tool_name == "create_cloze":
            self.create_cloze(tool_input.get("text", ""), tool_input.get("extra", ""))
        elif tool_name == "search_cards":
            q = tool_input.get("query", "")
            if q:
                self.search_cards(q)
        elif tool_name == "change_deck":
            d = tool_input.get("deck_name", "")
            if d:
                self.change_deck(d)
        elif tool_name == "update_card_back":
            c = tool_input.get("content", "")
            if c:
                self.update_card_back(c)

    # ------------------------------------------------------------------

    def create_card(self, front: str, back: str):
        if not front:
            return
        card = self._get_card()
        deck_id = card.did if card else self._mw.col.decks.get_current_id()

        notetype = None
        for nt in self._mw.col.models.all():
            if nt.get("name", "").lower() == "basic" and len(nt["flds"]) >= 2:
                notetype = nt
                break
        if notetype is None:
            for nt in self._mw.col.models.all():
                if len(nt["flds"]) >= 2:
                    notetype = nt
                    break
        if notetype is None:
            self._chat.add_status_message("*Kunde inte hitta en kortmall med minst 2 fält.*")
            return

        note = self._mw.col.new_note(notetype)
        note.fields[0] = front
        note.fields[1] = back
        self._mw.col.add_note(note, deck_id)
        self._chat.add_status_message("✓ Kort sparat i leken.")

    def create_cloze(self, text: str, extra: str = ""):
        if not text:
            return
        card = self._get_card()
        deck_id = card.did if card else self._mw.col.decks.get_current_id()

        notetype = None
        for nt in self._mw.col.models.all():
            if nt.get("type", 0) == 1:
                notetype = nt
                break
        if notetype is None:
            self._chat.add_status_message("*Hittade ingen cloze-mall i samlingen.*")
            return

        note = self._mw.col.new_note(notetype)
        note.fields[0] = text
        if extra and len(note.fields) > 1:
            note.fields[1] = extra
        self._mw.col.add_note(note, deck_id)
        self._chat.add_status_message("✓ Cloze-kort sparat i leken.")

    def search_cards(self, query: str):
        try:
            nids = self._mw.col.find_notes(query)[:10]
        except Exception as e:
            self._chat.add_status_message(f"*Sökfel: {html.escape(str(e))}*")
            return

        if not nids:
            self._chat.add_status_message(
                f'*Inga kort hittades för "{html.escape(query)}".*'
            )
            return

        lines = [f'**Sökresultat: "{html.escape(query)}"**\n']
        for nid in nids:
            try:
                note = self._mw.col.get_note(nid)
                front = re.sub(r'<[^>]+>', '', note.fields[0] if note.fields else "").strip()[:80]
                back_raw = note.fields[1] if len(note.fields) > 1 else ""
                back = re.sub(r'<[^>]+>', '', back_raw).strip()[:60]
                label = front or f"Note {nid}"
                snippet = f" — {back}" if back else ""
                lines.append(
                    f'<a href="anki://note/{nid}">{html.escape(label)}</a>'
                    f'<span style="color:#888;">{html.escape(snippet)}</span>'
                )
            except Exception:
                continue

        self._chat.add_status_message("\n".join(lines))

    def change_deck(self, deck_name: str):
        card = self._get_card()
        if not card:
            self._chat.add_status_message("*Inget aktivt kort att flytta.*")
            return

        all_decks = self._mw.col.decks.all_names_and_ids()
        needle = deck_name.lower()
        match = next(
            (d for d in all_decks if d.name.lower() == needle), None
        ) or next(
            (d for d in all_decks if needle in d.name.lower()), None
        )

        if match is None:
            names = ", ".join(d.name for d in all_decks[:6])
            self._chat.add_status_message(
                f'*Hittade ingen lek som matchar "{html.escape(deck_name)}". '
                f'Tillgängliga lekar: {html.escape(names)}…*'
            )
            return

        self._mw.col.set_deck([card.id], match.id)
        self._chat.add_status_message(
            f"✓ Kort flyttat till **{html.escape(match.name)}**."
        )

    def update_card_back(self, raw_markdown: str):
        card = self._get_card()
        if not card:
            return
        note = card.note()
        model = note.note_type()
        field_names = [f["name"] for f in model["flds"]]
        is_cloze = model.get("type", 0) == 1

        if is_cloze:
            idx = next(
                (i for i, n in enumerate(field_names)
                 if any(kw in n.lower() for kw in ("extra", "back"))),
                len(field_names) - 1 if len(field_names) > 1 else None,
            )
        else:
            idx = next(
                (i for i, n in enumerate(field_names)
                 if any(kw in n.lower() for kw in ("back", "answer", "svar", "baksida"))),
                1 if len(field_names) > 1 else None,
            )

        if idx is None:
            return

        note.fields[idx] = self._md_to_card_html(raw_markdown)
        self._mw.col.update_note(note)
