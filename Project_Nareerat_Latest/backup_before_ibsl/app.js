// IBSL Policy FAQ RAG — Light Dashboard Frontend Application
// Matches UI Reference Theme and User Requirements

let currentTab = 'home';
let currentSearchData = null;
let currentEvaluations = {}; // item_id -> 1 (relevant) or 0 (not relevant)

// On DOM Loaded
document.addEventListener('DOMContentLoaded', () => {
  if (window.lucide) {
    lucide.createIcons();
  }
  switchTab('home');
});

// Tab Switching (Home -> Search -> Metrics)
function switchTab(tabName) {
  currentTab = tabName;

  const viewHome = document.getElementById('view-home');
  const viewSearch = document.getElementById('view-search');
  const viewMetrics = document.getElementById('view-metrics');
  const viewFields = document.getElementById('view-fields');
  const viewFaqs = document.getElementById('view-faqs');
  const breadcrumb = document.getElementById('breadcrumb-current');
  const btnBackHome = document.getElementById('btn-back-home');
  const btnEnterSearchTop = document.getElementById('btn-enter-search-top');

  // Hide all views
  viewHome.classList.add('hidden');
  viewSearch.classList.add('hidden');
  viewMetrics.classList.add('hidden');
  if (viewFields) viewFields.classList.add('hidden');
  if (viewFaqs) viewFaqs.classList.add('hidden');

  // Reset navigation icons
  const navItems = ['home', 'search', 'fields', 'faqs', 'metrics'];
  navItems.forEach(name => {
    const btn = document.getElementById(`nav-btn-${name}`);
    if (btn) {
      if (name === tabName) {
        btn.className = 'w-10 h-10 rounded-2xl flex items-center justify-center transition-all duration-200 text-blue-600 bg-blue-50 shadow-sm';
      } else {
        btn.className = 'w-10 h-10 rounded-2xl flex items-center justify-center transition-all duration-200 text-slate-400 hover:text-slate-600 hover:bg-slate-50';
      }
    }
  });

  if (tabName === 'home') {
    viewHome.classList.remove('hidden');
    if (breadcrumb) breadcrumb.innerText = 'หน้าหลัก';
    if (btnBackHome) btnBackHome.classList.add('hidden');
    if (btnEnterSearchTop) btnEnterSearchTop.classList.remove('hidden');
  } else if (tabName === 'search') {
    viewSearch.classList.remove('hidden');
    if (breadcrumb) breadcrumb.innerText = 'ค้นหาคำตอบ';
    if (btnBackHome) btnBackHome.classList.remove('hidden');
    if (btnEnterSearchTop) btnEnterSearchTop.classList.add('hidden');
    
    // Requirement 2: Do NOT show answers prematurely if search hasn't been executed
    if (!currentSearchData) {
      document.getElementById('search-results-area').classList.add('hidden');
      document.getElementById('search-empty').classList.add('hidden');
      document.getElementById('search-loading').classList.add('hidden');
    }
    
    loadSearchPageMetrics();

    const input = document.getElementById('search-input');
    if (input) setTimeout(() => input.focus(), 100);
  } else if (tabName === 'metrics') {
    viewMetrics.classList.remove('hidden');
    if (breadcrumb) breadcrumb.innerText = 'ผลการประเมินประสิทธิภาพระบบ';
    if (btnBackHome) btnBackHome.classList.remove('hidden');
    if (btnEnterSearchTop) btnEnterSearchTop.classList.remove('hidden');
    loadMetricsData();
  } else if (tabName === 'fields') {
    if (viewFields) viewFields.classList.remove('hidden');
    if (breadcrumb) breadcrumb.innerText = 'รายการฟิลด์ข้อมูล';
    if (btnBackHome) btnBackHome.classList.remove('hidden');
    if (btnEnterSearchTop) btnEnterSearchTop.classList.remove('hidden');
    loadFieldsCatalog();
  } else if (tabName === 'faqs') {
    if (viewFaqs) viewFaqs.classList.remove('hidden');
    if (breadcrumb) breadcrumb.innerText = 'รายการ FAQ';
    if (btnBackHome) btnBackHome.classList.remove('hidden');
    if (btnEnterSearchTop) btnEnterSearchTop.classList.remove('hidden');
    loadFaqsCatalog();
  }

  if (window.lucide) {
    lucide.createIcons();
  }
}

// Set Query from Example Chips and Execute Search
function setQueryAndSearch(queryText) {
  if (currentTab !== 'search') {
    switchTab('search');
  }
  const input = document.getElementById('search-input');
  if (input) {
    input.value = queryText;
    handleSearch();
  }
}

function clearSearchInput() {
  const input = document.getElementById('search-input');
  if (input) {
    input.value = '';
    input.focus();
  }
  const clearBtn = document.getElementById('btn-clear-search');
  if (clearBtn) clearBtn.classList.add('hidden');

  // Requirement 2: Hide results area when cleared
  document.getElementById('search-results-area').classList.add('hidden');
  document.getElementById('search-empty').classList.add('hidden');
  document.getElementById('search-loading').classList.add('hidden');
  currentSearchData = null;
  currentEvaluations = {};
}

