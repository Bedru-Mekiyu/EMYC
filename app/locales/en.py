MESSAGES = {
    "welcome": "🏆 *Competitive Exam Platform*\n\nWelcome to the official competitive examination bot.",
    "start_btn": "▶️ Start",
    "change_lang_btn": "🌐 Change Language",
    "help_btn": "❓ Help",
    "select_language": "Please select your preferred language:",
    "language_updated": "✅ Language updated successfully.",
    "help_text": (
        "🏆 *How it works:*\n\n"
        "1. Verify your Membership ID (e.g., EMYC/4055828/2026).\n"
        "2. Review the competition details and tap Start Competition.\n"
        "3. Answer each question carefully before your individual timer expires.\n"
        "4. Your exam is automatically submitted when time runs out or upon final question submission.\n"
        "5. Official results will be published once the competition concludes."
    ),
    "membership_prompt": "Please enter your Membership ID to proceed:\n_(e.g., EMYC/4055828/2026)_",
    "membership_invalid_format": "❌ Invalid Membership ID format. Example: EMYC/4055828/2026",
    "membership_not_found": "❌ Membership ID could not be verified in the membership database.",
    "membership_already_bound": "❌ This Membership ID is already bound to another Telegram account.",
    "membership_verified": "✅ Membership verified!\nAccount linked to: `{membership_id}`.",
    "competition_not_open": "⏳ The competition is not open yet.\nOpens: {opens_at}\nCloses: {closes_at}",
    "competition_closed": "🚫 This competition has closed.",
    "start_exam_btn": "▶️ Start Competition",
    "exam_info": (
        "📋 *Competition Information*\n\n"
        "*Title:* {title}\n"
        "*Questions:* {questions}\n"
        "*Duration:* {duration} minutes\n"
        "*Closing Time:* {closes_at}\n\n"
        "Press below when you are ready. Your countdown will begin immediately!"
    ),
    "already_submitted": "✅ You have already completed and submitted your exam.",
    "question_header": "*Question {current} / {total}*\n⏱ *Time remaining:* {time_left}",
    "exam_submitted": (
        "✅ *Exam Submitted*\n\n"
        "Your submission has been recorded.\n"
        "Official results will be announced after the competition closes."
    ),
    "results_pending": "⏳ Official results are not published yet. Please check back after official announcement.",
    "results_title": (
        "🏆 *Official Results*\n\n"
        "*Score:* {score} / {total}\n"
        "*Rank:* #{rank}\n"
        "*Time:* {time}"
    ),
    "view_result_btn": "📊 View Result",
    "view_correct_btn": "✅ Correct Answers ({count})",
    "view_incorrect_btn": "❌ Incorrect Answers ({count})",
    "back_to_menu_btn": "🏠 Main Menu",
    "no_incorrect": "🎉 Perfect score! You have no incorrect answers.",
    "unauthorized_admin": "⛔ Unauthorized access.",
    "admin_menu_title": "🛠 *Admin Control Panel*",
    "admin_btn_competition": "🏆 Competition",
    "admin_btn_status": "📊 Status",
    "admin_btn_results": "🏆 Results",
    "admin_btn_announce": "📢 Announce",
}
