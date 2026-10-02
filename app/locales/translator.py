from typing import Any, Dict
from app.locales.en import MESSAGES as EN_MESSAGES
from app.locales.am import MESSAGES as AM_MESSAGES
from app.locales.om import MESSAGES as OM_MESSAGES
from app.locales.ar import MESSAGES as AR_MESSAGES

CATALOG: Dict[str, Dict[str, str]] = {
    "en": EN_MESSAGES,
    "am": AM_MESSAGES,
    "om": OM_MESSAGES,
    "ar": AR_MESSAGES,
}


def get_text(key: str, lang: str = "en", **kwargs: Any) -> str:
    """Retrieves localized text by key and formats with provided arguments.
    
    Falls back gracefully to English, then to the key itself if missing.
    """
    messages = CATALOG.get(lang, EN_MESSAGES)
    template = messages.get(key)

    if template is None:
        template = EN_MESSAGES.get(key, key)

    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template
