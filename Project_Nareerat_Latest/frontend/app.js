// IBSL Incident & Problem Analysis RAG — Dashboard Frontend Application
// Connects to the IBSL knowledge base: policy data only (Policy + sub-tables, IBSL categories 1.1-1.6)

let currentTab = 'home';
let currentSearchData = null;
let currentEvaluations = {}; // item_id -> 1 (relevant) or 0 (not relevant)
const EXTERNAL_AUTO_LOAD = 3;  // field cards whose AI Agent advice loads automatically; the rest have a button
let externalRequestToken = 0;  // bumped per search so late answers for an old search are dropped

// On DOM Loaded
document.addEventListener('DOMContentLoaded', () => {
  if (window.lucide) {
    lucide.createIcons();
  }
  switchTab('home');
});

// Tab Switching (Home -> Search -> Fields -> Metrics)
function switchTab(tabName) {
  currentTab = tabName;

  const viewHome = document.getElementById('view-home');
  const viewSearch = document.getElementById('view-search');
  const viewMetrics = document.getElementById('view-metrics');
  const viewFields = document.getElementById('view-fields');
  const breadcrumb = document.getElementById('breadcrumb-current');
  const btnBackHome = document.getElementById('btn-back-home');
  const btnEnterSearchTop = document.getElementById('btn-enter-search-top');

  // Hide all views
  if (viewHome) viewHome.classList.add('hidden');
  if (viewSearch) viewSearch.classList.add('hidden');
  if (viewMetrics) viewMetrics.classList.add('hidden');
  if (viewFields) viewFields.classList.add('hidden');

  // Reset navigation icons
  const navItems = ['home', 'search', 'fields', 'metrics'];
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
    if (viewHome) viewHome.classList.remove('hidden');
    if (breadcrumb) breadcrumb.innerText = 'หน้าหลัก';
    if (btnBackHome) btnBackHome.classList.add('hidden');
    if (btnEnterSearchTop) btnEnterSearchTop.classList.remove('hidden');
  } else if (tabName === 'search') {
    if (viewSearch) viewSearch.classList.remove('hidden');
    if (breadcrumb) breadcrumb.innerText = 'ค้นหาคำตอบ';
    if (btnBackHome) btnBackHome.classList.remove('hidden');
    if (btnEnterSearchTop) btnEnterSearchTop.classList.add('hidden');

    if (!currentSearchData) {
      document.getElementById('search-results-area')?.classList.add('hidden');
      document.getElementById('search-empty')?.classList.add('hidden');
      document.getElementById('search-loading')?.classList.add('hidden');
    }

    loadSearchPageMetrics();

    const input = document.getElementById('search-input');
    if (input) setTimeout(() => input.focus(), 100);
  } else if (tabName === 'metrics') {
    if (viewMetrics) viewMetrics.classList.remove('hidden');
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

  document.getElementById('search-results-area')?.classList.add('hidden');
  document.getElementById('search-empty')?.classList.add('hidden');
  document.getElementById('search-loading')?.classList.add('hidden');
  currentSearchData = null;
  currentEvaluations = {};
}

// Execute RAG Search
async function handleSearch() {
  const input = document.getElementById('search-input');
  const query = input ? input.value.trim() : '';

  if (!query) {
    alert('กรุณาระบุชื่อฟิลด์ รหัสคำถาม ข้อความ Error หรือคำถามที่ต้องการค้นหา');
    return;
  }

  const clearBtn = document.getElementById('btn-clear-search');
  if (clearBtn) clearBtn.classList.remove('hidden');

  document.getElementById('search-loading')?.classList.remove('hidden');
  document.getElementById('search-empty')?.classList.add('hidden');
  document.getElementById('search-results-area')?.classList.add('hidden');

  currentEvaluations = {};
  currentSearchData = null;

  try {
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: query })
    });

    document.getElementById('search-loading')?.classList.add('hidden');

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'เกิดข้อผิดพลาดในการค้นหา');
    }

    const data = await res.json();
    currentSearchData = data;

    if (!data.items || data.items.length === 0) {
      document.getElementById('search-empty')?.classList.remove('hidden');
      return;
    }

    renderSearchResults(data);

  } catch (err) {
    document.getElementById('search-loading')?.classList.add('hidden');
    alert(`เกิดข้อผิดพลาด: ${err.message}`);
  }
}

