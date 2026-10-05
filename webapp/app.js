/**
 * Ethiopian Muslim Youth Council (EMYC)
 * Telegram Mini App High-Throughput Exam Engine
 * Built to Amazon Principal Engineer Standards for 100,000 Concurrent Examinees.
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
      // Haptics optional on desktop browsers
    }
  }

  // 2. Localization Setup
  let currentLang = 'en';
  const userLang = tg?.initDataUnsafe?.user?.language_code;
  if (userLang && ['am', 'om', 'ar'].includes(userLang)) {
    currentLang = userLang;
  }
  const strings = window.EMYC_LOCALES?.[currentLang] || window.EMYC_LOCALES?.['en'] || {};

  function t(key, vars = {}) {
    let str = strings[key] || key;
    for (const [k, v] of Object.entries(vars)) {
      str = str.replace(`{${k}}`, v);
    }
    return str;
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
  let activeFilter = 'all'; // 'all', 'unans', 'flagged'

  // Network Toast Notification
  function showNetworkToast(isOnline) {
    const toast = document.getElementById('toast-network');
    const toastText = document.getElementById('toast-text');
    const toastIcon = document.getElementById('toast-icon');
    if (!toast) return;

    if (isOnline) {
      toast.className = 'network-toast visible online';
      if (toastIcon) toastIcon.textContent = '🟢';
      if (toastText) toastText.textContent = t('online_notice');
      setTimeout(() => {
        toast.classList.remove('visible');
      }, 3000);
    } else {
      toast.className = 'network-toast visible offline';
      if (toastIcon) toastIcon.textContent = '📶';
      if (toastText) toastText.textContent = t('offline_notice');
    }
  }

  window.addEventListener('online', () => showNetworkToast(true));
  window.addEventListener('offline', () => showNetworkToast(false));

  // Resolve initData for authentication
  function getInitData() {
    if (tg?.initData) return tg.initData;
    const search = window.location.search;
    if (search && search.includes('hash=')) {
      return search.startsWith('?') ? search.slice(1) : search;
    }
    // Fallback mock test initData for local dev preview
    return 'user=%7B%22id%22%3A12345678%2C%22username%22%3A%22teststudent%22%2C%22first_name%22%3A%22Student%22%7D&auth_date=1791211002&hash=test_valid_mock_hash';
  }

  // Backend API base URL.
  // When deployed on Cloudflare Pages, set this to your Render backend URL.
  // Example: 'https://your-app-name.onrender.com'
  // Leave empty ('') when the backend serves the webapp/ folder directly.
   const BACKEND_URL = 'https://emyc.onrender.com'; 

  const authHeaders = {
    'Content-Type': 'application/json',
    'X-Telegram-Init-Data': getInitData(),
  };

  // 4. Offline Storage Keys
  function getStorageKeys(attemptId) {
    return {
      sessionKey: `emyc_exam_sess_${attemptId}`,
      answersKey: `emyc_exam_ans_${attemptId}`,
      flagsKey: `emyc_exam_flags_${attemptId}`,
    };
  }

  // 5. Bootstrap Session
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
          showError("Authentication Error", "Invalid Telegram session. Please relaunch the exam from the Telegram bot.", false);
          return;
        }
        if (res.status === 403) {
          showError(t('not_live_title'), t('not_live_desc'), false);
          return;
        }
        throw new Error(`HTTP ${res.status}`);
      }

      const data = await res.json();

      // Case A: Competition not live or scheduled
      if (data.status === 'not_live' || data.status === 'scheduled') {
        showError(t('not_live_title'), data.message || t('not_live_desc'), false);
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

      // Case C: Exam Ready
      if (data.status === 'ready') {
        session = data;
        questions = data.questions || [];

        // Restore any local offline answers & flags
        const keys = getStorageKeys(session.attempt_id);
        const cachedAns = localStorage.getItem(keys.answersKey);
        if (cachedAns) {
          try {
            answers = JSON.parse(cachedAns) || {};
          } catch (e) {
            answers = {};
          }
        }
        if (data.saved_answers) {
          answers = { ...data.saved_answers, ...answers };
        }

        const cachedFlags = localStorage.getItem(keys.flagsKey);
        if (cachedFlags) {
          try {
            flags = new Set(JSON.parse(cachedFlags) || []);
          } catch (e) {
            flags = new Set();
          }
        }

        const pName = document.getElementById('lbl-participant');
        if (pName && data.participant_name) pName.textContent = data.participant_name;

        setupCountdown(data.time_left_seconds);
        buildMatrix();
        showQuestion(0);
        showView('exam');
      }
    } catch (err) {
      console.error("Failed to load exam session:", err);
      showError(t('error_title'), "Unable to connect to the exam server. Please check your connection and tap Retry.", true);
    }
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
    if (flagBtn) {
      flagBtn.className = `btn-flag ${isFlagged ? 'flagged' : ''}`;
    }
    if (flagText) {
      flagText.textContent = isFlagged ? t('unflag') : t('flag');
    }

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

    if (session?.attempt_id) {
      const keys = getStorageKeys(session.attempt_id);
      localStorage.setItem(keys.answersKey, JSON.stringify(answers));
    }
    showQuestion(currentIndex);
  }

  // 9. Clear Selected Answer
  function clearCurrentOption() {
    triggerHaptic('light');
    const q = questions[currentIndex];
    if (!q) return;
    delete answers[q.question_id];

    if (session?.attempt_id) {
      const keys = getStorageKeys(session.attempt_id);
      localStorage.setItem(keys.answersKey, JSON.stringify(answers));
    }
    showQuestion(currentIndex);
  }

  // 10. Toggle Bookmark / Flag
  function toggleCurrentFlag() {
    triggerHaptic('medium');
    const q = questions[currentIndex];
    if (!q) return;

    if (flags.has(q.question_id)) {
      flags.delete(q.question_id);
    } else {
      flags.add(q.question_id);
    }

    if (session?.attempt_id) {
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

      // Filter visibility
      let visible = true;
      if (activeFilter === 'unans' && isAns) visible = false;
      if (activeFilter === 'flagged' && !isFlag) visible = false;
      cell.style.display = visible ? 'flex' : 'none';
    });

    // Counts update
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

  // 12. Submit Batch Answers (Single Transaction)
  async function submitExamBatch(isAutoTimeout = false) {
    if (isSubmitting || !session) return;
    isSubmitting = true;

    if (timerInterval) clearInterval(timerInterval);

    closeModal('modal-grid');
    closeModal('modal-confirm');

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

      triggerHaptic('success');

      // Clear local cache for this attempt
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
        () => submitExamBatch(isAutoTimeout)
      );
    }
  }

  // 13. Modal Helpers
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
    const views = ['loading', 'exam', 'result', 'error'];
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
  }

  function showError(title, desc, allowRetry, retryFn) {
    showView('error');
    const tEl = document.getElementById('error-title');
    const dEl = document.getElementById('error-desc');
    const btn = document.getElementById('btn-error-action');

    if (tEl) tEl.textContent = title;
    if (dEl) dEl.textContent = desc;

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

  // 15. Event Listeners Wiring
  document.getElementById('btn-prev')?.addEventListener('click', () => {
    triggerHaptic('light');
    if (currentIndex > 0) showQuestion(currentIndex - 1);
  });

  document.getElementById('btn-next')?.addEventListener('click', () => {
    triggerHaptic('light');
    if (currentIndex < questions.length - 1) showQuestion(currentIndex + 1);
  });

  document.getElementById('btn-clear')?.addEventListener('click', clearCurrentOption);
  document.getElementById('btn-flag-toggle')?.addEventListener('click', toggleCurrentFlag);

  document.getElementById('btn-grid')?.addEventListener('click', () => {
    updateMatrixCells();
    openModal('modal-grid');
  });
  document.getElementById('btn-close-grid')?.addEventListener('click', () => closeModal('modal-grid'));

  // Filter Tabs
  document.getElementById('tab-filter-all')?.addEventListener('click', () => setMatrixFilter('all'));
  document.getElementById('tab-filter-unans')?.addEventListener('click', () => setMatrixFilter('unans'));
  document.getElementById('tab-filter-flagged')?.addEventListener('click', () => setMatrixFilter('flagged'));

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

  // Start initialization
  initExamSession();
})();
