import uuid
from typing import Optional, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.locales.translator import get_text


def get_main_menu_keyboard(lang: str = "en", is_admin: bool = False) -> InlineKeyboardMarkup:
    """Participant main menu keyboard strictly providing:
    1. ▶️ Start Competition
    2. 🌐 Change Language
    3. ❓ Help
    4. ⚙️ Admin Dashboard (if user is admin)
    """
    keyboard = [
        [InlineKeyboardButton(get_text("start_btn", lang), callback_data="menu:start")],
        [InlineKeyboardButton(get_text("change_lang_btn", lang), callback_data="menu:lang")],
        [InlineKeyboardButton(get_text("help_btn", lang), callback_data="menu:help")],
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="admin:home")])
    return InlineKeyboardMarkup(keyboard)


def get_membership_prompt_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Keyboard shown when prompting for membership:
    1. 🌐 Register for EMYC Membership (URL button opening https://membership.emyc.et/)
    2. 🔙 Main Menu (Back button)
    """
    keyboard = [
        [InlineKeyboardButton(get_text("register_online_btn", lang), url="https://membership.emyc.et/")],
        [InlineKeyboardButton(get_text("back_btn", lang), callback_data="menu:home")],
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
    question_id: Optional[uuid.UUID] = None,
    display_order: int = 1,
    total_questions: int = 1,
    selected_opt: Optional[str] = None,
    lang: str = "en",
) -> InlineKeyboardMarkup:
    """Question answer choices: A, B, C, D, Prev/Next navigation, Review and Submit.
    Uses compact callback under 64-byte Telegram limit: ans:<attempt_id>:<display_order>:<opt>
    """
    buttons = []
    for opt in ["A", "B", "C", "D"]:
        label = f"✅ {opt}" if selected_opt == opt else opt
        cb_data = f"ans:{attempt_id}:{display_order}:{opt}"
        buttons.append(InlineKeyboardButton(label, callback_data=cb_data))

    keyboard = [buttons]

    # Row 2: Navigation - Previous & Next
    nav_row = []
    if display_order > 1:
        nav_row.append(
            InlineKeyboardButton(get_text("prev_question_btn", lang), callback_data=f"q:nav:{attempt_id}:{display_order - 1}")
        )
    if display_order < total_questions:
        nav_row.append(
            InlineKeyboardButton(get_text("next_question_btn", lang), callback_data=f"q:nav:{attempt_id}:{display_order + 1}")
        )
    if nav_row:
        keyboard.append(nav_row)

    # Row 3: Review summary & Submit exam
    action_row = [
        InlineKeyboardButton(get_text("review_summary_btn", lang), callback_data=f"q:rev_all:{attempt_id}:{display_order}"),
        InlineKeyboardButton(get_text("finish_exam_btn", lang), callback_data=f"exam:submit:{attempt_id}"),
    ]
    keyboard.append(action_row)

    return InlineKeyboardMarkup(keyboard)


def get_exam_review_keyboard(
    attempt_id: uuid.UUID,
    total_questions: int,
    answered_orders: set,
    current_display_order: int = 1,
    lang: str = "en",
) -> InlineKeyboardMarkup:
    """Keyboard for in-exam review screen: quick jump buttons (rows of 5) + Return and Submit."""
    keyboard = []
    current_row = []
    for i in range(1, total_questions + 1):
        indicator = "✅" if i in answered_orders else "⚠️"
        btn_text = f"{i} {indicator}"
        current_row.append(InlineKeyboardButton(btn_text, callback_data=f"q:nav:{attempt_id}:{i}"))
        if len(current_row) == 5:
            keyboard.append(current_row)
            current_row = []
    if current_row:
        keyboard.append(current_row)

    action_row = [
        InlineKeyboardButton(get_text("review_return_btn", lang), callback_data=f"q:nav:{attempt_id}:{current_display_order}"),
        InlineKeyboardButton(get_text("finish_exam_btn", lang), callback_data=f"exam:submit:{attempt_id}"),
    ]
    keyboard.append(action_row)
    return InlineKeyboardMarkup(keyboard)


def get_results_keyboard(
    competition_id: uuid.UUID, correct_count: int, incorrect_count: int, lang: str = "en"
) -> InlineKeyboardMarkup:
    """Results post-publication keyboard with segregated answer review options:
    - ✅ Correct Answers
    - ❌ Incorrect Answers
    - 🔙 Main Menu
    """
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
    """Main administrative control panel keyboard with exactly 3 operational categories:
    1. 🌐 Change Language
    2. 🏆 Manage Competition
    3. 📊 Competition Results
    """
    keyboard = [
        [InlineKeyboardButton(get_text("admin_btn_lang", lang), callback_data="admin:lang")],
        [InlineKeyboardButton(get_text("admin_btn_competition", lang), callback_data="admin:competition")],
        [InlineKeyboardButton(get_text("admin_btn_results", lang), callback_data="admin:results")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_language_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Language selection keyboard for admin with return to admin panel."""
    keyboard = [
        [
            InlineKeyboardButton("English 🇬🇧", callback_data="admin:set_lang:en"),
            InlineKeyboardButton("አማርኛ 🇪🇹", callback_data="admin:set_lang:am"),
        ],
        [
            InlineKeyboardButton("Afaan Oromoo 🌳", callback_data="admin:set_lang:om"),
            InlineKeyboardButton("العربية 🇸🇦", callback_data="admin:set_lang:ar"),
        ],
        [InlineKeyboardButton(get_text("admin_btn_back", lang), callback_data="admin:home")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_participants_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Keyboard for dedicated participants analytics screen."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text("admin_btn_refresh", lang), callback_data="admin:participants")],
        [InlineKeyboardButton(get_text("admin_btn_back", lang), callback_data="admin:home")],
    ])


def get_admin_rankings_keyboard(lang: str = "en", page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Keyboard for rankings and leaderboard screen with pagination support."""
    rows = []
    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin:rankings:{page - 1}"))
        nav_buttons.append(InlineKeyboardButton(f"Page {page} / {total_pages}", callback_data=f"admin:rankings:{page}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin:rankings:{page + 1}"))
        rows.append(nav_buttons)

    rows.append([
        InlineKeyboardButton("📊 Results Dashboard", callback_data="admin:results"),
        InlineKeyboardButton(get_text("admin_btn_back", lang), callback_data="admin:home"),
    ])
    return InlineKeyboardMarkup(rows)


def get_admin_results_keyboard(comp_id: Optional[Any] = None, status: Optional[str] = None, lang: str = "en") -> InlineKeyboardMarkup:
    """Consolidated operational results dashboard keyboard."""
    rows = []
    if comp_id:
        rows.append([InlineKeyboardButton("🏅 View Rankings", callback_data="admin:rankings:1")])
        if status == "CLOSED":
            rows.append([InlineKeyboardButton("📊 Finalize Scores & Rankings", callback_data=f"admin:finalize:{comp_id}")])
        elif status == "RESULTS_FINALIZED":
            rows.append([InlineKeyboardButton("📢 Publish Results to Participants", callback_data=f"admin:publish:{comp_id}")])

    rows.append([
        InlineKeyboardButton(get_text("admin_btn_refresh", lang), callback_data="admin:results"),
        InlineKeyboardButton(get_text("admin_btn_back", lang), callback_data="admin:home"),
    ])
    return InlineKeyboardMarkup(rows)


def get_admin_system_status_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Keyboard for system status & health check screen."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text("admin_btn_refresh", lang), callback_data="admin:sys_status")],
        [InlineKeyboardButton(get_text("admin_btn_back", lang), callback_data="admin:home")],
    ])


def get_admin_create_comp_duration_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Interactive duration preset selector during competition creation."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏱ 15 Minutes", callback_data="admin:create_dur:15"),
            InlineKeyboardButton("⏱ 30 Minutes", callback_data="admin:create_dur:30"),
        ],
        [
            InlineKeyboardButton("⏱ 60 Minutes", callback_data="admin:create_dur:60"),
            InlineKeyboardButton("⏱ 120 Minutes", callback_data="admin:create_dur:120"),
        ],
        [
            InlineKeyboardButton(get_text("admin_btn_custom_dur", lang), callback_data="admin:create_dur:custom"),
        ],
        [InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="admin:competition")],
    ])


def get_admin_create_comp_schedule_keyboard() -> InlineKeyboardMarkup:
    """Interactive schedule window selector during competition creation."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 24 Hours", callback_data="admin:create_sched:24h"),
            InlineKeyboardButton("📅 3 Days", callback_data="admin:create_sched:3d"),
        ],
        [
            InlineKeyboardButton("📅 7 Days", callback_data="admin:create_sched:7d"),
            InlineKeyboardButton("📅 14 Days", callback_data="admin:create_sched:14d"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="admin:competition")],
    ])