// Render Results: Parts 1, 2, 3, 4
function renderSearchResults(data) {
  const resultsArea = document.getElementById('search-results-area');
  resultsArea?.classList.remove('hidden');

  const items = data.items || [];

  // Count Badge
  const countBadge = document.getElementById('fields-count-badge');
  if (countBadge) countBadge.innerText = `พบ ${items.length} รายการฟิลด์ข้อมูลที่เกี่ยวข้อง`;

  const sectionBadge = document.getElementById('fields-section-badge');
  if (sectionBadge) sectionBadge.innerText = `${items.length} ฟิลด์ข้อมูล`;

  // Human-in-the-Loop note
  const feedbackNote = document.getElementById('feedback-applied-note');
  if (feedbackNote) {
    const fb = data.feedback_applied || {};
    const parts = [];
    if (fb.promoted > 0) parts.push(`จัดไว้อันดับต้น ${fb.promoted} รายการ`);
    if (fb.hidden > 0) parts.push(`ซ่อน ${fb.hidden} รายการที่ถูกประเมินว่าไม่เกี่ยวข้อง`);
    feedbackNote.innerText = parts.length ? `ปรับตามผลประเมินของผู้ใช้: ${parts.join(' • ')}` : '';
    feedbackNote.classList.toggle('hidden', parts.length === 0);
  }

  // Render Retrieved Items List
  const container = document.getElementById('retrieved-fields-list');
  if (container) {
    container.innerHTML = '';
    items.forEach((item, index) => {
      const rankNo = index + 1;
      const itemId = item.item_id;
      const fn = item.field_name || item.name || '';
      const tbl = item.table || '';
      const reqBadge = item.required_label || item.required || '';
      const dtypeLen = item.dtype ? `${item.dtype}${item.length ? ` (${item.length})` : ''}` : '';

      // Match reasons
      const matchReasonsHtml = (item.match_reasons && item.match_reasons.length > 0)
        ? `<div class="flex flex-wrap gap-1.5 mt-2">
            ${item.match_reasons.map(r => `<span class="text-[10px] px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200 font-semibold">${escapeHtml(r)}</span>`).join('')}
           </div>`
        : '';

      // Part 2: IBSL Document Details (Check if this field has IBSL information)
      let ibslContentHtml = '';
      if (item.has_ibsl && item.ibsl_topics && item.ibsl_topics.length > 0) {
        ibslContentHtml = item.ibsl_topics.map(t => {
          const pageRef = t.page_range ? `หน้า ${t.page_range}` : (t.pages ? `หน้า ${t.pages.join(', ')}` : '-');
          const detailBlocks = [];

          if (t.problem && t.problem.trim()) {
            detailBlocks.push(`
              <div class="mb-3">
                <span class="text-[11px] font-bold text-slate-500 uppercase tracking-wider block mb-1">
                  คำอธิบายปัญหา:
                </span>
                <div class="text-xs md:text-sm text-slate-800 leading-relaxed bg-slate-50 p-3 rounded-2xl border border-slate-100 whitespace-pre-line font-sans">
                  ${escapeHtml(t.problem)}
                </div>
              </div>
            `);
          }

          if (t.conditions && t.conditions.trim()) {
            detailBlocks.push(`
              <div class="mb-3">
                <span class="text-[11px] font-bold text-blue-700 uppercase tracking-wider block mb-1">
                  เงื่อนไขหรือ Business Rule:
                </span>
                <div class="text-xs md:text-sm text-slate-800 leading-relaxed bg-blue-50/50 p-3 rounded-2xl border border-blue-100 whitespace-pre-line font-sans">
                  ${escapeHtml(t.conditions)}
                </div>
              </div>
            `);
          }

          if (t.invalid_examples && t.invalid_examples.trim()) {
            detailBlocks.push(`
              <div class="mb-3">
                <span class="text-[11px] font-bold text-rose-700 uppercase tracking-wider block mb-1">
                  ตัวอย่างข้อมูลที่ไม่ถูกต้อง:
                </span>
                <div class="text-xs md:text-sm text-rose-900 leading-relaxed bg-rose-50/70 p-3 rounded-2xl border border-rose-200 whitespace-pre-line font-mono">
                  ${escapeHtml(t.invalid_examples)}
                </div>
              </div>
            `);
          }

          if (t.valid_examples && t.valid_examples.trim()) {
            detailBlocks.push(`
              <div class="mb-3">
                <span class="text-[11px] font-bold text-emerald-700 uppercase tracking-wider block mb-1">
                  ตัวอย่างข้อมูลที่ถูกต้อง:
                </span>
                <div class="text-xs md:text-sm text-emerald-900 leading-relaxed bg-emerald-50/70 p-3 rounded-2xl border border-emerald-200 whitespace-pre-line font-mono">
                  ${escapeHtml(t.valid_examples)}
                </div>
              </div>
            `);
          }

          if (t.guidance && t.guidance.trim()) {
            detailBlocks.push(`
              <div class="mb-3">
                <span class="text-[11px] font-bold text-indigo-700 uppercase tracking-wider block mb-1">
                  แนวทางแก้ไขหรือคำชี้แจงสำหรับการปฏิบัติ:
                </span>
                <div class="text-xs md:text-sm text-indigo-950 leading-relaxed bg-indigo-50/50 p-3 rounded-2xl border border-indigo-100 whitespace-pre-line font-sans">
                  ${escapeHtml(t.guidance)}
                </div>
              </div>
            `);
          }

          if (t.validation_table && t.validation_table.trim()) {
            detailBlocks.push(`
              <div class="mb-3">
                <span class="text-[11px] font-bold text-slate-500 uppercase tracking-wider block mb-1">
                  ตัวอย่างข้อมูลสำหรับการนำส่งและผลการตรวจสอบ:
                </span>
                <div class="text-xs text-slate-700 leading-relaxed bg-slate-50 p-3 rounded-2xl border border-slate-200 whitespace-pre-line font-mono overflow-x-auto">
                  ${escapeHtml(t.validation_table)}
                </div>
              </div>
            `);
          }

          if (t.payload && t.payload.trim()) {
            detailBlocks.push(`
              <div class="mb-3">
                <span class="text-[11px] font-bold text-purple-700 uppercase tracking-wider block mb-1">
                  ตัวอย่าง Payload (Transaction M):
                </span>
                <div class="text-xs text-purple-950 leading-relaxed bg-purple-50/60 p-3 rounded-2xl border border-purple-200 whitespace-pre-line font-mono overflow-x-auto">
                  ${escapeHtml(t.payload)}
                </div>
              </div>
            `);
          }

          return `
            <div class="p-4 rounded-2xl bg-slate-50/80 border border-slate-200/70 mb-3 last:mb-0">
              <div class="flex flex-wrap items-center gap-2 mb-3 pb-2.5 border-b border-slate-200/60">
                <span class="font-mono text-xs font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded border border-blue-200">
                  หัวข้อ ${escapeHtml(t.section_no || '')}
                </span>
                <span class="text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-700">
                  หมวด: ${escapeHtml(t.category || '')} (${escapeHtml(t.category_th || '')})
                </span>
                <span class="text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200 font-mono">
                  ${escapeHtml(t.question_code || '-')}
                </span>
                <span class="text-xs font-bold text-blue-700 bg-blue-100/60 px-2 py-0.5 rounded ml-auto">
                  ${pageRef}
                </span>
              </div>
              <h5 class="font-bold text-slate-900 text-sm mb-3">
                ${escapeHtml(t.topic_title || '')}
              </h5>
              ${detailBlocks.join('')}
              <div class="text-[11px] text-slate-400 mt-2 flex items-center gap-1.5">
                <i data-lucide="book-open" class="w-3.5 h-3.5"></i>
                <span>อ้างอิง: เอกสาร ${escapeHtml(t.doc_name || 'IBSL-PR-INC_001_V1.0_25690813(1).pdf')} ${pageRef}</span>
              </div>
            </div>
          `;
        }).join('');
      } else {
        ibslContentHtml = `
          <div class="p-3.5 rounded-2xl bg-amber-50/40 border border-amber-200/70 text-xs text-amber-900 flex items-start gap-2.5">
            <i data-lucide="info" class="w-4 h-4 text-amber-600 mt-0.5 shrink-0"></i>
            <div>
              <span class="font-bold text-amber-800">สถานะในเอกสาร FAQ:</span>
              <p class="mt-0.5 text-amber-900/90 leading-relaxed font-sans">
                ยังไม่มีกรณีปัญหาหรือแนวทางแก้ไขเพิ่มเติมในเอกสาร FAQ สำหรับฟิลด์นี้ (ให้ปฏิบัติตามมาตรฐานโครงสร้างข้อมูลข้างต้น)
              </p>
            </div>
          </div>
        `;
      }

      const card = document.createElement('div');
      card.className = 'p-6 rounded-3xl bg-white border border-slate-200/80 shadow-[0_4px_20px_rgba(0,0,0,0.03)] hover:border-slate-300 transition';
      card.id = `retrieved-card-${itemId}`;

      card.innerHTML = `
        <!-- PART 1: Top Retrieved Field Header -->
        <div class="flex flex-wrap items-start justify-between gap-3 mb-4 pb-3 border-b border-slate-100">
          <div class="flex items-start gap-3">
            <span class="w-8 h-8 rounded-xl bg-blue-600 text-white font-bold text-xs flex items-center justify-center shadow-sm flex-shrink-0 mt-0.5">
              ${rankNo}
            </span>
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-mono text-sm md:text-base font-bold text-blue-700 bg-blue-50 px-2.5 py-0.5 rounded-lg border border-blue-200">
                  ${escapeHtml(fn)}
                </span>
                <span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-700">
                  ตาราง: ${escapeHtml(tbl)}
                </span>
                ${reqBadge ? `
                <span class="text-[11px] font-semibold px-2.5 py-0.5 rounded-full ${reqBadge.includes('จำเป็น') ? 'bg-rose-50 text-rose-700 border border-rose-200' : 'bg-slate-100 text-slate-600'}">
                  ${escapeHtml(reqBadge)}
                </span>` : ''}
                ${dtypeLen ? `
                <span class="text-[11px] font-mono px-2 py-0.5 rounded-full bg-slate-50 text-slate-600 border border-slate-200">
                  ${escapeHtml(dtypeLen)}
                </span>` : ''}
              </div>
              <h4 class="font-semibold text-slate-800 text-sm md:text-base mt-2">
                ${escapeHtml(item.desc || '')}
              </h4>
              ${matchReasonsHtml}
            </div>
          </div>

          <!-- Similarity Badge -->
          <div class="text-right flex-shrink-0 flex flex-col items-end gap-1.5">
            ${item.user_feedback > 0 ? `
            <div class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200" title="ผู้ใช้ประเมินว่าเกี่ยวข้องกับคำค้นนี้">
              <i data-lucide="user-check" class="w-3.5 h-3.5"></i>
              <span>ผู้ใช้ยืนยันว่าเกี่ยวข้อง</span>
            </div>` : ''}
            <div class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200" title="ค่าความคล้ายคลึง Cosine Similarity จากเวกเตอร์จริง">
              <span>ความคล้ายคลึง: ${item.similarity_score}%</span>
            </div>
            <span class="text-[10px] text-slate-400 font-mono">${escapeHtml(itemId)}</span>
          </div>
        </div>

        <!-- รายละเอียดโครงสร้างฟิลด์ข้อมูล (Field Specifications) -->
        <div class="mb-5 bg-slate-50/70 p-4 rounded-2xl border border-slate-100">
          <div class="flex items-center gap-2 mb-2.5">
            <div class="w-5 h-5 rounded-md bg-blue-100 text-blue-700 flex items-center justify-center">
              <i data-lucide="database" class="w-3 h-3"></i>
            </div>
            <span class="text-xs font-bold text-slate-700 uppercase tracking-wider">รายละเอียดโครงสร้างฟิลด์:</span>
          </div>
          ${renderFieldSpec(item)}
        </div>

        <!-- PART 2: รายละเอียดจากเอกสาร FAQ -->
        <div class="mb-4">
          <div class="flex items-center gap-2 mb-3">
            <div class="w-5 h-5 rounded-md bg-indigo-100 text-indigo-700 flex items-center justify-center">
              <i data-lucide="file-text" class="w-3 h-3"></i>
            </div>
            <span class="text-xs font-bold text-slate-700 uppercase tracking-wider">รายละเอียดและข้อกำหนดจากเอกสาร FAQ:</span>
            ${item.has_ibsl ? `
              <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 ml-auto">
                พบข้อมูลในเอกสาร FAQ (${item.ibsl_topics.length} หัวข้อ)
              </span>
            ` : `
              <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-500 ml-auto">
                ไม่พบกรณีปัญหาเฉพาะในเอกสาร FAQ
              </span>
            `}
          </div>

          ${ibslContentHtml}
        </div>

        <!-- PART 3: คำแนะนำเพิ่มเติมจาก AI Agent (per field, loaded by loadExternalAdvice) -->
        <div class="mb-4 p-4 rounded-2xl bg-amber-50/30 border border-amber-200/70">
          <div class="flex flex-wrap items-center gap-2 mb-2">
            <div class="w-5 h-5 rounded-md bg-amber-500 text-white flex items-center justify-center">
              <i data-lucide="globe" class="w-3 h-3"></i>
            </div>
            <span class="text-xs font-bold text-slate-700 uppercase tracking-wider">คำแนะนำเพิ่มเติมจาก AI Agent:</span>
            <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200 ml-auto">
              แหล่งข้อมูลภายนอก (ไม่ใช่ข้อกำหนดเอกสาร FAQ)
            </span>
          </div>
          <div id="ext-content-${itemId}" class="text-xs md:text-sm text-slate-700 leading-relaxed whitespace-pre-line">
            ${externalButtonHtml(itemId, 'โหลดคำแนะนำจาก AI Agent สำหรับฟิลด์นี้')}
          </div>
        </div>

        <!-- Human-in-the-Loop Relevance Rating for this item -->
        <div class="pt-3 border-t border-slate-100 flex flex-wrap items-center justify-between gap-3">
          <span class="text-xs font-medium text-slate-500">การประเมินความเกี่ยวข้องของรายการนี้ (Human-in-the-Loop):</span>
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

      container.appendChild(card);
    });

    // PART 3: per-field AI Agent advice — the top cards load by themselves, the rest on click (saves web-search quota)
    externalRequestToken += 1;
    items.slice(0, EXTERNAL_AUTO_LOAD).forEach(item => loadExternalAdvice(item.item_id));
  }

  // PART 4: LLM Synthesized Answer
  const answerEl = document.getElementById('synthesized-answer-text');
  if (answerEl) {
    answerEl.innerText = data.answer || 'ไม่สามารถค้นพบข้อมูลที่เกี่ยวข้องจากเอกสาร FAQ';
  }

  const sourceBadge = document.getElementById('answer-source-badge');
  if (sourceBadge) {
    if (data.answer_source === 'llm_gemini') {
      sourceBadge.className = 'px-3 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 shadow-sm';
      sourceBadge.innerText = 'สังเคราะห์โดย Gemini AI (อ้างอิงเอกสาร FAQ)';
    } else if (data.answer_source === 'llm_openai') {
      sourceBadge.className = 'px-3 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 shadow-sm';
      sourceBadge.innerText = 'สังเคราะห์โดย OpenAI (อ้างอิงเอกสาร FAQ)';
    } else {
      sourceBadge.className = 'px-3 py-1 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200 shadow-sm';
      sourceBadge.innerText = 'สกัดจากเอกสาร FAQ โดยตรง';
    }
  }

  // Reset Comment Field
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

  if (evaluatedCount === totalItems && totalItems > 0) {
    hintEl.className = 'text-xs text-emerald-700 bg-emerald-50 px-3 py-1.5 rounded-xl border border-emerald-200 font-semibold flex items-center gap-1';
    hintEl.innerHTML = `<i data-lucide="check" class="w-3.5 h-3.5"></i> ประเมินครบ ${totalItems}/${totalItems} รายการแล้ว พร้อมบันทึก`;
  } else {
    hintEl.className = 'text-xs text-slate-500 bg-white px-3 py-1.5 rounded-xl border border-slate-200';
    hintEl.innerText = `ประเมินแล้ว ${evaluatedCount}/${totalItems} รายการ`;
  }
  if (window.lucide) lucide.createIcons();
}

// Submit Human Evaluation (HITL) for Report Data
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

    alert('บันทึกผลการประเมินของผู้ใช้สำเร็จ! ข้อมูลจะถูกบันทึกเพื่อใช้ในระบบ Human-in-the-Loop');

  } catch (err) {
    submitBtn.disabled = false;
    submitBtn.innerHTML = origHtml;
    alert(`เกิดข้อผิดพลาด: ${err.message}`);
  }
}

// Delete Single Evaluation Record
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

// Clear All User Evaluations
async function confirmClearAllEvaluations() {
  if (!confirm('คุณต้องการล้างประวัติการประเมินของผู้ใช้ทั้งหมดใช่หรือไม่?')) return;

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

// Field Catalog (รายการฟิลด์ข้อมูล)
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

  const rows = fieldsCatalog.fields.filter(f => {
    if (!text) return true;
    const haystack = [f.field_name, f.desc, f.table, ...(f.aliases || [])].join(' ').toLowerCase();
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

  const relatedTopics = (f.related_topics || []).map(t => `
    <li class="flex gap-2">
      <span class="font-mono text-[11px] font-semibold text-indigo-600 shrink-0">หัวข้อ ${escapeHtml(t.section_no)} (หน้า ${escapeHtml(t.page_range)})</span>
      <span class="text-slate-600">${escapeHtml(t.title)}</span>
    </li>`).join('');

  return `
    <tr class="bg-blue-50/40">
      <td colspan="6" class="px-4 md:px-6 py-4">
        ${renderFieldSpec(f)}
        ${(f.aliases || []).length ? `<dl class="mt-3 text-xs">${item('คำค้นที่เกี่ยวข้อง', f.aliases.join(', '))}</dl>` : ''}
        ${relatedTopics ? `
        <div class="mt-4">
          <p class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5">หัวข้อในเอกสาร FAQ ที่เกี่ยวข้อง (${f.related_topics.length})</p>
          <ul class="space-y-1 text-xs">${relatedTopics}</ul>
        </div>` : ''}
      </td>
    </tr>`;
}

function toggleFieldDetail(itemId) {
  fieldsCatalogOpenId = fieldsCatalogOpenId === itemId ? null : itemId;
  renderFieldsCatalog();
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
    // same display as the KPI cards on the evaluation tab
    const naText = '<span class="text-sm font-semibold text-slate-400">ยังไม่มีผลการประเมิน</span>';
    const fmt = v => (gtEv.available && v != null) ? `${(v * 100).toFixed(1)}%` : naText;
    if (els.precision) els.precision.innerHTML = fmt(gtEv.macro_precision_at_3);
    if (els.recall) els.recall.innerHTML = fmt(gtEv.macro_recall_at_3);
    if (els.f1) els.f1.innerHTML = fmt(gtEv.macro_f1_at_3);
  } catch (err) {
    console.error('Search page metrics error:', err);
  }
}

// Load Evaluation Metrics Calculated from Ground Truth & User Evaluation
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

    // The 3 KPI cards come strictly from Ground Truth benchmark
    if (gtEv.available && gtEv.test_count > 0) {
      if (p3El) p3El.innerText = `${(gtEv.macro_precision_at_3 * 100).toFixed(1)}%`;
      if (r3El) r3El.innerText = `${(gtEv.macro_recall_at_3 * 100).toFixed(1)}%`;
      if (f1El) f1El.innerText = `${(gtEv.macro_f1_at_3 * 100).toFixed(1)}%`;
    } else {
      const naText = '<span class="text-sm font-semibold text-slate-400">ยังไม่มีผลการประเมิน</span>';
      if (p3El) p3El.innerHTML = naText;
      if (r3El) r3El.innerHTML = naText;
      if (f1El) f1El.innerHTML = naText;
    }

    const gtCountEl = document.getElementById('gt-case-count');
    if (gtCountEl) {
      gtCountEl.innerText = gtEv.available ? `${gtEv.test_count} ชุดทดสอบมาตรฐาน` : 'ยังไม่มีผลการประเมิน';
    }

    const gtEvalAt = document.getElementById('gt-evaluated-at');
    if (gtEvalAt) {
      gtEvalAt.innerText = gtEv.evaluated_at ? `ประเมินล่าสุด: ${gtEv.evaluated_at}` : '';
    }

    // Populate User Evaluation Table
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
              ยังไม่มีประวัติการประเมินจากผู้ใช้งาน สามารถทดลองค้นหาและประเมินผลในหน้าค้นหาเพื่อเริ่มสะสมข้อมูลสถิติ
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
            <td colspan="9" class="px-4 py-6 text-center text-slate-400 text-xs">
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
            <td class="px-4 py-2.5 font-mono font-semibold text-slate-700 text-[11px]">${escapeHtml(c.test_id || '')}</td>
            <td class="px-4 py-2.5 font-semibold text-slate-800">${escapeHtml(c.query)}</td>
            <td class="px-4 py-2.5 text-slate-500">${escapeHtml(c.category || 'IBSL')}</td>
            <td class="px-4 py-2.5 font-mono text-blue-600 text-[11px]">${escapeHtml((c.expected_ids || []).join(', '))}</td>
            <td class="px-4 py-2.5 font-mono text-slate-500 text-[11px]">${escapeHtml((c.retrieved_ids || []).join(', '))}</td>
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

// Every column of the field's row in Fields_4.0 (same order as the source), shared by the search
// cards and the field catalog. Empty values show "-" so it is clear nothing was left out.
function renderFieldSpec(f) {
  if (f.ibsl_only) {
    return `<p class="text-xs text-amber-800">ฟิลด์นี้ไม่มีในข้อกำหนดโครงสร้างข้อมูล (Fields) พบเฉพาะในเอกสาร FAQ — ดูรายละเอียดจากเอกสาร FAQ ด้านล่าง</p>`;
  }
  const yesNo = v => v === 'Yes' ? 'ใช่ (Yes)' : v === 'No' ? 'ไม่ใช่ (No)' : v;
  const spec = [
    ['ตาราง', f.table],
    ['ลำดับในตาราง', f.seq],
    ['ความต้องการข้อมูล', f.required ? `${f.required}${f.required_label && f.required_label !== f.required ? ` — ${f.required_label}` : ''}` : ''],
    ['ประเภทข้อมูล', f.dtype],
    ['ความยาวข้อมูล', f.length],
    ['อาเรย์', yesNo(f.is_array)],
    ['ข้อจำกัดของ sFTP V.1.0', f.sftp_limit_v1],
    ['ข้อจำกัดของ sFTP V.4.0', f.sftp_limit_v4],
    ['ข้อมูลอ่อนไหว (Sensitive Data)', yesNo(f.sensitive)],
    ['ตัวอย่างข้อมูล', f.example, 'mono'],
    ['Master Data', f.master_data, 'wide'],
    ['คำอธิบายข้อมูล', f.desc, 'wide'],
    ...(f.desc_fix_reason
      ? [['คำอธิบายในต้นฉบับ (ก่อนแก้ไข)', `${f.desc_original}\nเหตุผลที่แก้: ${f.desc_fix_reason}`, 'wide']] : []),
    ['เงื่อนไขการส่งข้อมูล', f.condition, 'wide'],
  ];
  const cells = spec.map(([label, value, style]) => `
    <div class="${style === 'wide' ? 'sm:col-span-2 md:col-span-3' : ''}">
      <dt class="text-[11px] font-semibold text-slate-400 tracking-wide">${label}</dt>
      <dd class="mt-0.5 text-slate-800 whitespace-pre-line break-words ${style === 'mono' ? 'font-mono' : ''}">${value ? escapeHtml(value) : '<span class="text-slate-300">-</span>'}</dd>
    </div>`).join('');
  return `<dl class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-x-5 gap-y-3 text-xs">${cells}</dl>`;
}

// AI Agent advice for one field card
function externalButtonHtml(itemId, label) {
  return `
    <button type="button" onclick="loadExternalAdvice('${itemId}')"
      class="px-3 py-1.5 rounded-xl text-xs font-medium border border-amber-300 text-amber-800 bg-white hover:bg-amber-50 transition inline-flex items-center gap-1.5">
      <i data-lucide="search" class="w-3.5 h-3.5"></i>
      <span>${escapeHtml(label)}</span>
    </button>`;
}

async function loadExternalAdvice(itemId) {
  const box = document.getElementById(`ext-content-${itemId}`);
  if (!box || !currentSearchData) return;
  const token = externalRequestToken;
  box.innerHTML = `
    <span class="inline-flex items-center gap-2 text-slate-500">
      <i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>
      กำลังค้นหาคำแนะนำจากแหล่งข้อมูลภายนอก...
    </span>`;
  if (window.lucide) lucide.createIcons();

  try {
    const res = await fetch('/api/external', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: currentSearchData.query, item_id: itemId })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (token !== externalRequestToken) return; // a newer search replaced these cards

    const sources = (data.sources || []).map(s => `
      <li>
        <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer" class="hover:underline inline-flex items-center gap-1 text-blue-600">
          <i data-lucide="external-link" class="w-3 h-3"></i>
          <span>${escapeHtml(s.title || s.url)}</span>
        </a>
      </li>`).join('');
    const retry = ['error', 'quota_exceeded'].includes(data.status)
      ? `<div class="mt-2">${externalButtonHtml(itemId, 'ลองอีกครั้ง')}</div>` : '';
    box.innerHTML = `
      <div class="${data.status === 'success' ? '' : 'text-slate-500'}">${escapeHtml(data.message || 'ไม่มีคำแนะนำเพิ่มเติมจากภายนอก')}</div>
      ${sources ? `
      <div class="mt-3 pt-2 border-t border-amber-100 whitespace-normal">
        <p class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">แหล่งอ้างอิงภายนอกที่สืบค้นได้:</p>
        <ul class="space-y-1 text-xs">${sources}</ul>
      </div>` : ''}
      ${retry}`;
  } catch (err) {
    if (token !== externalRequestToken) return;
    box.innerHTML = `
      <div class="text-rose-700">ดึงคำแนะนำจากภายนอกไม่สำเร็จ (${escapeHtml(err.message)}) คำตอบจากเอกสาร FAQ ยังใช้งานได้ตามปกติ</div>
      <div class="mt-2">${externalButtonHtml(itemId, 'ลองอีกครั้ง')}</div>`;
  }
  if (window.lucide) lucide.createIcons();
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