// Execute RAG Search
async function handleSearch() {
  const input = document.getElementById('search-input');
  const query = input ? input.value.trim() : '';

  if (!query) {
    alert('กรุณาระบุชื่อข้อมูล หรือรายละเอียดข้อมูลที่ต้องการค้นหา');
    return;
  }

  const clearBtn = document.getElementById('btn-clear-search');
  if (clearBtn) clearBtn.classList.remove('hidden');

  // UI States (Requirement 2: only show results after search execution)
  document.getElementById('search-loading').classList.remove('hidden');
  document.getElementById('search-empty').classList.add('hidden');
  document.getElementById('search-results-area').classList.add('hidden');

  currentEvaluations = {};
  currentSearchData = null;

  try {
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: query })
    });

    document.getElementById('search-loading').classList.add('hidden');

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'เกิดข้อผิดพลาดในการค้นหา');
    }

    const data = await res.json();
    currentSearchData = data;

    if (!data.items || data.items.length === 0) {
      document.getElementById('search-empty').classList.remove('hidden');
      return;
    }

    renderSearchResults(data);

  } catch (err) {
    document.getElementById('search-loading').classList.add('hidden');
    alert(`เกิดข้อผิดพลาด: ${err.message}`);
  }
}

// Render Results: Fields with embedded FAQ answers, with Overall AI Answer at the Bottom
function renderSearchResults(data) {
  const resultsArea = document.getElementById('search-results-area');
  resultsArea.classList.remove('hidden');

  const fields = data.fields || data.items?.filter(it => it.type === 'Field') || [];

  // 1. Display Search Query in indicator banner prominently
  const queryDisplay = document.getElementById('search-query-display');
  if (queryDisplay) {
    queryDisplay.innerText = data.query || '';
  }

  const queryTypeBadge = document.getElementById('query-type-badge');
  if (queryTypeBadge) {
    const qType = data.query_type || (fields.length > 0 && fields[0].is_name_match ? 'ชื่อรายการฟิลด์' : 'คำอธิบายข้อมูล');
    queryTypeBadge.innerText = qType;
    if (qType === 'ชื่อรายการฟิลด์') {
      queryTypeBadge.className = 'px-3 py-1 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200 shadow-xs';
    } else {
      queryTypeBadge.className = 'px-3 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 shadow-xs';
    }
  }

  const fieldsCountBadge = document.getElementById('fields-count-badge');
  if (fieldsCountBadge) fieldsCountBadge.innerText = `พบ ${fields.length} ฟิลด์ข้อมูล`;

  // Human-in-the-Loop: tell the user when earlier evaluations changed these results
  const feedbackNote = document.getElementById('feedback-applied-note');
  if (feedbackNote) {
    const fb = data.feedback_applied || {};
    const parts = [];
    if (fb.promoted > 0) parts.push(`จัดไว้อันดับต้น ${fb.promoted} รายการ`);
    if (fb.hidden > 0) parts.push(`ซ่อน ${fb.hidden} รายการที่ถูกประเมินว่าไม่เกี่ยวข้อง`);
    feedbackNote.innerText = parts.length ? `ปรับตามผลประเมินของผู้ใช้: ${parts.join(' • ')}` : '';
    feedbackNote.classList.toggle('hidden', parts.length === 0);
  }

  const fieldsSectionBadge = document.getElementById('fields-section-badge');
  if (fieldsSectionBadge) fieldsSectionBadge.innerText = `${fields.length} รายการ`;

  // 2. SECTION 1: Render Fields List (เอาข้อมูลรายการฟิลด์ขึ้นก่อน และเพิ่มคำตอบตามข้อมูลของ FAQ)
  const fieldsContainer = document.getElementById('retrieved-fields-list');
  if (fieldsContainer) {
    fieldsContainer.innerHTML = '';
    fields.forEach((item, index) => {
      const rankNo = index + 1;
      const itemId = item.item_id;
      const tableName = item.table || 'ตาราง Policy';
      const faqAnswers = item.faq_answers || [];

      // Generate FAQ Answer Box inside this field card
      let faqBoxHtml = '';
      if (faqAnswers.length > 0) {
        const faqListHtml = faqAnswers.map((faq, fIdx) => `
          <div class="p-4 rounded-2xl bg-white border border-indigo-200/90 shadow-xs mb-3 last:mb-0">
            <div class="flex flex-wrap items-center justify-between gap-2 mb-2 pb-2 border-b border-indigo-100">
              <div class="flex items-center gap-2">
                <span class="w-5 h-5 rounded-md bg-indigo-600 text-white text-[11px] font-bold flex items-center justify-center">${fIdx + 1}</span>
                <span class="font-bold text-indigo-950 text-xs md:text-sm">คำถาม FAQ: ${escapeHtml(faq.question)}</span>
              </div>
              <span class="text-[10px] font-mono text-indigo-700 bg-indigo-50 border border-indigo-100 px-2 py-0.5 rounded">${escapeHtml(faq.item_id)}</span>
            </div>
            <div class="text-xs md:text-sm text-slate-800 whitespace-pre-line leading-relaxed pl-3.5 border-l-4 border-indigo-500 bg-indigo-50/40 py-2.5 rounded-r-xl font-sans">
              <span class="font-bold text-indigo-900 block mb-1">คำตอบตามข้อมูลของ FAQ:</span>
              ${escapeHtml(faq.answer)}
            </div>
            <div class="text-[11px] text-slate-400 mt-2 flex items-center gap-1.5">
              <i data-lucide="book-open" class="w-3.5 h-3.5"></i>
              <span>แหล่งอ้างอิง: ${escapeHtml(faq.source)}</span>
            </div>
          </div>
        `).join('');

        faqBoxHtml = `
          <div class="p-4 md:p-5 rounded-2xl bg-gradient-to-br from-indigo-50/90 to-blue-50/60 border border-indigo-200/90 mb-4 shadow-sm">
            <div class="flex flex-wrap items-center justify-between gap-2 font-bold text-indigo-950 mb-3 pb-2 border-b border-indigo-100">
              <div class="flex items-center gap-2">
                <div class="w-6 h-6 rounded-lg bg-indigo-600 text-white flex items-center justify-center shadow-xs">
                  <i data-lucide="help-circle" class="w-3.5 h-3.5"></i>
                </div>
                <span class="text-xs md:text-sm">คำตอบตามข้อมูลของ FAQ ที่สอดคล้องกับฟิลด์ ${escapeHtml(item.field_name)}:</span>
              </div>
              <span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-indigo-100 text-indigo-800 border border-indigo-200">
                พบ ${faqAnswers.length} คำตอบตาม FAQ
              </span>
            </div>
            <div class="space-y-3">
              ${faqListHtml}
            </div>
          </div>
        `;
      } else {
        faqBoxHtml = `
          <div class="p-3.5 rounded-2xl bg-slate-50 border border-slate-200/80 text-xs text-slate-500 mb-4 flex items-center gap-2.5">
            <i data-lucide="info" class="w-4 h-4 text-slate-400 flex-shrink-0"></i>
            <span>ยังไม่มีคำตอบเพิ่มเติมจาก FAQ สำหรับฟิลด์นี้ โดยให้ปฏิบัติตามมาตรฐานโครงสร้างข้อมูลข้างต้น</span>
          </div>
        `;
      }

      const card = document.createElement('div');
      card.className = 'p-6 rounded-3xl bg-white border border-slate-200/80 shadow-[0_4px_20px_rgba(0,0,0,0.03)] hover:border-slate-300 transition';
      card.id = `retrieved-card-${itemId}`;

      card.innerHTML = `
        <!-- Card Header -->
        <div class="flex flex-wrap items-start justify-between gap-3 mb-4">
          <div class="flex items-start gap-3">
            <span class="w-8 h-8 rounded-xl bg-blue-600 text-white font-bold text-xs flex items-center justify-center shadow-sm flex-shrink-0 mt-0.5">
              ${rankNo}
            </span>
            <div>
              <h4 class="font-bold text-slate-900 text-base md:text-lg flex flex-wrap items-center gap-2">
                <span class="font-mono text-blue-700">${escapeHtml(item.field_name || itemId)}</span>
                ${item.desc ? `<span class="text-slate-700 font-semibold">(${escapeHtml(item.desc)})</span>` : ''}
                <span class="text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
                  ${escapeHtml(tableName)}
                </span>
              </h4>
              <div class="text-xs text-slate-400 mt-1 flex flex-wrap items-center gap-2">
                <span class="font-mono text-slate-700 bg-slate-100 px-2 py-0.5 rounded text-[11px] font-semibold">${itemId}</span>
                <span>•</span>
                <span>${escapeHtml(item.source)}</span>
              </div>
            </div>
          </div>

          <!-- Similarity Badge (+ Human-in-the-Loop badge) -->
          <div class="text-right flex-shrink-0 flex flex-col items-end gap-1.5">
            ${item.user_feedback > 0 ? `
            <div class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200" title="ผู้ใช้ประเมินว่าเกี่ยวข้องกับคำค้นนี้ จึงจัดไว้อันดับต้น">
              <i data-lucide="user-check" class="w-3.5 h-3.5"></i>
              <span>ผู้ใช้ยืนยันว่าเกี่ยวข้อง</span>
            </div>` : ''}
            <div class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200">
              <span>${item.user_feedback > 0 && !item.similarity_score ? 'เพิ่มจากผลประเมินของผู้ใช้' : `ความใกล้เคียง: ${item.similarity_score}%`}</span>
            </div>
          </div>
        </div>

        <!-- Raw Metadata Box (ข้อมูลรายการฟิลด์) -->
        <div class="p-3.5 rounded-2xl bg-slate-50 border border-slate-100 text-xs text-slate-700 whitespace-pre-line leading-relaxed mb-4 font-mono">
${escapeHtml(item.content)}
        </div>

        <!-- FAQ Answers Box (เพิ่มคำตอบตามข้อมูลของ FAQ ที่สอดคล้องกับฟิลด์) -->
        ${faqBoxHtml}

        <!-- AI Explanation Specific to this Field -->
        <div class="p-4 md:p-5 rounded-2xl bg-blue-50/60 border border-blue-100/90 text-xs text-slate-800 leading-relaxed mb-4 shadow-sm">
          <div class="flex items-center gap-2 font-bold text-blue-900 mb-2">
            <i data-lucide="sparkles" class="w-4 h-4 text-blue-600"></i>
            <span>คำอธิบายและข้อแนะนำจาก AI สำหรับฟิลด์นี้:</span>
          </div>
          <p class="whitespace-pre-line text-slate-700 text-xs md:text-sm font-normal leading-relaxed">
            ${escapeHtml(item.ai_explanation || 'ไม่มีคำอธิบายเพิ่มเติม')}
          </p>
        </div>

        <!-- Human-in-the-Loop Relevance Rating -->
        <div class="pt-3 border-t border-slate-100 flex flex-wrap items-center justify-between gap-3">
          <span class="text-xs font-medium text-slate-500">การประเมินความเกี่ยวข้องของรายการฟิลด์นี้:</span>
          <div class="flex items-center gap-2">
            <button 
              type="button" 
              onclick="setRelevance('${itemId}', 1)"
              id="rel-btn-${itemId}-1"
              class="px-3.5 py-1.5 rounded-xl text-xs font-medium border border-slate-200 text-slate-600 hover:border-emerald-500 hover:text-emerald-700 hover:bg-emerald-50 transition flex items-center gap-1.5"
            >
              <i data-lucide="check" class="w-3.5 h-3.5"></i>
              <span>เกี่ยวข้องกับคำถาม</span>
            </button>
            <button 
              type="button" 
              onclick="setRelevance('${itemId}', 0)"
              id="rel-btn-${itemId}-0"
              class="px-3.5 py-1.5 rounded-xl text-xs font-medium border border-slate-200 text-slate-600 hover:border-rose-500 hover:text-rose-700 hover:bg-rose-50 transition flex items-center gap-1.5"
            >
              <i data-lucide="x" class="w-3.5 h-3.5"></i>
              <span>ไม่เกี่ยวข้องกับคำถาม</span>
            </button>
          </div>
        </div>
      `;

      fieldsContainer.appendChild(card);
    });
  }

  // 3. SECTION 2: Synthesized Overall AI Answer (เอาคำตอบโดยรวมจาก AI ไว้ล่างสุดของผลการค้นหา)
  const answerEl = document.getElementById('synthesized-answer-text');
  if (answerEl) {
    answerEl.innerText = data.answer || 'ไม่พบคำตอบที่สังเคราะห์ได้';
  }

  const sourceBadge = document.getElementById('answer-source-badge');
  if (sourceBadge) {
    if (data.answer_source === 'llm_gemini') {
      sourceBadge.className = 'px-3 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 shadow-sm';
      sourceBadge.innerText = 'สังเคราะห์โดย Gemini AI (อ้างอิง Excel)';
    } else if (data.answer_source === 'llm_openai') {
      sourceBadge.className = 'px-3 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 shadow-sm';
      sourceBadge.innerText = 'สังเคราะห์โดย OpenAI (อ้างอิง Excel)';
    } else {
      sourceBadge.className = 'px-3 py-1 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200 shadow-sm';
      sourceBadge.innerText = 'ข้อมูลต้นฉบับจากฐานความรู้ (อ้างอิง Excel)';
    }
  }

  // 5. Reset Comment Field
  const commentEl = document.getElementById('eval-comment-input');
  if (commentEl) commentEl.value = '';
  updateEvaluationHint();

  if (window.lucide) {
    lucide.createIcons();
  }
}