def get_admin_create_comp_questions_keyboard() -> InlineKeyboardMarkup:
    """Question setup mode selector during competition creation."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⚡ Attach EMYC Standard Questions", callback_data="admin:create_q:standard"
            )
        ],
        [
            InlineKeyboardButton(
                "📝 Create Empty (Add Questions Manually)", callback_data="admin:create_q:manual"
            )
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="admin:competition")],
    ])


def get_admin_confirm_announcement_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Confirmation modal keyboard before broadcasting an announcement."""
    keyboard = [
        [
            InlineKeyboardButton(get_text("btn_confirm_broadcast", lang), callback_data="admin:announce_confirm"),
            InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="admin:home"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_question_correct_choice_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting the correct answer during interactive question creation."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("A", callback_data="admin:q_wiz_choice:A"),
            InlineKeyboardButton("B", callback_data="admin:q_wiz_choice:B"),
            InlineKeyboardButton("C", callback_data="admin:q_wiz_choice:C"),
            InlineKeyboardButton("D", callback_data="admin:q_wiz_choice:D"),
        ],
        [InlineKeyboardButton("❌ Discard", callback_data="admin:q_wiz_cancel")],
    ])


def get_admin_question_preview_keyboard(comp_id: Any) -> InlineKeyboardMarkup:
    """Keyboard for previewing and confirming question addition."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💾 Save Question", callback_data=f"admin:q_wiz_save:{comp_id}")],
        [InlineKeyboardButton("❌ Discard", callback_data="admin:q_wiz_cancel")],
    ])


def get_admin_question_list_keyboard(comp_id: Any, page: int, total_pages: int, questions: list) -> InlineKeyboardMarkup:
    """Keyboard for managing questions in a competition with pagination and delete buttons."""
    rows = []
    # Add a row for each question to delete
    for q in questions:
        title_snippet = q.question_text[:25] + ("..." if len(q.question_text) > 25 else "")
        rows.append([
            InlineKeyboardButton(f"🗑 Delete #{q.order_index}: {title_snippet}", callback_data=f"admin:q_del:{q.id}")
        ])

    # Pagination navigation
    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin:q_list:{comp_id}:{page - 1}"))
        nav_buttons.append(InlineKeyboardButton(f"Page {page} / {total_pages}", callback_data=f"admin:q_list:{comp_id}:{page}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin:q_list:{comp_id}:{page + 1}"))
        rows.append(nav_buttons)

    # Action buttons
    rows.append([
        InlineKeyboardButton("➕ Add Another Question", callback_data=f"admin:add_q:{comp_id}"),
    ])
    rows.append([
        InlineKeyboardButton("⚙️ Competition Controls", callback_data="admin:competition"),
        InlineKeyboardButton("◀️ Admin Home", callback_data="admin:home"),
    ])
    return InlineKeyboardMarkup(rows)

