MESSAGES = {
    "welcome": (
        "Assalamu Alaikum, {name}! 👋\n\n"
        "🏆 *EMYC Competition*\n"
        "Welcome to the Ethiopian Muslim Youth Council Competitive Examination Platform.\n\n"
        "Test your knowledge, compete with peers, and track your performance."
    ),
    "start_btn": "🏆 Competition",
    "change_lang_btn": "🌐 Change Language",
    "help_btn": "❓ Help",
    "select_language": "Please select your preferred language:",
    "language_updated": "✅ Language updated successfully.",
    "help_text": (
        "🏆 *EMYC Competition Guidelines:*\n\n"
        "1. You must be an officially registered member of EMYC. Verify your Membership ID.\n"
        "2. Review the competition details and tap *Start Competition*.\n"
        "3. Answer each question carefully before your individual timer expires.\n"
        "4. Your exam is automatically submitted when time runs out or upon final question submission.\n"
        "5. Official results will be published once the competition concludes."
    ),
    "register_online_btn": "🌐 Register for EMYC Membership",
    "membership_prompt": (
        "🆔 *EMYC Membership Verification*\n\n"
        "To participate in this competition, you must be a registered member of the "
        "Ethiopian Muslim Youth Council (EMYC).\n\n"
        "🔗 *Not registered yet?*\n"
        "Tap the button below to register online and receive your official Membership ID at:\n"
        "https://membership.emyc.et/\n\n"
        "📩 *Already registered?*\n"
        "Please send your official EMYC Membership ID directly in this chat:"
    ),
    "membership_invalid_format": "❌ Invalid Membership ID. Please check your official EMYC ID and try again.",
    "membership_not_found": (
        "❌ Membership ID could not be found.\n\n"
        "Please verify that you entered your ID correctly. If you have not registered yet, "
        "please register online at https://membership.emyc.et/ before taking the exam."
    ),
    "membership_already_bound": "❌ This Membership ID is already bound to another Telegram account.",
    "membership_verified": (
        "✅ *Membership Verified!*\n\n"
        "👤 *Member:* {full_name}\n"
        "🆔 *ID:* `{membership_id}`\n\n"
        "Your account is linked successfully. Proceeding to competition details..."
    ),
    "competition_not_open": "⏳ The competition is not open yet.\nOpens: {opens_at}\nCloses: {closes_at}",
    "competition_closed": "🚫 This competition has closed.",
    "start_exam_btn": "▶️ Start Competition",
    "exam_info": (
        "🏆 *EMYC Competition*\n"
        "*{title}*\n\n"
        "📅 *Opens:* {opens_at}\n"
        "📅 *Closes:* {closes_at}\n"
        "⏱ *Duration:* {duration} minutes\n"
        "📝 *Questions:* {questions} questions\n\n"
        "✅ *You are eligible to participate.*\n\n"
        "Press the button below when you are ready to begin your timed attempt."
    ),
    "already_submitted": "✅ You have already completed and submitted your exam.",
    "question_header": "*Question {current} / {total}*\n⏱ *Time remaining:* {time_left}",
    "time_up_auto_submit": "⏰ *Time is up.*\nYour examination has been submitted automatically.",
    "finish_exam_btn": "🏁 Finish Examination",
    "exam_submitted": (
        "⏳ *Examination Completed*\n\n"
        "Thank you for participating! Your answers have been submitted.\n"
        "Results will be announced after the competition closes."
    ),
    "results_pending": "⏳ Official results are not published yet. Please check back after official announcement.",
    "results_title": (
        "🏆 *EMYC Competition Results*\n"
        "*{title}*\n\n"
        "👤 *Participant:* {full_name}\n"
        "🎯 *Score:* {score} / {total} ({percent}%)\n"
        "✅ *Correct Answers:* {correct}\n"
        "❌ *Incorrect / Unanswered:* {incorrect}\n"
        "🏅 *Rank:* #{rank} of {total_participants}\n"
        "⏱ *Time Taken:* {time}"
    ),
    "view_result_btn": "📊 View Result",
    "view_correct_btn": "✅ Correct Answers ({count})",
    "view_incorrect_btn": "❌ Incorrect Answers ({count})",
    "back_to_menu_btn": "🔙 Main Menu",
    "back_btn": "🔙 Back",
    "next_question_btn": "⏭ Next Question",
    "prev_question_btn": "⏮ Previous Question",
    "review_summary_btn": "📋 Review All Answers",
    "review_title": "📋 *Exam Progress & Answer Review*",
    "review_progress": "📝 *Progress:* {answered} / {total} answered ({unanswered} remaining)",
    "review_time": "⏱ *Time Remaining:* {time_left}",
    "review_return_btn": "◀️ Return to Exam",
    "selected_answer_text": "📌 *Selected Answer:* Option *{option}* ✅",
    "answer_selected_toast": "✅ Option {option} selected!",
    "submit_exam_btn": "🏁 Submit Exam",
    "no_incorrect": "🎉 Perfect score! You have no incorrect answers.",
    "unauthorized_admin": "⛔ Unauthorized access.",
    "admin_menu_title": (
        "🏆 *EMYC Competition Admin*\n\n"
        "*Active Competition:* {title} [{status}]\n"
        "👥 *Registered Participants:* {registered_count}\n"
        "▶️ *Started Attempts:* {started_count}\n"
        "⏳ *In Progress:* {in_progress_count}\n"
        "✅ *Submitted:* {submitted_count}"
    ),
    "admin_btn_competition": "🏆 Competition",
    "admin_btn_status": "📊 Status",
    "admin_btn_results": "📊 Results",
    "admin_btn_lang": "🌐 Language",
    "admin_btn_custom_dur": "⏱ Custom Duration",
    "admin_custom_dur_prompt": (
        "⏱ *Custom Competition Duration*\n\n"
        "Please enter the duration in minutes as a number.\n"
        "Example: `45` or `75` or `90`"
    ),
    "admin_custom_dur_invalid": "⚠️ Please enter a valid duration between 1 and 1440 minutes (e.g., 45).",
    "admin_results_dash": (
        "📊 *Competition Results*\n\n"
        "🏆 *{title}*\n\n"
        "👥 *Registered:* {registered}\n"
        "▶️ *Started:* {started}\n"
        "⏳ *In Progress:* {in_progress}\n"
        "✅ *Completed:* {completed}\n"
        "⌛ *Expired:* {expired}\n\n"
        "📈 *Completion Rate:* {rate}%\n"
        "🏅 *Top Score:* {top_score}\n\n"
        "*Status:* `{status}`"
    ),
    "admin_btn_announce": "📢 Announcement",
    "admin_confirm_broadcast": (
        "📢 *Confirm Announcement Broadcast*\n\n"
        "*Preview:*\n"
        "----------------------------------------\n"
        "{text}\n"
        "----------------------------------------\n\n"
        "Send this announcement to all {count} registered participants?"
    ),
    "btn_confirm_broadcast": "✅ Confirm Broadcast",
    "btn_cancel": "❌ Cancel",
    "membership_account_already_bound": "❌ This Telegram account is already linked to a verified EMYC membership.",
    "admin_btn_participants": "👥 Participants",
    "admin_btn_rankings": "🏅 View Rankings",
    "admin_btn_sys_status": "💻 System Status",
    "admin_btn_participant_view": "👤 Switch to Participant View",
    "admin_btn_publish_results": "🏆 Publish Results",
    "admin_btn_create_comp": "➕ Create Competition",
    "admin_btn_refresh": "🔄 Refresh",
    "admin_btn_back": "◀️ Back to Admin Panel",
    "review_answers_btn": "📖 Review Answers",
    "my_result_btn": "🏆 My Result",
    "back_to_my_result_btn": "🏆 Back to My Result",
    "review_your_answer": "📌 *Your Answer:*",
    "review_correct_answer": "✅ *Correct Answer:*",
    "review_result_label": "📊 *Result:*",
    "review_correct": "✅ Correct (+{points} pts)",
    "review_incorrect": "❌ Incorrect (0 pts)",
    "review_not_answered": "⚪ Not answered (0 pts)",
    "results_pending_notice": "⏳ *Results Pending*\n\nYour exam has been submitted successfully. The official results and rankings have not been published yet. Please check back once the administration publishes the final results!",
    "btn_prev": "◀️ Prev",
    "btn_next": "Next ▶️",
}