// User Relevance Rating Handler
function setRelevance(itemId, relevanceVal) {
  currentEvaluations[itemId] = relevanceVal;

  const btn1 = document.getElementById(`rel-btn-${itemId}-1`);
  const btn0 = document.getElementById(`rel-btn-${itemId}-0`);

  if (btn1 && btn0) {
    if (relevanceVal === 1) {
      btn1.className = 'px-3.5 py-1.5 rounded-xl text-xs font-bold border-2 border-emerald-500 text-emerald-800 bg-emerald-100/70 shadow-sm flex items-center gap-1.5';
      btn0.className = 'px-3.5 py-1.5 rounded-xl text-xs font-medium border border-slate-200 text-slate-400 bg-white hover:bg-slate-50 transition flex items-center gap-1.5';
    } else {
      btn0.className = 'px-3.5 py-1.5 rounded-xl text-xs font-bold border-2 border-rose-500 text-rose-800 bg-rose-100/70 shadow-sm flex items-center gap-1.5';
      btn1.className = 'px-3.5 py-1.5 rounded-xl text-xs font-medium border border-slate-200 text-slate-400 bg-white hover:bg-slate-50 transition flex items-center gap-1.5';
    }
  }

  updateEvaluationHint();
}

function updateEvaluationHint() {
  const hintEl = document.getElementById('eval-status-hint');
  if (!hintEl || !currentSearchData) return;

  const totalItems = currentSearchData.items.length;
  const evaluatedCount = Object.keys(currentEvaluations).length;

  if (evaluatedCount === totalItems) {
    hintEl.className = 'text-xs text-emerald-700 bg-emerald-50 px-3 py-1.5 rounded-xl border border-emerald-200 font-semibold flex items-center gap-1';
    hintEl.innerHTML = `<i data-lucide="check" class="w-3.5 h-3.5"></i> ประเมินครบ ${totalItems}/${totalItems} รายการแล้ว พร้อมบันทึก`;
  } else {
    hintEl.className = 'text-xs text-slate-500 bg-white px-3 py-1.5 rounded-xl border border-slate-200';
    hintEl.innerText = `ประเมินแล้ว ${evaluatedCount}/${totalItems} รายการ`;
  }
  if (window.lucide) lucide.createIcons();
}

