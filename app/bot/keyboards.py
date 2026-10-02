import uuid
from typing import Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.locales.translator import get_text


def get_main_menu_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Participant main menu keyboard: 3 essential buttons."""
    keyboard = [
        [InlineKeyboardButton(get_text("start_btn", lang), callback_data="menu:start")],
        [
            InlineKeyboardButton(get_text("change_lang_btn", lang), callback_data="menu:lang"),
            InlineKeyboardButton(get_text("help_btn", lang), callback_data="menu:help"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_language_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Language selection keyboard: 2x2 grid with back button."""
    keyboard = [
        [
            InlineKeyboardButton("English 🇬🇧", callback_data="lang:en"),
            InlineKeyboardButton("አማርኛ 🇪🇹", callback_data="lang:am"),
        ],
        [
            InlineKeyboardButton("Afaan Oromoo 🌳", callback_data="lang:om"),
            InlineKeyboardButton("العربية 🇸🇦", callback_data="lang:ar"),
        ],
        [InlineKeyboardButton(get_text("back_btn", lang), callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_start_exam_keyboard(competition_id: uuid.UUID, lang: str = "en") -> InlineKeyboardMarkup:
    """Confirmation to launch competition attempt."""
    keyboard = [
        [InlineKeyboardButton(get_text("start_exam_btn", lang), callback_data=f"exam:start:{competition_id}")],
        [InlineKeyboardButton(get_text("back_btn", lang), callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_question_keyboard(
    attempt_id: uuid.UUID,
    question_id: uuid.UUID,
    display_order: int,
    total_questions: int,
    selected_opt: Optional[str] = None,
    lang: str = "en",
) -> InlineKeyboardMarkup:
    """Question answer choices and progression buttons."""
    # 4 Option buttons: A, B, C, D
    buttons = []
    for opt in ["A", "B", "C", "D"]:
        label = f"🔘 {opt}" if selected_opt == opt else opt
        # Callback payload: ans:<attempt_id>:<question_id>:<opt>:<display_order>
        cb_data = f"ans:{attempt_id}:{question_id}:{opt}:{display_order}"
        buttons.append(InlineKeyboardButton(label, callback_data=cb_data))

    keyboard = [buttons]

    # Navigation or Final Submission row
    if display_order < total_questions:
        nav_label = get_text("next_question_btn", lang)
        nav_btn = InlineKeyboardButton(nav_label, callback_data=f"q:nav:{attempt_id}:{display_order + 1}")
        keyboard.append([nav_btn])
    else:
        submit_label = get_text("submit_exam_btn", lang)
        submit_btn = InlineKeyboardButton(submit_label, callback_data=f"exam:submit:{attempt_id}")
        keyboard.append([submit_btn])

    return InlineKeyboardMarkup(keyboard)


def get_results_keyboard(
    competition_id: uuid.UUID, correct_count: int, incorrect_count: int, lang: str = "en"
) -> InlineKeyboardMarkup:
    """Results post-publication keyboard with segregated answer review options."""
    keyboard = [
        [
            InlineKeyboardButton(
                get_text("view_correct_btn", lang, count=correct_count),
                callback_data=f"rev:correct:{competition_id}",
            )
        ],
        [
            InlineKeyboardButton(
                get_text("view_incorrect_btn", lang, count=incorrect_count),
                callback_data=f"rev:incorrect:{competition_id}",
            )
        ],
        [InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Main administrative control panel keyboard."""
    keyboard = [
        [
            InlineKeyboardButton(get_text("admin_btn_status", lang), callback_data="admin:status"),
            InlineKeyboardButton(get_text("admin_btn_results", lang), callback_data="admin:results"),
        ],
        [
            InlineKeyboardButton(get_text("admin_btn_announce", lang), callback_data="admin:announce"),
            InlineKeyboardButton(get_text("admin_btn_competition", lang), callback_data="admin:competition"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_confirm_announcement_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Confirmation modal keyboard before broadcasting an announcement."""
    keyboard = [
        [
            InlineKeyboardButton(get_text("btn_confirm_broadcast", lang), callback_data="admin:announce_confirm"),
            InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="admin:home"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
