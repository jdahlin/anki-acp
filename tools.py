"""
Claude tool definitions (function-calling schema) for the Anki study assistant.
All tools are listed in ALL_TOOLS and passed to the Claude API.
"""

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