// Submit Human Evaluation (HITL) for Report Data (Requirement 5)
async function submitOverallEvaluation() {
  if (!currentSearchData || !currentSearchData.query_id) {
    alert('ไม่มีข้อมูลคำค้นหาปัจจุบัน');
    return;
  }

  const queryId = currentSearchData.query_id;
  const items = currentSearchData.items;

  const evals = [];
  items.forEach(it => {
    if (it.item_id in currentEvaluations) {
      evals.push({
        item_id: it.item_id,
        relevance: currentEvaluations[it.item_id]
      });
    }
  });

  if (evals.length === 0) {
    alert('กรุณาเลือกประเมินความเกี่ยวข้องในการ์ดผลลัพธ์อย่างน้อย 1 รายการก่อนกดบันทึก');
    return;
  }

  const commentInput = document.getElementById('eval-comment-input');
  const comment = commentInput ? commentInput.value : '';

  const submitBtn = document.getElementById('btn-submit-eval');
  const origHtml = submitBtn.innerHTML;
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<span>กำลังบันทึก...</span>';

  try {
    const res = await fetch('/api/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query_id: queryId,
        evaluations: evals,
        comment: comment
      })
    });

    submitBtn.disabled = false;
    submitBtn.innerHTML = origHtml;

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'ไม่สามารถบันทึกผลการประเมินได้');
    }

    alert('บันทึกผลการประเมินของผู้ใช้สำเร็จ! ข้อมูลจะถูกนำไปคำนวณ Precision@3, Recall@3, F1@3 ในหน้าผลประเมินเพื่อใช้ประกอบรายงาน');

  } catch (err) {
    submitBtn.disabled = false;
    submitBtn.innerHTML = origHtml;
    alert(`เกิดข้อผิดพลาด: ${err.message}`);
  }
}

