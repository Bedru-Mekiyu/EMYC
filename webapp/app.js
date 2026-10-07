/**
 * Ethiopian Muslim Youth Council (EMYC)
 * Telegram Mini App High-Throughput Exam Engine
 * Feature-Complete Client: Offline-First, Audio Effects, Practice Mode, Language Picker & Accessible Zoom
 */

(function () {
  'use strict';

  // 1. Telegram WebApp Integration & Haptics
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.enableClosingConfirmation();
    } catch (e) {
      console.warn("Closing confirmation not supported on this client", e);
    }
  }

  // Audio Effects Synthesizer (Web Audio API - 100% offline, zero audio files required)
  let soundEnabled = true;
  let audioCtx = null;

  function playSound(type) {
    if (!soundEnabled) return;
    try {
      if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      }
      if (audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.connect(gain);
      gain.connect(audioCtx.destination);

      const now = audioCtx.currentTime;
      if (type === 'tap') {
        osc.frequency.setValueAtTime(440, now);
        osc.frequency.exponentialRampToValueAtTime(880, now + 0.05);
        gain.gain.setValueAtTime(0.12, now);
        gain.gain.exponentialRampToValueAtTime(0.01, now + 0.05);
        osc.start(now);
        osc.stop(now + 0.05);
      } else if (type === 'flag') {
        osc.frequency.setValueAtTime(800, now);
        osc.frequency.exponentialRampToValueAtTime(1200, now + 0.08);
        gain.gain.setValueAtTime(0.15, now);
        gain.gain.exponentialRampToValueAtTime(0.01, now + 0.08);
        osc.start(now);
        osc.stop(now + 0.08);
      } else if (type === 'success') {
        // Ascending chime
        [523.25, 659.25, 783.99, 1046.50].forEach((freq, i) => {
          const o = audioCtx.createOscillator();
          const g = audioCtx.createGain();
          o.connect(g);
          g.connect(audioCtx.destination);
          o.frequency.value = freq;
          g.gain.setValueAtTime(0.15, now + i * 0.1);
          g.gain.exponentialRampToValueAtTime(0.01, now + i * 0.1 + 0.2);
          o.start(now + i * 0.1);
          o.stop(now + i * 0.1 + 0.2);
        });
      }
    } catch (e) {
      // Audio optional
    }
  }

  function triggerHaptic(type = 'light') {
    try {
      if (type === 'light' || type === 'medium' || type === 'heavy') {
        tg?.HapticFeedback?.impactOccurred(type);
      } else if (type === 'success' || type === 'warning' || type === 'error') {
        tg?.HapticFeedback?.notificationOccurred(type);
      } else if (type === 'selection') {
        tg?.HapticFeedback?.selectionChanged();
      }
    } catch (e) {
      // Haptics optional on desktop
    }
  }

  // 2. Localization Setup & Dynamic Switching
  let currentLang = localStorage.getItem('emyc_pref_lang') || 'en';
  const userLang = tg?.initDataUnsafe?.user?.language_code;
  if (!localStorage.getItem('emyc_pref_lang') && userLang && ['am', 'om', 'ar'].includes(userLang)) {
    currentLang = userLang;
  }

  function t(key, vars = {}) {
    const strings = window.EMYC_LOCALES?.[currentLang] || window.EMYC_LOCALES?.['en'] || {};
    let str = strings[key] || window.EMYC_LOCALES?.['en']?.[key] || key;
    for (const [k, v] of Object.entries(vars)) {
      str = str.replace(`{${k}}`, v);
    }
    return str;
  }

  function setLanguage(langCode) {
    if (!['en', 'am', 'om', 'ar'].includes(langCode)) return;
    currentLang = langCode;
    localStorage.setItem('emyc_pref_lang', langCode);

    const langLabel = document.getElementById('lbl-current-lang');
    if (langLabel) {
      const codeMap = { en: 'EN', am: 'አማ', om: 'ORO', ar: 'عربي' };
      langLabel.textContent = codeMap[langCode] || 'EN';
    }

    applyStaticLocales();
    if (questions.length > 0) {
      showQuestion(currentIndex);
    }
    closeModal('modal-lang');
  }

  function applyStaticLocales() {
    const mappings = {
      'lbl-title': 'title',
      'lbl-loading': 'loading',
      'lbl-prev': 'prev',
      'lbl-next': 'next',
      'lbl-clear': 'clear',
      'lbl-finish': 'finish',
      'lbl-matrix-title': 'review_title',
      'lbl-legend-ans': 'answered_count',
      'lbl-legend-unans': 'unanswered_count',
      'lbl-legend-flagged': 'flagged_count',
      'lbl-finish-from-grid': 'finish',
      'lbl-confirm-title': 'confirm_title',
      'lbl-confirm-msg': 'confirm_msg',
      'btn-cancel-submit': 'cancel',
      'btn-do-submit': 'confirm_btn',
      'btn-exit': 'close_app',
      'lbl-instr-title': 'instructions_title',
      'lbl-instr-desc': 'instructions_desc',
      'lbl-rule-q-label': 'instructions_q_count',
      'lbl-rule-time-label': 'instructions_duration',
      'lbl-rule-1': 'rule_1',
      'lbl-rule-2': 'rule_2',
      'lbl-rule-3': 'rule_3',
      'lbl-start-exam-now': 'start_exam_now',
      'lbl-jump-unans': 'jump_unanswered',
      'lbl-jump-flagged': 'jump_flagged',
      'lbl-lang-modal-title': 'lang_selector_title',
      'btn-practice-trigger': 'practice_btn',
    };
    for (const [id, key] of Object.entries(mappings)) {
      const el = document.getElementById(id);
      if (el) el.textContent = t(key);
    }
  }

  // 3. Application State
  let session = null;
  let questions = [];
  let answers = {};
  let flags = new Set();
  let currentIndex = 0;
  let timerInterval = null;
  let deadlineEpoch = null;
  let isSubmitting = false;
  let isPracticeMode = false;
  let activeFilter = 'all'; // 'all', 'unans', 'flagged'

  // Backend API URL (Targeting Render deployment)
  const BACKEND_URL = 'https://emyc.onrender.com';

  function getInitData() {
    if (tg?.initData) return tg.initData;
    const search = window.location.search;
    if (search && search.includes('hash=')) {
      return search.startsWith('?') ? search.slice(1) : search;
    }
    return 'user=%7B%22id%22%3A12345678%2C%22username%22%3A%22teststudent%22%2C%22first_name%22%3A%22Student%22%7D&auth_date=1791211002&hash=test_valid_mock_hash';
  }

  const authHeaders = {
    'Content-Type': 'application/json',
    'X-Telegram-Init-Data': getInitData(),
  };

  function getStorageKeys(attemptId) {
    return {
      sessionKey: `emyc_exam_sess_${attemptId}`,
      answersKey: `emyc_exam_ans_${attemptId}`,
      flagsKey: `emyc_exam_flags_${attemptId}`,
    };
  }

  // Network Status Banner
  function showNetworkToast(isOnline) {
    const toast = document.getElementById('toast-network');
    const toastText = document.getElementById('toast-text');
    const toastIcon = document.getElementById('toast-icon');
    if (!toast) return;

    if (isOnline) {
      toast.className = 'network-toast visible online';
      if (toastIcon) toastIcon.textContent = '🟢';
      if (toastText) toastText.textContent = t('online_notice');
      setTimeout(() => toast.classList.remove('visible'), 3000);
    } else {
      toast.className = 'network-toast visible offline';
      if (toastIcon) toastIcon.textContent = '📶';
      if (toastText) toastText.textContent = t('offline_notice');
    }
  }

  window.addEventListener('online', () => showNetworkToast(true));
  window.addEventListener('offline', () => showNetworkToast(false));

  // 4. Session Loading & Initialization
  async function initExamSession() {
    applyStaticLocales();
    showView('loading');

    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/webapp/session`, {
        method: 'GET',
        headers: authHeaders,
      });

      if (!res.ok) {
        if (res.status === 401) {
          showError("Authentication Notice", "To take the official competition, please launch this app directly inside your Telegram Bot chat.", true, true);
          return;
        }
        if (res.status === 403) {
          showError(t('not_live_title'), t('not_live_desc'), true, true);
          return;
        }
        throw new Error(`HTTP ${res.status}`);
      }

      const data = await res.json();

      // Case A: Competition not live or scheduled
      if (data.status === 'not_live' || data.status === 'scheduled') {
        showError(t('not_live_title'), data.message || t('not_live_desc'), true, true);
        return;
      }

      // Case B: Already submitted
      if (data.status === 'already_submitted') {
        showResultView({
          score: data.score,
          correct_count: data.score,
          total_questions: data.total_questions,
          submitted_at: data.submitted_at,
        });
        return;
      }

      // Case C: Exam Ready -> Show Instructions Sheet First
      if (data.status === 'ready') {
        session = data;
        questions = data.questions || [];
        isPracticeMode = false;

        // Restore local state
        const keys = getStorageKeys(session.attempt_id);
        const cachedAns = localStorage.getItem(keys.answersKey);
        if (cachedAns) {
          try { answers = JSON.parse(cachedAns) || {}; } catch (e) { answers = {}; }
        }
        if (data.saved_answers) {
          answers = { ...data.saved_answers, ...answers };
        }

        const cachedFlags = localStorage.getItem(keys.flagsKey);
        if (cachedFlags) {
          try { flags = new Set(JSON.parse(cachedFlags) || []); } catch (e) { flags = new Set(); }
        }

        const pName = document.getElementById('lbl-participant');
        if (pName && data.participant_name) pName.textContent = data.participant_name;

        // Populate Instructions Sheet
        const qCountEl = document.getElementById('instr-total-q');
        const durEl = document.getElementById('instr-duration');
        if (qCountEl) qCountEl.textContent = questions.length;
        if (durEl) durEl.textContent = `${data.duration_minutes || 60} mins`;

        showView('instructions');
      }
    } catch (err) {
      console.warn("Server connection failed, offering practice mode:", err);
      showError(t('error_title'), "Unable to connect to exam server at this moment. You can test your phone in Practice Mode below.", true, true);
    }
  }

  // 5. Practice Exam Mode (Interactive 5-Question Demo)
  function startPracticeExam() {
    isPracticeMode = true;
    session = {
      attempt_id: 'practice_' + Date.now(),
      competition_title: 'EMYC Practice Test',
      duration_minutes: 5,
      time_left_seconds: 300,
    };

    questions = [
      {
        question_id: 'prac_1',
        display_order: 1,
        question_text: 'In which holy month is the fast (Sawm) observed by Muslims worldwide?',
        options: { A: 'Shawwal', B: 'Ramadan', C: 'Muharram', D: 'Rajab' },
        correct_option: 'B',
      },
      {
        question_id: 'prac_2',
        display_order: 2,
        question_text: 'How many daily obligatory (Fard) prayers are prescribed in Islam?',
        options: { A: '3', B: '4', C: '5', D: '7' },
        correct_option: 'C',
      },
      {
        question_id: 'prac_3',
        display_order: 3,
        question_text: 'Which historical city in Ethiopia is famous for being the site of the First Hijrah in Islam?',
        options: { A: 'Harar', B: 'Negash', C: 'Gondar', D: 'Dire Dawa' },
        correct_option: 'B',
      },
      {
        question_id: 'prac_4',
        display_order: 4,
        question_text: 'What is the highest and most sacred pillar of Islamic worship after Tawheed?',
        options: { A: 'Salah (Prayer)', B: 'Zakat (Charity)', C: 'Hajj (Pilgrimage)', D: 'Sadaqah' },
        correct_option: 'A',
      },
      {
        question_id: 'prac_5',
        display_order: 5,
        question_text: 'Which Surah of the Holy Quran is known as the "Mother of the Book" (Umm al-Kitab)?',
        options: { A: 'Surah Al-Baqarah', B: 'Surah Ya-Sin', C: 'Surah Al-Fatihah', D: 'Surah Al-Ikhlas' },
        correct_option: 'C',
      },
    ];

    answers = {};
    flags = new Set();
    currentIndex = 0;

    setupCountdown(300);
    buildMatrix();
    showQuestion(0);
    showView('exam');
  }

  function startExamFromInstructions() {
    if (!session) return;
    setupCountdown(session.time_left_seconds || 3600);
    buildMatrix();
    showQuestion(0);
    showView('exam');
  }

  // 6. Timer Engine
  function setupCountdown(timeLeftSeconds) {
    if (timerInterval) clearInterval(timerInterval);
    deadlineEpoch = Date.now() + timeLeftSeconds * 1000;

    function tick() {
      const now = Date.now();
      const remainingMs = Math.max(0, deadlineEpoch - now);
      const remainingSec = Math.floor(remainingMs / 1000);

      const m = Math.floor(remainingSec / 60);
      const s = remainingSec % 60;
      const fmt = `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;

      const timerText = document.getElementById('timer-text');
      const timerBox = document.getElementById('timer-box');
      if (timerText) timerText.textContent = fmt;

      if (timerBox) {
        timerBox.classList.remove('warning', 'danger');
        if (remainingSec <= 180) {
          timerBox.classList.add('danger');
        } else if (remainingSec <= 600) {
          timerBox.classList.add('warning');
        }
      }

      if (remainingSec <= 0) {
        clearInterval(timerInterval);
        submitExamBatch(true);
      }
    }

    tick();
    timerInterval = setInterval(tick, 1000);
  }

  // 7. Render Question
  function showQuestion(index) {
    if (index < 0 || index >= questions.length) return;
    currentIndex = index;
    const q = questions[index];

    // Badge and count
    const badge = document.getElementById('lbl-question-badge');
    if (badge) badge.textContent = `${t('question')} ${index + 1} ${t('of')} ${questions.length}`;

    // Flag button state
    const flagBtn = document.getElementById('btn-flag-toggle');
    const flagText = document.getElementById('lbl-flag-text');
    const isFlagged = flags.has(q.question_id);
    if (flagBtn) flagBtn.className = `btn-flag ${isFlagged ? 'flagged' : ''}`;
    if (flagText) flagText.textContent = isFlagged ? t('unflag') : t('flag');

    // Status pill
    const statusPill = document.getElementById('lbl-answer-status');
    const selected = answers[q.question_id];
    if (statusPill) {
      statusPill.textContent = selected ? `Selected: ${selected}` : "Unanswered";
      statusPill.style.color = selected ? "var(--accent-emerald)" : "var(--text-secondary)";
    }

    // Question Text
    const qText = document.getElementById('question-text');
    if (qText) qText.textContent = q.question_text;

    // Render Options
    const optContainer = document.getElementById('options-container');
    if (optContainer) {
      optContainer.innerHTML = '';
      const opts = q.options || {};
      for (const letter of ['A', 'B', 'C', 'D']) {
        if (!opts[letter]) continue;
        const btn = document.createElement('div');
        btn.className = `option-btn ${selected === letter ? 'selected' : ''}`;
        btn.innerHTML = `
          <div class="option-letter">${letter}</div>
          <div class="option-text">${opts[letter]}</div>
        `;
        btn.addEventListener('click', () => {
          playSound('tap');
          triggerHaptic('selection');
          selectOption(q.question_id, letter);
        });
        optContainer.appendChild(btn);
      }
    }

    // Update Navigation Buttons
    const btnPrev = document.getElementById('btn-prev');
    const btnNext = document.getElementById('btn-next');
    if (btnPrev) btnPrev.disabled = (currentIndex === 0);
    if (btnNext) btnNext.disabled = (currentIndex === questions.length - 1);

    // Update Progress Bar
    const answeredCount = Object.keys(answers).length;
    const pct = (answeredCount / Math.max(1, questions.length)) * 100;
    const fill = document.getElementById('progress-fill');
    if (fill) fill.style.width = `${pct}%`;

    updateMatrixCells();
  }

  // 8. Select & Persist Answer (Offline-First)
  function selectOption(questionId, letter) {
    answers[questionId] = letter;

    if (!isPracticeMode && session?.attempt_id) {
      const keys = getStorageKeys(session.attempt_id);
      localStorage.setItem(keys.answersKey, JSON.stringify(answers));
    }
    showQuestion(currentIndex);
  }

  // 9. Clear Selected Answer
  function clearCurrentOption() {
    playSound('tap');
    triggerHaptic('light');
    const q = questions[currentIndex];
    if (!q) return;
    delete answers[q.question_id];

    if (!isPracticeMode && session?.attempt_id) {
      const keys = getStorageKeys(session.attempt_id);
      localStorage.setItem(keys.answersKey, JSON.stringify(answers));
    }
    showQuestion(currentIndex);
  }

  // 10. Toggle Bookmark / Flag
  function toggleCurrentFlag() {
    playSound('flag');
    triggerHaptic('medium');
    const q = questions[currentIndex];
    if (!q) return;

    if (flags.has(q.question_id)) {
      flags.delete(q.question_id);
    } else {
      flags.add(q.question_id);
    }

    if (!isPracticeMode && session?.attempt_id) {
      const keys = getStorageKeys(session.attempt_id);
      localStorage.setItem(keys.flagsKey, JSON.stringify(Array.from(flags)));
    }
    showQuestion(currentIndex);
  }

  // 11. Question Matrix / Navigator Modal
  function buildMatrix() {
    const grid = document.getElementById('matrix-container');
    if (!grid) return;
    grid.innerHTML = '';

    questions.forEach((q, idx) => {
      const cell = document.createElement('div');
      cell.id = `matrix-cell-${idx}`;
      cell.className = 'grid-cell';
      cell.textContent = idx + 1;
      cell.addEventListener('click', () => {
        playSound('tap');
        triggerHaptic('light');
        closeModal('modal-grid');
        showQuestion(idx);
      });
      grid.appendChild(cell);
    });
    updateMatrixCells();
  }

  function updateMatrixCells() {
    let unansCount = 0;
    let flaggedCount = 0;

    questions.forEach((q, idx) => {
      const cell = document.getElementById(`matrix-cell-${idx}`);
      if (!cell) return;

      const isAns = !!answers[q.question_id];
      const isFlag = flags.has(q.question_id);
      const isCur = (idx === currentIndex);

      if (!isAns) unansCount++;
      if (isFlag) flaggedCount++;

      cell.className = 'grid-cell';
      if (isAns) cell.classList.add('answered');
      if (isCur) cell.classList.add('current');
      if (isFlag) cell.classList.add('flagged-marker');

      let visible = true;
      if (activeFilter === 'unans' && isAns) visible = false;
      if (activeFilter === 'flagged' && !isFlag) visible = false;
      cell.style.display = visible ? 'flex' : 'none';
    });

    const cAll = document.getElementById('count-all');
    const cUnans = document.getElementById('count-unans');
    const cFlag = document.getElementById('count-flagged');
    if (cAll) cAll.textContent = questions.length;
    if (cUnans) cUnans.textContent = unansCount;
    if (cFlag) cFlag.textContent = flaggedCount;

    const gridBtn = document.getElementById('lbl-grid');
    if (gridBtn) {
      const answered = Object.keys(answers).length;
      gridBtn.textContent = `${answered}/${questions.length}`;
    }
  }

  function setMatrixFilter(filterType) {
    triggerHaptic('light');
    activeFilter = filterType;
    ['all', 'unans', 'flagged'].forEach(f => {
      const tab = document.getElementById(`tab-filter-${f}`);
      if (tab) {
        if (f === filterType) tab.classList.add('active');
        else tab.classList.remove('active');
      }
    });
    updateMatrixCells();
  }

  function jumpToFirstUnanswered() {
    const idx = questions.findIndex(q => !answers[q.question_id]);
    if (idx !== -1) {
      closeModal('modal-grid');
      showQuestion(idx);
    }
  }

  function jumpToFirstFlagged() {
    const idx = questions.findIndex(q => flags.has(q.question_id));
    if (idx !== -1) {
      closeModal('modal-grid');
      showQuestion(idx);
    }
  }

  // 12. Submit Batch Answers (Single Transaction)
  async function submitExamBatch(isAutoTimeout = false) {
    if (isSubmitting || !session) return;
    isSubmitting = true;

    if (timerInterval) clearInterval(timerInterval);

    closeModal('modal-grid');
    closeModal('modal-confirm');

    // Handle Practice Mode client-side
    if (isPracticeMode) {
      let score = 0;
      questions.forEach(q => {
        if (answers[q.question_id] === q.correct_option) score++;
      });
      playSound('success');
      triggerHaptic('success');
      isSubmitting = false;
      showResultView({
        score: score,
        correct_count: score,
        incorrect_count: questions.length - score,
        total_questions: questions.length,
        completion_seconds: Math.round((Date.now() - (deadlineEpoch - 300000)) / 1000),
      });
      return;
    }

    showView('loading');
    const loadingLbl = document.getElementById('lbl-loading');
    if (loadingLbl) loadingLbl.textContent = t('submitting');

    const batchAnswers = questions
      .map(q => ({
        question_id: q.question_id,
        display_order: q.display_order,
        selected_option: answers[q.question_id] || "",
      }))
      .filter(a => a.selected_option !== "");

    const payload = {
      attempt_id: session.attempt_id,
      answers: batchAnswers,
    };

    try {
      const res = await fetch(`${BACKEND_URL}/api/v1/webapp/submit`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const result = await res.json();
      playSound('success');
      triggerHaptic('success');

      const keys = getStorageKeys(session.attempt_id);
      localStorage.removeItem(keys.answersKey);
      localStorage.removeItem(keys.flagsKey);
      localStorage.removeItem(keys.sessionKey);

      showResultView(result);
    } catch (err) {
      console.error("Batch submit failed:", err);
      triggerHaptic('error');
      isSubmitting = false;
      showError(
        t('error_title'),
        `${t('offline_notice')} Tap Retry to resend.`,
        true,
        false,
        () => submitExamBatch(isAutoTimeout)
      );
    }
  }

  // 13. Modal Helpers & Submission Summary
  function openModal(modalId) {
    triggerHaptic('light');
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.add('active');
  }

  function closeModal(modalId) {
    triggerHaptic('light');
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.remove('active');
  }

  function showSubmitConfirmation() {
    triggerHaptic('medium');
    const answeredCount = Object.keys(answers).length;
    const unansweredCount = questions.length - answeredCount;
    const flaggedCount = flags.size;

    const ansNum = document.getElementById('confirm-stat-answered');
    const unansNum = document.getElementById('confirm-stat-unanswered');
    const flagNum = document.getElementById('confirm-stat-flagged');
    if (ansNum) ansNum.textContent = answeredCount;
    if (unansNum) unansNum.textContent = unansweredCount;
    if (flagNum) flagNum.textContent = flaggedCount;

    const warnBox = document.getElementById('confirm-warning-box');
    if (warnBox) {
      if (unansweredCount > 0) {
        warnBox.style.display = 'block';
        warnBox.textContent = `⚠️ Warning: You have ${unansweredCount} unanswered questions out of ${questions.length}.`;
      } else {
        warnBox.style.display = 'none';
      }
    }
    openModal('modal-confirm');
  }

  // 14. Screen View Management
  function showView(viewName) {
    const views = ['loading', 'instructions', 'exam', 'result', 'error'];
    views.forEach(v => {
      const el = document.getElementById(`view-${v}`);
      if (el) el.style.display = (v === viewName ? 'flex' : 'none');
    });

    const bottomBar = document.getElementById('bottom-bar');
    if (bottomBar) bottomBar.style.display = (viewName === 'exam' ? 'flex' : 'none');
  }

  function showResultView(result) {
    showView('result');
    const scoreEl = document.getElementById('result-score');
    if (scoreEl) scoreEl.textContent = `${result.score ?? '--'} / ${result.total_questions ?? questions.length}`;

    const correctEl = document.getElementById('result-correct');
    if (correctEl) correctEl.textContent = result.correct_count ?? result.score ?? 0;

    const incorrectEl = document.getElementById('result-incorrect');
    if (incorrectEl) incorrectEl.textContent = result.incorrect_count ?? 0;

    const timeEl = document.getElementById('result-time');
    if (timeEl && result.completion_seconds != null) {
      const sec = Math.round(result.completion_seconds);
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      timeEl.textContent = `${m}m ${s}s`;
    }

    const practiceAgainBtn = document.getElementById('btn-practice-again');
    if (practiceAgainBtn) {
      practiceAgainBtn.style.display = isPracticeMode ? 'inline-flex' : 'none';
    }
  }

  function showError(title, desc, allowRetry, allowPractice, retryFn) {
    showView('error');
    const tEl = document.getElementById('error-title');
    const dEl = document.getElementById('error-desc');
    const btn = document.getElementById('btn-error-action');
    const pracBtn = document.getElementById('btn-practice-trigger');

    if (tEl) tEl.textContent = title;
    if (dEl) dEl.textContent = desc;

    if (pracBtn) {
      pracBtn.style.display = allowPractice ? 'inline-flex' : 'none';
    }

    if (btn) {
      if (allowRetry) {
        btn.textContent = t('retry_btn');
        btn.style.display = 'inline-flex';
        btn.onclick = () => {
          triggerHaptic('medium');
          if (retryFn) retryFn();
          else initExamSession();
        };
      } else {
        btn.textContent = t('close_app');
        btn.onclick = () => {
          if (tg) tg.close();
        };
      }
    }
  }

  // 15. Header Controls (Audio, Zoom, Language)
  function toggleSound() {
    soundEnabled = !soundEnabled;
    const btn = document.getElementById('btn-sound-toggle');
    if (btn) btn.textContent = soundEnabled ? '🔊' : '🔇';
  }

  function toggleFontSize() {
    const body = document.body;
    if (body.classList.contains('font-large')) {
      body.classList.remove('font-large');
      body.classList.add('font-small');
    } else if (body.classList.contains('font-small')) {
      body.classList.remove('font-small');
    } else {
      body.classList.add('font-large');
    }
  }

  // 16. Event Listeners Wiring
  document.getElementById('btn-prev')?.addEventListener('click', () => {
    playSound('tap');
    triggerHaptic('light');
    if (currentIndex > 0) showQuestion(currentIndex - 1);
  });

  document.getElementById('btn-next')?.addEventListener('click', () => {
    playSound('tap');
    triggerHaptic('light');
    if (currentIndex < questions.length - 1) showQuestion(currentIndex + 1);
  });

  document.getElementById('btn-clear')?.addEventListener('click', clearCurrentOption);
  document.getElementById('btn-flag-toggle')?.addEventListener('click', toggleCurrentFlag);

  document.getElementById('btn-grid')?.addEventListener('click', () => {
    playSound('tap');
    updateMatrixCells();
    openModal('modal-grid');
  });
  document.getElementById('btn-close-grid')?.addEventListener('click', () => closeModal('modal-grid'));

  // Filter Tabs
  document.getElementById('tab-filter-all')?.addEventListener('click', () => setMatrixFilter('all'));
  document.getElementById('tab-filter-unans')?.addEventListener('click', () => setMatrixFilter('unans'));
  document.getElementById('tab-filter-flagged')?.addEventListener('click', () => setMatrixFilter('flagged'));

  // Quick Jumps
  document.getElementById('btn-jump-first-unans')?.addEventListener('click', jumpToFirstUnanswered);
  document.getElementById('btn-jump-first-flagged')?.addEventListener('click', jumpToFirstFlagged);

  // Language Modal
  document.getElementById('btn-lang-picker')?.addEventListener('click', () => openModal('modal-lang'));
  document.getElementById('btn-close-lang')?.addEventListener('click', () => closeModal('modal-lang'));
  document.querySelectorAll('.lang-choice-card').forEach(card => {
    card.addEventListener('click', () => {
      const code = card.getAttribute('data-lang');
      if (code) setLanguage(code);
    });
  });

  // Sound & Zoom
  document.getElementById('btn-sound-toggle')?.addEventListener('click', toggleSound);
  document.getElementById('btn-font-toggle')?.addEventListener('click', toggleFontSize);

  // Start exam from instructions
  document.getElementById('btn-start-exam-now')?.addEventListener('click', () => {
    playSound('tap');
    startExamFromInstructions();
  });

  // Practice Mode triggers
  document.getElementById('btn-practice-trigger')?.addEventListener('click', () => {
    playSound('tap');
    startPracticeExam();
  });
  document.getElementById('btn-practice-again')?.addEventListener('click', () => {
    playSound('tap');
    startPracticeExam();
  });

  // Submission Dialog
  document.getElementById('btn-finish')?.addEventListener('click', showSubmitConfirmation);
  document.getElementById('btn-finish-from-grid')?.addEventListener('click', () => {
    closeModal('modal-grid');
    showSubmitConfirmation();
  });

  document.getElementById('btn-close-confirm')?.addEventListener('click', () => closeModal('modal-confirm'));
  document.getElementById('btn-cancel-submit')?.addEventListener('click', () => closeModal('modal-confirm'));
  document.getElementById('btn-do-submit')?.addEventListener('click', () => submitExamBatch(false));

  document.getElementById('btn-exit')?.addEventListener('click', () => {
    if (tg) tg.close();
  });

  // Initial language setup & startup
  setLanguage(currentLang);
  initExamSession();
})();
