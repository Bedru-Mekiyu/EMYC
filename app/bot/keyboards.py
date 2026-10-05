import uuid
from typing import Optional, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from app.locales.translator import get_text


def get_main_menu_keyboard(
    lang: str = "en",
    is_admin: bool = False,
    published_comp_id: Optional[uuid.UUID] = None,
) -> InlineKeyboardMarkup:
    """Participant main menu keyboard providing:
    1. 🌐 Change Language
    2. 🏆 Competition
    3. ⚙️ Admin Dashboard (if user is admin)
    """
    rows = [
        [InlineKeyboardButton(get_text("change_lang_btn", lang), callback_data="menu:lang")],
        [InlineKeyboardButton(get_text("start_btn", lang), callback_data="menu:start")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="admin:home")])
    return InlineKeyboardMarkup(rows)


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


def get_share_phone_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    """Provides native contact sharing button for participant registration."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(get_text("reg_share_phone_btn", lang), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


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
    competition_id: uuid.UUID,
    correct_count: Optional[int] = None,
    incorrect_count: Optional[int] = None,
    lang: str = "en",
) -> InlineKeyboardMarkup:
    """Results post-publication keyboard with single review option or legacy segregated options:
    - 📖 Review Answers (or legacy Correct/Incorrect buttons if counts explicitly provided)
    - 🔙 Main Menu
    """
    if correct_count is not None and incorrect_count is not None:
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

    keyboard = [
        [
            InlineKeyboardButton(
                get_text("review_answers_btn", lang),
                callback_data=f"rev:q:{competition_id}:1",
            )
        ],
        [InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_answer_review_nav_keyboard(
    competition_id: uuid.UUID,
    display_order: int,
    total_questions: int,
    lang: str = "en",
) -> InlineKeyboardMarkup:
    """Keyboard for 1-question-at-a-time post-exam answer review with Prev/Next, Back to My Result, and Main Menu."""
    rows = []
    nav_row = []
    if display_order > 1:
        nav_row.append(
            InlineKeyboardButton(
                get_text("btn_prev", lang),
                callback_data=f"rev:q:{competition_id}:{display_order - 1}",
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            f"{display_order} / {total_questions}",
            callback_data=f"rev:q:{competition_id}:{display_order}",
        )
    )
    if display_order < total_questions:
        nav_row.append(
            InlineKeyboardButton(
                get_text("btn_next", lang),
                callback_data=f"rev:q:{competition_id}:{display_order + 1}",
            )
        )
    rows.append(nav_row)

    rows.append([
        InlineKeyboardButton(
            get_text("back_to_my_result_btn", lang),
            callback_data=f"rev:my_result:{competition_id}",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            get_text("back_to_menu_btn", lang),
            callback_data="menu:home",
        )
    ])
    return InlineKeyboardMarkup(rows)


def get_admin_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    """Main administrative control panel keyboard with strictly 3 actions:
    1. 🏆 Competition
    2. 📢 Announcement
    3. 🌐 Language
    """
    keyboard = [
        [InlineKeyboardButton(get_text("admin_btn_competition", lang), callback_data="admin:competition")],
        [InlineKeyboardButton(get_text("admin_btn_announce", lang), callback_data="admin:announce")],
        [InlineKeyboardButton(get_text("admin_btn_lang", lang), callback_data="admin:lang")],
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


def get_admin_rankings_keyboard(lang: str = "en", page: int = 1, total_pages: int = 1, comp_id: Optional[Any] = None) -> InlineKeyboardMarkup:
    """Keyboard for rankings and leaderboard screen showing top performers."""
    rows = []
    if comp_id:
        rows.append([InlineKeyboardButton(get_text("admin_btn_export_csv", lang), callback_data=f"admin:export_results:{comp_id}")])
    rows.append([
        InlineKeyboardButton("📊 Results Dashboard", callback_data="admin:results"),
        InlineKeyboardButton(get_text("admin_btn_back", lang), callback_data="admin:home"),
    ])
    return InlineKeyboardMarkup(rows)


def get_admin_results_keyboard(comp_id: Optional[Any] = None, status: Optional[str] = None, lang: str = "en") -> InlineKeyboardMarkup:
    """Consolidated operational results dashboard keyboard."""
    rows = []
    if comp_id:
        rows.append([InlineKeyboardButton("🏅 View Rankings", callback_data="admin:rankings")])
        if status in ["CLOSED", "RESULTS_FINALIZED", "PUBLISHED", "ARCHIVED"]:
            rows.append([InlineKeyboardButton(get_text("admin_btn_export_csv", lang), callback_data=f"admin:export_results:{comp_id}")])
        if status == "CLOSED":
            rows.append([InlineKeyboardButton("📊 Finalize Scores & Rankings", callback_data=f"admin:finalize:{comp_id}")])
        elif status == "RESULTS_FINALIZED":
            rows.append([InlineKeyboardButton("📢 Publish Results to Participants", callback_data=f"admin:publish:{comp_id}")])

    rows.append([
        InlineKeyboardButton(get_text("admin_btn_refresh", lang), callback_data="admin:results"),
        InlineKeyboardButton("◀️ Competition", callback_data="admin:competition"),
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


def get_admin_create_comp_schedule_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
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
        [
            InlineKeyboardButton("📅 30 Days (1 Month)", callback_data="admin:create_sched:30d"),
            InlineKeyboardButton(get_text("admin_btn_custom_sched", lang), callback_data="admin:create_sched:custom"),
        ],
        [InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="admin:competition")],
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