// Delete Single Evaluation Record (Requirement 3: เพิ่มฟังก์ชันให้ลบ)
async function deleteSingleEvaluation(queryId) {
  if (!confirm(`คุณต้องการลบประวัติคำค้นรหัส ${queryId} ใช่หรือไม่?`)) return;

  try {
    const res = await fetch(`/api/evaluate/${queryId}`, { method: 'DELETE' });
    if (res.ok) {
      loadMetricsData();
    } else {
      alert('ไม่สามารถลบรายการได้');
    }
  } catch (err) {
    alert(`เกิดข้อผิดพลาด: ${err.message}`);
  }
}

// Clear All User Evaluations (Requirement 3: เพิ่มฟังก์ชันให้ล้างทั้งหมด)
async function confirmClearAllEvaluations() {
  if (!confirm('คุณต้องการล้างประวัติการประเมินของผู้ใช้ทั้งหมดใช่หรือไม่? (การคำนวณสถิติจะถูกรีเซ็ตใหม่สำหรับเริ่มเก็บข้อมูลรอบใหม่)')) return;

  try {
    const res = await fetch('/api/evaluate', { method: 'DELETE' });
    if (res.ok) {
      loadMetricsData();
    } else {
      alert('ไม่สามารถล้างประวัติทั้งหมดได้');
    }
  } catch (err) {
    alert(`เกิดข้อผิดพลาด: ${err.message}`);
  }
}

// ==============================================================
// Field Catalog (รายการฟิลด์ข้อมูล): the knowledge base the RAG engine searches
// ==============================================================
let fieldsCatalog = null;
let fieldsCatalogOpenId = null;

async function loadFieldsCatalog() {
  if (fieldsCatalog) {
    renderFieldsCatalog();
    return;
  }
  const body = document.getElementById('fields-catalog-body');
  try {
    const res = await fetch('/api/fields');
    if (!res.ok) throw new Error('ไม่สามารถโหลดรายการฟิลด์ได้');
    fieldsCatalog = await res.json();

    const tableSelect = document.getElementById('fields-filter-table');
    if (tableSelect) {
      fieldsCatalog.tables.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t.table;
        opt.textContent = `${t.table} (${t.field_count})`;
        tableSelect.appendChild(opt);
      });
    }
    renderFieldsCatalog();
  } catch (err) {
    console.error('Field catalog error:', err);
    if (body) body.innerHTML = `<tr><td colspan="6" class="px-5 py-8 text-center text-rose-500">${escapeHtml(err.message)}</td></tr>`;
  }
}

function requiredBadge(field) {
  const styles = {
    'Yes': 'bg-blue-50 text-blue-700 border-blue-200',
    'Yes(C)': 'bg-amber-50 text-amber-700 border-amber-200',
    'No': 'bg-slate-50 text-slate-500 border-slate-200'
  };
  const cls = styles[field.required] || styles['No'];
  return `<span class="inline-block whitespace-nowrap px-2.5 py-0.5 rounded-full text-[11px] font-semibold border ${cls}">${escapeHtml(field.required_label || '-')}</span>`;
}

function renderFieldsCatalog() {
  if (!fieldsCatalog) return;
  const body = document.getElementById('fields-catalog-body');
  const countEl = document.getElementById('fields-catalog-count');
  const text = (document.getElementById('fields-filter-text')?.value || '').trim().toLowerCase();
  const table = document.getElementById('fields-filter-table')?.value || '';
  const required = document.getElementById('fields-filter-required')?.value || '';

  const rows = fieldsCatalog.fields.filter(f => {
    if (table && f.table !== table) return false;
    if (required && f.required !== required) return false;
    if (!text) return true;
    const haystack = [f.field_name, f.desc, ...(f.aliases || [])].join(' ').toLowerCase();
    return haystack.includes(text);
  });

  if (countEl) countEl.innerText = `แสดง ${rows.length} จาก ${fieldsCatalog.total} ฟิลด์`;
  if (!body) return;

  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="6" class="px-5 py-8 text-center text-slate-400">ไม่พบฟิลด์ที่ตรงกับคำค้นหา</td></tr>';
    return;
  }

  body.innerHTML = rows.map((f, idx) => {
    const isOpen = fieldsCatalogOpenId === f.item_id;
    const dtype = f.dtype
      ? `${escapeHtml(f.dtype)}${f.length ? ` <span class="text-slate-400">(${escapeHtml(f.length)})</span>` : ''}`
      : '-';
    const row = `
      <tr class="cursor-pointer transition ${isOpen ? 'bg-blue-50/60' : 'hover:bg-slate-50'}" onclick="toggleFieldDetail('${f.item_id}')">
        <td class="px-4 py-3 font-mono text-slate-400">${idx + 1}</td>
        <td class="px-4 py-3 font-mono font-semibold text-blue-700 break-all">${escapeHtml(f.field_name)}</td>
        <td class="px-4 py-3 text-slate-700 min-w-[12rem]">${escapeHtml(f.desc || '-')}</td>
        <td class="px-4 py-3 text-slate-500 hidden md:table-cell">${escapeHtml(f.table)}</td>
        <td class="px-4 py-3 font-mono text-slate-600 hidden sm:table-cell whitespace-nowrap">${dtype}</td>
        <td class="px-4 py-3 text-center">${requiredBadge(f)}</td>
      </tr>`;
    return isOpen ? row + fieldDetailRow(f) : row;
  }).join('');

  if (window.lucide) lucide.createIcons();
}

function fieldDetailRow(f) {
  const item = (label, value) => value
    ? `<div><dt class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">${label}</dt><dd class="mt-0.5 text-slate-700 whitespace-pre-line break-words">${escapeHtml(value)}</dd></div>`
    : '';
  const faqs = (f.related_faqs || []).map(q => `
    <li class="flex gap-2">
      <span class="font-mono text-[11px] font-semibold text-indigo-600 shrink-0">${escapeHtml(q.item_id)}</span>
      <span class="text-slate-600">${escapeHtml(q.question.split('\n')[0])}</span>
    </li>`).join('');

  return `
    <tr class="bg-blue-50/40">
      <td colspan="6" class="px-4 md:px-6 py-4">
        <dl class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 text-xs">
          ${item('ตาราง', f.table)}
          ${item('ประเภทข้อมูล', f.dtype ? f.dtype + (f.length ? ` (ความยาว ${f.length})` : '') : '')}
          ${item('ตัวอย่างข้อมูล', f.example)}
          ${item('Master Data', f.master_data)}
          ${item('ข้อมูลอ่อนไหว (Sensitive)', f.sensitive)}
          ${item('คำค้นที่เกี่ยวข้อง', (f.aliases || []).join(', '))}
          <div class="md:col-span-2">${item('เงื่อนไขการส่งข้อมูล', f.condition)}</div>
        </dl>
        ${faqs ? `
        <div class="mt-4">
          <p class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">FAQ ที่เกี่ยวข้อง (${f.related_faqs.length})</p>
          <ul class="space-y-1 text-xs">${faqs}</ul>
        </div>` : ''}
        <p class="mt-4 text-[11px] text-slate-400">${escapeHtml(f.source)}</p>
      </td>
    </tr>`;
}

function toggleFieldDetail(itemId) {
  fieldsCatalogOpenId = fieldsCatalogOpenId === itemId ? null : itemId;
  renderFieldsCatalog();
}

// ==============================================================
// FAQ Catalog (รายการ FAQ): all in-scope Policy FAQs in the knowledge base
// ==============================================================
let faqsCatalog = null;

async function loadFaqsCatalog() {
  if (faqsCatalog) {
    renderFaqsCatalog();
    return;
  }
  const body = document.getElementById('faqs-catalog-body');
  try {
    const res = await fetch('/api/faqs');
    if (!res.ok) throw new Error('ไม่สามารถโหลดรายการ FAQ ได้');
    faqsCatalog = await res.json();
    renderFaqsCatalog();
  } catch (err) {
    console.error('FAQ catalog error:', err);
    if (body) body.innerHTML = `<tr><td colspan="3" class="px-5 py-8 text-center text-rose-500">${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderFaqsCatalog() {
  if (!faqsCatalog) return;
  const body = document.getElementById('faqs-catalog-body');
  const countEl = document.getElementById('faqs-catalog-count');
  const text = (document.getElementById('faqs-filter-text')?.value || '').trim().toLowerCase();

  const rows = (faqsCatalog.faqs || []).filter(q => {
    if (!text) return true;
    const haystack = [q.item_id, q.question, q.answer].join(' ').toLowerCase();
    return haystack.includes(text);
  });

  if (countEl) countEl.innerText = `แสดง ${rows.length} จาก ${faqsCatalog.total} รายการ`;
  if (!body) return;

  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="3" class="px-5 py-8 text-center text-slate-400">ไม่พบรายการ FAQ ที่ตรงกับคำค้นหา</td></tr>';
    return;
  }

  body.innerHTML = rows.map(q => `
    <tr class="hover:bg-slate-50 transition align-top">
      <td class="px-4 py-3.5 font-mono font-semibold text-indigo-700 whitespace-nowrap">${escapeHtml(q.item_id)}</td>
      <td class="px-4 py-3.5 text-slate-800 font-medium whitespace-pre-line leading-relaxed min-w-[16rem]">${escapeHtml(q.question)}</td>
      <td class="px-4 py-3.5 text-slate-700 whitespace-pre-line leading-relaxed min-w-[22rem]">${escapeHtml(q.answer)}</td>
    </tr>
  `).join('');

  if (window.lucide) lucide.createIcons();
}

// Ground Truth Precision/Recall/F1@3 strip under the search box
async function loadSearchPageMetrics() {
  const els = {
    precision: document.getElementById('search-metric-precision'),
    recall: document.getElementById('search-metric-recall'),
    f1: document.getElementById('search-metric-f1')
  };
  try {
    const res = await fetch('/api/metrics');
    if (!res.ok) throw new Error('ไม่สามารถโหลดข้อมูลสถิติได้');
    const gtEv = (await res.json()).ground_truth || {};
    const fmt = v => gtEv.available ? `${(v * 100).toFixed(1)}%` : '--%';
    if (els.precision) els.precision.innerText = fmt(gtEv.macro_precision_at_3);
    if (els.recall) els.recall.innerText = fmt(gtEv.macro_recall_at_3);
    if (els.f1) els.f1.innerText = fmt(gtEv.macro_f1_at_3);
  } catch (err) {
    console.error('Search page metrics error:', err);
  }
}

// Load Evaluation Metrics Calculated from User Evaluation (Requirement 5)
async function loadMetricsData() {
  try {
    const res = await fetch('/api/metrics');
    if (!res.ok) throw new Error('ไม่สามารถโหลดข้อมูลสถิติได้');

    const data = await res.json();
    const userEv = data.user_evaluation || {};
    const gtEv = data.ground_truth || {};

    const p3El = document.getElementById('metric-precision-val');
    const r3El = document.getElementById('metric-recall-val');
    const f1El = document.getElementById('metric-f1-val');

    // The 3 KPI cards come from the GROUND TRUTH benchmark (data/ground_truth.json)
    if (gtEv.available) {
      if (p3El) p3El.innerText = `${(gtEv.macro_precision_at_3 * 100).toFixed(1)}%`;
      if (r3El) r3El.innerText = `${(gtEv.macro_recall_at_3 * 100).toFixed(1)}%`;
      if (f1El) f1El.innerText = `${(gtEv.macro_f1_at_3 * 100).toFixed(1)}%`;
    } else {
      const naText = '<span class="text-xs font-normal text-slate-400">ยังไม่มีข้อมูลสำหรับคำนวณ (ไม่พบชุดทดสอบ Ground Truth)</span>';
      if (p3El) p3El.innerHTML = naText;
      if (r3El) r3El.innerHTML = naText;
      if (f1El) f1El.innerHTML = naText;
    }

    // Populate User Evaluation Table with Delete Actions (Requirement 3)
    const userCountEl = document.getElementById('user-eval-count');
    if (userCountEl) userCountEl.innerText = `${userEv.evaluated_queries_count || 0} รายการ`;

    const userPrecEl = document.getElementById('user-precision-avg');
    if (userPrecEl) {
      userPrecEl.innerText = userEv.macro_precision_at_3 != null
        ? `User P@3: ${(userEv.macro_precision_at_3 * 100).toFixed(1)}%`
        : 'User P@3: --';
    }

    const userTbody = document.getElementById('user-eval-table-body');
    if (userTbody) {
      userTbody.innerHTML = '';
      const userList = userEv.evaluated_queries_list || [];
      if (userList.length === 0) {
        userTbody.innerHTML = `
          <tr>
            <td colspan="9" class="px-5 py-8 text-center text-slate-400 text-xs">
              ยังไม่มีประวัติการประเมินจากผู้ใช้งาน สามารถทดลองค้นหาและประเมินผลในหน้าค้นหาเพื่อเริ่มสะสมข้อมูลสถิติสำหรับรายงาน
            </td>
          </tr>
        `;
      } else {
        userList.forEach(item => {
          const tr = document.createElement('tr');
          tr.className = 'hover:bg-slate-50 transition';
          tr.innerHTML = `
            <td class="px-5 py-3 text-slate-400 font-mono text-[11px]">${escapeHtml(item.search_time || '-')}</td>
            <td class="px-5 py-3 font-semibold text-slate-800">${escapeHtml(item.query_text)}</td>
            <td class="px-5 py-3 text-center text-slate-600 font-mono">${item.displayed_count}</td>
            <td class="px-5 py-3 text-center text-emerald-600 font-bold font-mono">${item.relevant_count}/${item.displayed_count}${item.evaluated_count < item.displayed_count ? ` <span class="text-[10px] font-normal text-slate-400">(ประเมิน ${item.evaluated_count})</span>` : ''}</td>
            <td class="px-5 py-3 text-center font-mono font-bold text-slate-700">${item.precision_all != null ? `${(item.precision_all * 100).toFixed(1)}%` : '-'}</td>
            <td class="px-5 py-3 text-center text-emerald-600 font-bold font-mono">${item.top_relevant_count}/${item.top_count}</td>
            <td class="px-5 py-3 text-center font-mono font-bold text-blue-600">${(item.precision_at_3 * 100).toFixed(1)}%</td>
            <td class="px-5 py-3 text-slate-500 italic text-[11px]">${escapeHtml(item.feedback_comment || '-')}</td>
            <td class="px-4 py-3 text-center">
              <button onclick="deleteSingleEvaluation(${item.query_id})" class="p-1.5 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition" title="ลบรายการนี้">
                <i data-lucide="trash-2" class="w-4 h-4"></i>
              </button>
            </td>
          `;
          userTbody.appendChild(tr);
        });
      }
    }

    // Populate Ground Truth Reference Benchmark Table
    const gtTbody = document.getElementById('gt-table-body');
    if (gtTbody) {
      gtTbody.innerHTML = '';
      const cases = gtEv.test_cases || [];
      if (cases.length === 0) {
        gtTbody.innerHTML = `
          <tr>
            <td colspan="8" class="px-4 py-6 text-center text-slate-400 text-xs">
              ยังไม่มีข้อมูลชุดทดสอบอ้างอิง
            </td>
          </tr>
        `;
      } else {
        cases.forEach((c, idx) => {
          const tr = document.createElement('tr');
          tr.className = 'hover:bg-slate-50 transition';
          tr.innerHTML = `
            <td class="px-4 py-2.5 font-mono text-slate-400">${idx + 1}</td>
            <td class="px-4 py-2.5 font-semibold text-slate-800">${escapeHtml(c.query)}</td>
            <td class="px-4 py-2.5 text-slate-500">${escapeHtml(c.description || 'Policy')}</td>
            <td class="px-4 py-2.5 font-mono text-blue-600 text-[11px]">${escapeHtml(c.expected_ids.join(', '))}</td>
            <td class="px-4 py-2.5 font-mono text-slate-500 text-[11px]">${escapeHtml(c.retrieved_ids.join(', '))}</td>
            <td class="px-4 py-2.5 text-center font-mono font-bold text-blue-600">${(c.precision * 100).toFixed(1)}%</td>
            <td class="px-4 py-2.5 text-center font-mono font-bold text-emerald-600">${(c.recall * 100).toFixed(1)}%</td>
            <td class="px-4 py-2.5 text-center font-mono font-bold text-indigo-600">${(c.f1 * 100).toFixed(1)}%</td>
          `;
          gtTbody.appendChild(tr);
        });
      }
    }

    if (window.lucide) {
      lucide.createIcons();
    }

  } catch (err) {
    console.error('Metrics loading error:', err);
  }
}

// Utility: Escape HTML
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
