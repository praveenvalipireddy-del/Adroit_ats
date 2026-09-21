/**
 * ADROIT ATS - Multi-Consultant US IT Staffing & 1-Click Outreach Platform
 * Complete Dashboard Controller & Interactive Engine
 */

let state = {
    consultants: [],
    activeConsultantId: 1,
    jobs: [],
    students: [],
    studentsLoaded: false,
    pipeline: {},
    activeTab: 'jobs',
    lastOptimizedResumeText: '',
    lastOptimizedCandidateName: 'Consultant'
};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initConsultants();
    initJobsTable();
    initModals();
    initResumeBot();
    initStudentsTab();
    checkUrlAuthParams();
});

function checkUrlAuthParams() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('auth_success')) {
        showToast('Gmail successfully connected for consultant!', 'success');
        window.history.replaceState({}, document.title, window.location.pathname);
    }
}

// =========================================================================
// 1. Navigation & Tab Switching
// =========================================================================
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const tabId = item.getAttribute('data-tab');
            switchTab(tabId);
        });
    });

    // Hash support
    if (window.location.hash) {
        const hashTab = window.location.hash.replace('#', '');
        switchTab(hashTab);
    } else {
        switchTab('dashboard');
    }
}

const tabAliasMap = {
    'dashboard': 'dashboard',
    'candidates': 'consultants',
    'consultants': 'consultants',
    'jobs': 'jobs',
    'sourcing': 'students',
    'students': 'students',
    'reporting': 'drafts',
    'drafts': 'drafts',
    'settings': 'team',
    'team': 'team',
    'resumebot': 'resumebot'
};

function switchTab(rawTabId) {
    const paneKey = tabAliasMap[rawTabId] || rawTabId;
    state.activeTab = rawTabId;

    document.querySelectorAll('.nav-item').forEach(el => {
        const dt = el.getAttribute('data-tab');
        if (dt === rawTabId || tabAliasMap[dt] === paneKey) {
            el.classList.add('active');
        } else {
            el.classList.remove('active');
        }
    });

    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    const activePane = document.getElementById(`tab-${paneKey}`) || document.getElementById(`tab-${rawTabId}`);
    if (activePane) activePane.classList.add('active');

    if (paneKey === 'dashboard') {
        if (typeof renderDashboardPipeline === 'function') renderDashboardPipeline();
    } else if (paneKey === 'consultants') {
        if (typeof renderConsultantsTable === 'function') renderConsultantsTable();
    } else if (paneKey === 'drafts') {
        loadPipeline();
    } else if (paneKey === 'team') {
        loadRecruiters();
    } else if (paneKey === 'students') {
        if (!state.students || state.students.length === 0) {
            loadStudentBench();
        }
    } else if (paneKey === 'jobs') {
        if (!state.jobs || state.jobs.length === 0) {
            searchJobs(false);
        }
    }
}

// =========================================================================
// 2. US Bench Consultants Hub
// =========================================================================
async function initConsultants() {
    await fetchConsultants();

    const globalSelect = document.getElementById('global-active-consultant');
    if (globalSelect) {
        globalSelect.addEventListener('change', (e) => {
            state.activeConsultantId = parseInt(e.target.value);
            updateActiveConsultantUI();
            updateTableConsultantSelects();
        });
    }

    const btnAddTab = document.getElementById('btn-add-consultant-tab');
    if (btnAddTab) {
        btnAddTab.addEventListener('click', () => openConsultantModal());
    }

    const btnHeaderAdd = document.getElementById('btn-open-add-consultant');
    if (btnHeaderAdd) {
        btnHeaderAdd.addEventListener('click', () => openConsultantModal());
    }

    const btnHeaderScrape = document.getElementById('btn-header-scrape-us');
    if (btnHeaderScrape) {
        btnHeaderScrape.addEventListener('click', () => triggerUsScrape());
    }

    const btnHeaderPasteDraft = document.getElementById('btn-open-paste-draft-modal');
    if (btnHeaderPasteDraft) {
        btnHeaderPasteDraft.addEventListener('click', () => openPasteDraftModal(state.activeConsultantId));
    }
}

async function fetchConsultants(recruiterId = null) {
    try {
        const filterEl = document.getElementById('select-consultant-recruiter-filter');
        const rId = recruiterId !== null ? recruiterId : (filterEl ? filterEl.value : '');
        const url = rId ? `/api/consultants?recruiter_id=${encodeURIComponent(rId)}` : '/api/consultants';
        const res = await fetch(url);
        const data = await res.json();
        state.consultants = Array.isArray(data) ? data : [];
        populateConsultantDropdowns();
        updateActiveConsultantUI();
        if (typeof renderConsultantsTable === 'function') renderConsultantsTable();
        renderConsultantsGrid();
        if (typeof renderDashboardPipeline === 'function') renderDashboardPipeline();
    } catch (err) {
        console.error('Error fetching consultants:', err);
    }
}

function populateConsultantDropdowns() {
    const globalSelect = document.getElementById('global-active-consultant');
    const resumeSelect = document.getElementById('resumebot-consultant-select');
    const pdSelect = document.getElementById('pd-consultant-select');

    if (globalSelect) {
        globalSelect.innerHTML = state.consultants.map(c => 
            `<option value="${c.id}" ${c.id === state.activeConsultantId ? 'selected' : ''}>
                ${escapeHtml(c.name)} (${escapeHtml(c.title || 'Consultant')})
            </option>`
        ).join('');
    }

    if (pdSelect) {
        pdSelect.innerHTML = state.consultants.map(c => 
            `<option value="${c.id}" ${c.id === state.activeConsultantId ? 'selected' : ''}>
                ${escapeHtml(c.name)} (${escapeHtml(c.title || 'Consultant')}) - ${escapeHtml(c.target_rate || '$90/hr')}
            </option>`
        ).join('');
    }

    if (resumeSelect) {
        resumeSelect.innerHTML = state.consultants.map(c => 
            `<option value="${c.id}" ${c.id === state.activeConsultantId ? 'selected' : ''}>
                ${escapeHtml(c.name)} - ${escapeHtml(c.title || 'Consultant')}
            </option>`
        ).join('');
    }
}

function updateActiveConsultantUI() {
    const active = state.consultants.find(c => c.id === state.activeConsultantId) || state.consultants[0];
    if (!active) return;

    state.activeConsultantId = active.id;

    const gmailBadge = document.getElementById('active-gmail-badge');
    if (gmailBadge) {
        if (active.gmail_connected) {
            gmailBadge.innerHTML = `🟢 ${escapeHtml(active.gmail_account || 'Gmail Connected')}`;
            gmailBadge.style.color = '#34d399';
            gmailBadge.style.background = 'rgba(52, 211, 153, 0.15)';
            gmailBadge.style.border = '1px solid rgba(52, 211, 153, 0.35)';
        } else {
            gmailBadge.innerHTML = `⚠️ Connect Gmail`;
            gmailBadge.style.color = '#fbbf24';
            gmailBadge.style.background = 'rgba(251, 191, 36, 0.15)';
            gmailBadge.style.border = '1px solid rgba(251, 191, 36, 0.35)';
        }
    }

    const rateBadge = document.getElementById('active-rate-badge');
    if (rateBadge) {
        rateBadge.innerText = active.target_rate || '$90/hr C2C';
    }
}

function updateTableConsultantSelects() {
    document.querySelectorAll('.job-consultant-select').forEach(sel => {
        sel.value = state.activeConsultantId;
    });
}

function renderConsultantsGrid() {
    const container = document.getElementById('consultants-cards-container');
    if (!container) return;

    if (!state.consultants || state.consultants.length === 0) {
        container.innerHTML = `
            <div style="grid-column: 1 / -1; text-align:center; padding: 40px; color: var(--text-muted); background: var(--card-bg); border-radius: 12px;">
                <p>No consultants found. Click <strong>"Add New Consultant Profile"</strong> to add your first bench candidate.</p>
            </div>`;
        return;
    }

    container.innerHTML = state.consultants.map(c => {
        const initials = c.name.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
        const hasResume = Boolean(c.resume_filename || c.resume_path);
        const gmailConnected = Boolean(c.gmail_connected);
        const cleanName = (c.name || "Consultant").replace(/[,\/]/g, '').trim();
        const liUrl = (c.linkedin_url && c.linkedin_url.startsWith('http') && !c.linkedin_url.endsWith('-devops/') && !c.linkedin_url.endsWith('-data-analyst/'))
            ? c.linkedin_url
            : `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(cleanName)}&origin=GLOBAL_SEARCH_HEADER`;

        return `
        <div class="consultant-card" data-id="${c.id}">
            <div class="cand-header">
                <a href="${liUrl}" target="_blank" rel="noopener noreferrer" style="text-decoration:none;" onclick="event.stopPropagation(); window.open('${liUrl}', '_blank'); return true;">
                    <div class="cand-avatar" style="cursor:pointer;" title="Click to view LinkedIn">${initials}</div>
                </a>
                <div class="cand-details">
                    <h3>
                        <a href="${liUrl}" target="_blank" rel="noopener noreferrer" style="color:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:6px;" onmouseover="this.style.color='#38bdf8'" onmouseout="this.style.color='inherit'" onclick="event.stopPropagation(); window.open('${liUrl}', '_blank'); return true;">
                            ${escapeHtml(c.name)}
                            <span title="View LinkedIn Profile" style="color:#38bdf8; font-size:12px;">↗</span>
                        </a>
                    </h3>
                    <div class="cand-title">${escapeHtml(c.title || 'Technical Consultant')}</div>
                </div>
            </div>

            <div class="cand-meta-row">
                <span class="meta-chip green">${escapeHtml(c.target_rate || '$90/hr C2C')}</span>
                <span class="meta-chip">${escapeHtml(c.visa_status || 'C2C Eligible')}</span>
                <span class="meta-chip">${c.experience_years || 5}+ Yrs Exp</span>
                <span class="meta-chip">${escapeHtml(c.location || 'United States')}</span>
            </div>

            ${c.recruiter_name ? `
            <div style="font-size: 0.75rem; color: #94a3b8; margin: 6px 0 10px; display: flex; align-items: center; justify-content: space-between; background: rgba(30, 41, 59, 0.6); padding: 4px 10px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.06);">
                <span>👤 Recruiter: <strong style="color: #38bdf8;">${escapeHtml(c.recruiter_name)}</strong></span>
                <button class="btn-trigger-reassign" data-id="${c.id}" data-name="${escapeHtml(c.name)}" style="background: rgba(168, 85, 247, 0.15); border: 1px solid rgba(168, 85, 247, 0.3); color: #c084fc; border-radius: 4px; padding: 2px 7px; cursor: pointer; font-size: 0.72rem; font-weight: 600;">Transfer</button>
            </div>` : ''}

            <div class="cand-skills-box">
                <strong>Primary Skills:</strong> ${escapeHtml(c.primary_skills || 'Full Stack Engineering, Cloud Services')}
            </div>

            <div class="cand-integrations">
                <div class="integration-item">
                    <span><strong>Gmail Mailbox:</strong></span>
                    ${gmailConnected ? 
                        `<span style="color: #34d399; font-weight: 600;">✓ Connected (${escapeHtml(c.gmail_account || 'Active')})</span>` : 
                        `<div>
                            <button class="btn btn-success btn-xs btn-open-app-pass" data-id="${c.id}" data-name="${escapeHtml(c.name)}" data-email="${escapeHtml(c.email || '')}">🔑 App Password</button>
                            <a href="/api/consultants/${c.id}/connect-gmail" class="btn btn-outline-primary btn-xs" style="margin-left:4px;">OAuth</a>
                         </div>`
                    }
                </div>
                <div class="integration-item">
                    <span><strong>Master Resume:</strong></span>
                    ${hasResume ? 
                        `<span style="color: #a5b4fc;">📄 ${escapeHtml(c.resume_filename || 'Resume Attached')}</span>` : 
                        `<button class="btn btn-secondary btn-xs btn-upload-cand-resume" data-id="${c.id}" data-name="${escapeHtml(c.name)}">Upload .docx</button>`
                    }
                </div>
            </div>

            <div class="cand-card-actions">
                <a href="${liUrl}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm" style="display:inline-flex; align-items:center; gap:4px; text-decoration:none; background:rgba(14, 118, 168, 0.2); color:#38bdf8; border:1px solid rgba(56, 189, 248, 0.35);" onclick="event.stopPropagation(); window.open('${liUrl}', '_blank'); return true;">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M19 3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14m-.5 15.5v-5.3a3.26 3.26 0 0 0-3.26-3.26c-.85 0-1.84.52-2.28 1.3v-1.11h-2.79v8.37h2.79v-4.93c0-.77.62-1.4 1.39-1.4a1.4 1.4 0 0 1 1.4 1.4v4.93h2.75M6.46 10.9v8.37H9.2V10.9H6.46M7.83 6.27a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2Z"/></svg>
                    LinkedIn ↗
                </a>
                <button class="btn btn-success btn-sm btn-paste-draft-cand" data-id="${c.id}" data-name="${escapeHtml(c.name)}">
                    ✉️ Paste JD & Draft
                </button>
                <button class="btn btn-primary btn-sm btn-find-jobs-cand" data-id="${c.id}" data-skills="${escapeHtml(c.primary_skills || '')}">
                    🎯 Find 24h Jobs
                </button>
                <button class="btn btn-secondary btn-sm btn-edit-cand" data-id="${c.id}">
                    Edit Profile
                </button>
            </div>
        </div>
        `;
    }).join('');

    // Attach card event listeners
    container.querySelectorAll('.btn-paste-draft-cand').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            openPasteDraftModal(candId);
        });
    });

    container.querySelectorAll('.btn-find-jobs-cand').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const skills = btn.getAttribute('data-skills') || '';
            state.activeConsultantId = candId;
            updateActiveConsultantUI();
            
            const firstSkill = skills.split(',')[0].trim() || 'Software Engineer';
            const searchInput = document.getElementById('filter-query');
            if (searchInput) searchInput.value = firstSkill;
            
            // Switch to jobs tab and trigger search
            switchTab('jobs');
            setTimeout(() => {
                searchJobs(false);
            }, 100);
        });
    });

    container.querySelectorAll('.btn-edit-cand').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const c = state.consultants.find(cand => cand.id === candId);
            if (c) openConsultantModal(c);
        });
    });

    container.querySelectorAll('.btn-upload-cand-resume').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const name = btn.getAttribute('data-name');
            openUploadResumeModal(candId, name);
        });
    });

    container.querySelectorAll('.btn-open-app-pass').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const name = btn.getAttribute('data-name');
            const email = btn.getAttribute('data-email');
            openAppPasswordModal(candId, name, email);
        });
    });
}

function openConsultantModal(cand = null) {
    const modal = document.getElementById('modal-consultant');
    const form = document.getElementById('form-consultant');
    const title = document.getElementById('modal-consultant-title');
    const editIdInput = document.getElementById('consultant-edit-id');

    if (form) form.reset();

    if (cand) {
        if (title) title.innerText = `Edit Profile: ${cand.name}`;
        if (editIdInput) editIdInput.value = cand.id;
        document.getElementById('c-name').value = cand.name || '';
        document.getElementById('c-email').value = cand.email || '';
        document.getElementById('c-phone').value = cand.phone || '';
        document.getElementById('c-title').value = cand.title || '';
        document.getElementById('c-skills').value = cand.primary_skills || '';
        document.getElementById('c-exp').value = cand.experience_years || 5;
        document.getElementById('c-rate').value = cand.target_rate || '$90/hr (C2C)';
        document.getElementById('c-visa').value = cand.visa_status || 'C2C Eligible';
        document.getElementById('c-location').value = cand.location || 'United States';
        document.getElementById('c-summary').value = cand.summary || '';
    } else {
        if (title) title.innerText = 'Add New US Bench Consultant';
        if (editIdInput) editIdInput.value = '';
    }

    if (modal) modal.style.display = 'flex';
}

function closeConsultantModal() {
    const modal = document.getElementById('modal-consultant');
    if (modal) modal.style.display = 'none';
}

function openUploadResumeModal(candId, candName) {
    document.getElementById('upload-candidate-id').value = candId;
    document.getElementById('upload-candidate-name-label').innerText = `Uploading resume for ${candName}`;
    const modal = document.getElementById('modal-upload-resume');
    if (modal) modal.style.display = 'flex';
}

function closeUploadResumeModal() {
    const modal = document.getElementById('modal-upload-resume');
    if (modal) modal.style.display = 'none';
}

function openAppPasswordModal(candId, candName, candEmail) {
    document.getElementById('app-pass-cand-id').value = candId;
    document.getElementById('app-pass-cand-name').innerText = candName;
    document.getElementById('app-pass-email').value = candEmail || '';
    document.getElementById('app-pass-key').value = '';
    const modal = document.getElementById('modal-app-password');
    if (modal) modal.style.display = 'flex';
}

function closeAppPasswordModal() {
    const modal = document.getElementById('modal-app-password');
    if (modal) modal.style.display = 'none';
}

function openPasteDraftModal(candId = null) {
    const modal = document.getElementById('modal-paste-draft');
    const form = document.getElementById('form-paste-draft');
    if (form) form.reset();

    const select = document.getElementById('pd-consultant-select');
    if (select && candId) {
        select.value = candId;
    } else if (select && state.activeConsultantId) {
        select.value = state.activeConsultantId;
    }

    if (modal) modal.style.display = 'flex';
}

function closePasteDraftModal() {
    const modal = document.getElementById('modal-paste-draft');
    if (modal) modal.style.display = 'none';
}

// =========================================================================
// 3. Live 24-Hour USA IT Jobs & 1-Click Outreach Table
// =========================================================================
function initJobsTable() {
    const btnSearch = document.getElementById('btn-search-jobs');
    const btnLiveScrape = document.getElementById('btn-live-scrape-trigger');
    const queryInput = document.getElementById('filter-query');
    const locInput = document.getElementById('filter-location');
    const sourceSelect = document.getElementById('filter-source');

    if (btnSearch) {
        btnSearch.addEventListener('click', () => searchJobs(false));
    }

    if (btnLiveScrape) {
        btnLiveScrape.addEventListener('click', () => triggerUsScrape());
    }

    if (queryInput) {
        queryInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                searchJobs(false);
            }
        });
    }

    if (sourceSelect) {
        sourceSelect.addEventListener('change', () => searchJobs(false));
    }

    // Quick filter inside table if present
    const quickFilterInput = document.getElementById('quick-job-filter-input');
    if (quickFilterInput) {
        quickFilterInput.addEventListener('input', (e) => {
            const term = e.target.value.toLowerCase();
            document.querySelectorAll('#jobs-table-body tr.job-row').forEach(row => {
                const text = row.innerText.toLowerCase();
                row.style.display = text.includes(term) ? '' : 'none';
            });
        });
    }

    // Initial load of jobs
    searchJobs(false);
}

async function searchJobs(liveScrape = false) {
    const tbody = document.getElementById('jobs-table-body');
    const countLabel = document.getElementById('jobs-table-count');
    const query = document.getElementById('filter-query')?.value?.trim() || '';
    const location = document.getElementById('filter-location')?.value?.trim() || 'United States';
    const source = document.getElementById('filter-source')?.value || 'All';

    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="loading-cell" style="text-align:center; padding: 30px; color: var(--text-muted);">
                    <div class="spinner" style="display:inline-block; margin-right:8px;"></div>
                    ${liveScrape ? 'Scraping fresh 24h US contract jobs across portals...' : 'Searching US job requisitions...'}
                </td>
            </tr>`;
    }

    try {
        const res = await fetch('/api/jobs/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query: query,
                location: location,
                source: source,
                contract_only: true,
                is_24h_only: true,
                live_scrape: liveScrape
            })
        });

        const data = await res.json();
        state.jobs = data.jobs || data.results || [];

        if (countLabel) {
            countLabel.innerText = `Showing ${state.jobs.length} Fresh US Requisitions`;
        }

        renderJobsTable(state.jobs);
    } catch (err) {
        console.error('Error fetching jobs:', err);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 24px; color: #ef4444;">Failed to load jobs. Please try searching again.</td></tr>`;
        }
    }
}

async function triggerUsScrape() {
    showToast('🚀 Running Live 24h US Requisition Scraper (LinkedIn, Dice, Indeed, ZipRecruiter)...', 'info', 6000);
    const query = document.getElementById('filter-query')?.value?.trim() || 'Software Engineer';
    const location = document.getElementById('filter-location')?.value?.trim() || 'United States';

    try {
        const res = await fetch('/api/jobs/scrape-us', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                keywords: [query],
                location: location,
                contract_only: true
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`✅ Scraped & saved ${data.count || 0} fresh US requisitions!`, 'success');
            searchJobs(false);
        } else {
            showToast(`Scrape completed with notice: ${data.message || 'Ready'}`, 'info');
            searchJobs(false);
        }
    } catch (err) {
        showToast('Error during live scrape: ' + err.message, 'error');
        searchJobs(false);
    }
}

function renderJobsTable(jobs) {
    const tbody = document.getElementById('jobs-table-body');
    if (!tbody) return;

    if (!jobs || jobs.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" style="text-align:center; padding: 40px; color: var(--text-muted);">
                    No jobs found matching your search. Try adjusting keywords or click <strong>"Live 24h Scrape"</strong> to fetch fresh postings.
                </td>
            </tr>`;
        return;
    }

    const consultantOptions = state.consultants.map(c => 
        `<option value="${c.id}" ${c.id === state.activeConsultantId ? 'selected' : ''}>
            ${escapeHtml(c.name)} (${escapeHtml(c.title || 'Consultant')})
        </option>`
    ).join('');

    tbody.innerHTML = jobs.map(j => {
        const portalClass = (j.source || '').toLowerCase().replace(/[^a-z0-9]/g, '');
        const hasEmail = Boolean(j.recruiter_email);
        const reqUrl = j.url || '#';

        return `
        <tr class="job-row" data-job-id="${j.id}">
            <td>
                <div style="font-weight: 600; color: #fff; margin-bottom: 2px;">
                    ${escapeHtml(j.title || 'Software Engineer')}
                </div>
                <div style="font-size: 0.85rem; color: var(--text-muted); display:flex; align-items:center; gap:8px;">
                    <span>🏢 ${escapeHtml(j.company || 'Direct Client / Prime Vendor')}</span>
                    <span>📍 ${escapeHtml(j.location || 'United States')}</span>
                    ${reqUrl && reqUrl !== '#' ? `<a href="${reqUrl}" target="_blank" rel="noopener noreferrer" style="color:#38bdf8; text-decoration:none;" title="Open original job posting">View Req ↗</a>` : ''}
                </div>
            </td>
            <td>
                <span class="portal-badge badge-${portalClass}">${escapeHtml(j.source || 'Portal')}</span>
            </td>
            <td>
                <span style="color: #34d399; font-weight: 600;">${escapeHtml(j.salary || j.job_type || 'C2C / Contract')}</span>
            </td>
            <td>
                <div style="display:flex; align-items:center; gap:6px;">
                    <input type="email" class="form-control form-control-sm email-inline-input" data-job-id="${j.id}" value="${escapeHtml(j.recruiter_email || '')}" placeholder="Paste recruiter email..." style="min-width: 180px; font-size: 0.85rem;">
                    <button class="btn btn-secondary btn-xs btn-save-email" data-job-id="${j.id}" title="Save Recruiter Email">💾</button>
                </div>
            </td>
            <td>
                <select class="form-control form-control-sm job-consultant-select" data-job-id="${j.id}" style="font-size: 0.85rem;">
                    ${consultantOptions}
                </select>
            </td>
            <td style="text-align: right; white-space: nowrap;">
                <button class="btn btn-primary btn-sm btn-draft-job" data-job-id="${j.id}" style="display:inline-flex; align-items:center; gap:4px;">
                    ✉️ 1-Click Draft
                </button>
                <button class="btn btn-sm btn-copilot-job" data-job-id="${j.id}" style="background: linear-gradient(135deg, #2563eb, #38bdf8); color: #ffffff; border: none; font-weight: 600; font-size: 0.75rem; padding: 6px 10px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px; margin-left: 6px;" title="Personalize email with AI Outreach Copilot">
                    🤖 AI
                </button>
            </td>
        </tr>
        `;
    }).join('');

    // Attach inline email save listeners
    tbody.querySelectorAll('.email-inline-input').forEach(input => {
        input.addEventListener('change', (e) => {
            const jobId = e.target.getAttribute('data-job-id');
            saveRecruiterEmail(jobId, e.target.value);
        });
    });

    tbody.querySelectorAll('.btn-save-email').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const jobId = btn.getAttribute('data-job-id');
            const row = btn.closest('tr');
            const input = row.querySelector('.email-inline-input');
            if (input) {
                saveRecruiterEmail(jobId, input.value);
            }
        });
    });

    // Attach 1-Click Draft listeners
    tbody.querySelectorAll('.btn-draft-job').forEach(btn => {
        btn.addEventListener('click', () => {
            const jobId = btn.getAttribute('data-job-id');
            const row = btn.closest('tr');
            const candSelect = row.querySelector('.job-consultant-select');
            const emailInput = row.querySelector('.email-inline-input');
            const candId = candSelect ? parseInt(candSelect.value) : state.activeConsultantId;
            const recruiterEmail = emailInput ? emailInput.value.trim() : '';

            createJobDraft(jobId, candId, recruiterEmail, btn);
        });
    });
}

async function saveRecruiterEmail(jobId, email) {
    if (!email) return;
    try {
        const res = await fetch('/api/jobs/update-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                job_id: jobId,
                recruiter_email: email.trim()
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast('Recruiter email saved!', 'success', 2000);
        }
    } catch (err) {
        console.error('Error updating email:', err);
    }
}

async function createJobDraft(jobId, candId, customToEmail = '', btnElement = null) {
    if (!candId) {
        showToast('Please select a consultant first', 'warning');
        return;
    }

    if (btnElement) {
        btnElement.disabled = true;
        btnElement.innerText = 'Creating Draft...';
    }

    try {
        const res = await fetch('/api/outreach/create-draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                job_id: jobId,
                candidate_id: candId,
                custom_to_email: customToEmail
            })
        });

        const data = await res.json();
        if (data.success) {
            showToast(`✉️ Gmail Draft Created for ${data.consultant_name}! Sent to: ${data.recruiter_email || 'Recruiter'}`, 'success', 6000);
            
            // Update stats
            const statDrafts = document.getElementById('stat-drafted-count');
            if (statDrafts) {
                const cur = parseInt(statDrafts.innerText) || 0;
                statDrafts.innerText = cur + 1;
            }
        } else {
            showToast(data.error || 'Failed to create Gmail draft. Please ensure Gmail is connected.', 'error', 6000);
        }
    } catch (err) {
        showToast('Draft error: ' + err.message, 'error');
    } finally {
        if (btnElement) {
            btnElement.disabled = false;
            btnElement.innerHTML = '✉️ 1-Click Draft';
        }
    }
}

// =========================================================================
// 4. Outbound Pipeline / Drafts Kanban
// =========================================================================
async function loadPipeline() {
    const container = document.getElementById('pipeline-container');
    if (!container) return;

    try {
        const res = await fetch('/api/pipeline');
        const data = await res.json();
        state.pipeline = data;
        renderPipeline(data);
    } catch (err) {
        console.error('Error loading pipeline:', err);
    }
}

function renderPipeline(pipeline) {
    const stages = ['Drafted', 'Applied', 'Interviewing', 'Offer'];
    
    stages.forEach(stage => {
        const colContainer = document.getElementById(`col-${stage}`);
        const countSpan = document.getElementById(`count-${stage}`);
        const items = pipeline[stage] || [];

        if (countSpan) countSpan.innerText = items.length;

        if (colContainer) {
            if (items.length === 0) {
                colContainer.innerHTML = `<div style="text-align:center; padding: 24px; color: var(--text-muted); font-size: 0.85rem;">No applications in ${stage}</div>`;
                return;
            }

            colContainer.innerHTML = items.map(app => {
                const dateStr = app.created_at ? new Date(app.created_at).toLocaleDateString() : 'Recent';
                return `
                <div class="pipeline-card" data-app-id="${app.id}">
                    <div style="font-weight: 600; color: #fff; margin-bottom: 4px;">${escapeHtml(app.job_title || 'Software Engineering Role')}</div>
                    <div style="font-size: 0.8rem; color: #38bdf8; margin-bottom: 2px;">👤 ${escapeHtml(app.candidate_name || 'Consultant')}</div>
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 6px;">🏢 ${escapeHtml(app.company || 'Client')} (${escapeHtml(app.recruiter_email || 'No email')})</div>
                    <div style="display:flex; justify-content:space-between; align-items:center; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 6px; margin-top: 6px;">
                        <span style="font-size: 0.75rem; color: var(--text-muted);">${dateStr}</span>
                        <div class="stage-actions">
                            ${stage === 'Drafted' ? `<button class="btn btn-xs btn-outline-primary" onclick="updatePipelineStage(${app.id}, 'Applied')">➔ Applied</button>` : ''}
                            ${stage === 'Applied' ? `<button class="btn btn-xs btn-outline-primary" onclick="updatePipelineStage(${app.id}, 'Interviewing')">➔ Interview</button>` : ''}
                            ${stage === 'Interviewing' ? `<button class="btn btn-xs btn-success" onclick="updatePipelineStage(${app.id}, 'Offer')">🎉 Offer</button>` : ''}
                        </div>
                    </div>
                </div>
                `;
            }).join('');
        }
    });
}

async function updatePipelineStage(appId, newStage) {
    try {
        const res = await fetch('/api/pipeline/update-stage', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                application_id: appId,
                stage: newStage
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Application moved to ${newStage}!`, 'success');
            loadPipeline();
        }
    } catch (err) {
        showToast('Error updating stage: ' + err.message, 'error');
    }
}
window.updatePipelineStage = updatePipelineStage;

// =========================================================================
// 5. AI Master Resume Optimization Bot
// =========================================================================
function initResumeBot() {
    const btnOptimize = document.getElementById('btn-run-resume-optimization');
    const btnDownload = document.getElementById('btn-download-optimized-docx');
    const candSelect = document.getElementById('resumebot-consultant-select');
    const fileInput = document.getElementById('resumebot-file-input');
    const jdTextarea = document.getElementById('resumebot-jd-text');
    const resumeTextarea = document.getElementById('resumebot-resume-text');
    const notesInput = document.getElementById('resumebot-custom-notes');

    if (btnOptimize) {
        btnOptimize.addEventListener('click', async () => {
            const jd = jdTextarea ? jdTextarea.value.trim() : '';
            const resume = resumeTextarea ? resumeTextarea.value.trim() : '';
            const candId = candSelect ? parseInt(candSelect.value) : state.activeConsultantId;
            const notes = notesInput ? notesInput.value.trim() : '';

            if (!jd) {
                showToast('Please paste the client Job Description (JD) to optimize against.', 'warning');
                if (jdTextarea) jdTextarea.focus();
                return;
            }

            btnOptimize.disabled = true;
            btnOptimize.innerHTML = '⚡ Optimizing Resume (ATS Keyword Engine)...';

            try {
                const res = await fetch('/api/resume-bot/optimize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        candidate_id: candId,
                        jd_text: jd,
                        resume_text: resume,
                        custom_instructions: notes
                    })
                });

                const data = await res.json();
                if (data.error) {
                    showToast(data.error, 'error');
                    return;
                }

                // Populate results
                state.lastOptimizedResumeText = data.optimized_resume_text || data.optimized_summary || '';
                const selectedCand = state.consultants.find(c => c.id === candId);
                state.lastOptimizedCandidateName = selectedCand ? selectedCand.name : 'Consultant';

                const resultsCard = document.getElementById('resumebot-results-card');
                if (resultsCard) resultsCard.style.display = 'block';

                const scoreInitial = document.getElementById('score-initial');
                const scoreTarget = document.getElementById('score-target');
                const scoreBar = document.getElementById('score-progress-bar');
                const matchedSkills = document.getElementById('result-matched-skills');
                const addedSkills = document.getElementById('result-added-skills');
                const domainBadge = document.getElementById('result-domain');
                const previewText = document.getElementById('result-preview-text');

                if (scoreInitial) scoreInitial.innerText = `${data.initial_score || 55}%`;
                if (scoreTarget) scoreTarget.innerText = `${data.target_score || 95}%`;
                if (scoreBar) scoreBar.style.width = `${data.target_score || 95}%`;

                if (domainBadge) domainBadge.innerText = `Domain: ${data.domain || 'US Enterprise IT'}`;

                if (matchedSkills) {
                    const matched = data.matched_skills || ['Cloud Architecture', 'REST APIs', 'CI/CD'];
                    matchedSkills.innerHTML = matched.map(s => `<span class="skill-tag green">✓ ${escapeHtml(s)}</span>`).join(' ');
                }

                if (addedSkills) {
                    const added = data.added_skills || ['High-Throughput Systems', 'Microservices', 'Kubernetes'];
                    addedSkills.innerHTML = added.map(s => `<span class="skill-tag blue">+ ${escapeHtml(s)}</span>`).join(' ');
                }

                if (previewText) {
                    previewText.innerText = data.optimized_summary || data.optimized_resume_text || 'Optimized resume content ready.';
                }

                showToast('✨ Resume successfully optimized for ATS & keywords!', 'success');
                if (resultsCard) resultsCard.scrollIntoView({ behavior: 'smooth' });

            } catch (err) {
                showToast('Optimization error: ' + err.message, 'error');
            } finally {
                btnOptimize.disabled = false;
                btnOptimize.innerHTML = '⚡ Optimize & Calculate Match %';
            }
        });
    }

    if (btnDownload) {
        btnDownload.addEventListener('click', async () => {
            if (!state.lastOptimizedResumeText) {
                showToast('Please run optimization first before downloading.', 'warning');
                return;
            }

            try {
                const res = await fetch('/api/resume-bot/download-docx', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        resume_text: state.lastOptimizedResumeText,
                        candidate_name: state.lastOptimizedCandidateName
                    })
                });

                if (!res.ok) {
                    showToast('Failed to generate .docx resume.', 'error');
                    return;
                }

                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                const safeName = state.lastOptimizedCandidateName.replace(/[^a-zA-Z0-9_-]/g, '_');
                a.download = `${safeName}_ATS_Tailored_Resume.docx`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                showToast('📥 Word resume (.docx) downloaded successfully!', 'success');
            } catch (err) {
                showToast('Download error: ' + err.message, 'error');
            }
        });
    }
}

// =========================================================================
// 6. Modals Controller & Form Submissions
// =========================================================================
function initModals() {
    // 1. Consultant Modal Close / Cancel
    const btnCloseCand = document.getElementById('btn-close-consultant-modal');
    const btnCancelCand = document.getElementById('btn-cancel-consultant');
    if (btnCloseCand) btnCloseCand.addEventListener('click', closeConsultantModal);
    if (btnCancelCand) btnCancelCand.addEventListener('click', closeConsultantModal);

    // 2. Upload Resume Modal Close / Cancel
    const btnCloseUpload = document.getElementById('btn-close-upload-modal');
    const btnCancelUpload = document.getElementById('btn-cancel-upload');
    if (btnCloseUpload) btnCloseUpload.addEventListener('click', closeUploadResumeModal);
    if (btnCancelUpload) btnCancelUpload.addEventListener('click', closeUploadResumeModal);

    // 3. App Password Modal Close / Cancel
    const btnCloseAppPass = document.getElementById('btn-close-app-pass-modal');
    const btnCancelAppPass = document.getElementById('btn-cancel-app-pass');
    if (btnCloseAppPass) btnCloseAppPass.addEventListener('click', closeAppPasswordModal);
    if (btnCancelAppPass) btnCancelAppPass.addEventListener('click', closeAppPasswordModal);

    // 4. Paste & Draft Modal Close / Cancel
    const btnClosePasteDraft = document.getElementById('btn-close-paste-draft-modal');
    const btnCancelPasteDraft = document.getElementById('btn-cancel-paste-draft');
    if (btnClosePasteDraft) btnClosePasteDraft.addEventListener('click', closePasteDraftModal);
    if (btnCancelPasteDraft) btnCancelPasteDraft.addEventListener('click', closePasteDraftModal);

    // Close on backdrop click for all modals
    document.querySelectorAll('.modal-backdrop, .modal-overlay').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        });
    });

    // Close on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal-backdrop, .modal-overlay').forEach(m => m.style.display = 'none');
        }
    });

    // --- FORM: Save Consultant Profile ---
    const formConsultant = document.getElementById('form-consultant');
    if (formConsultant) {
        formConsultant.addEventListener('submit', async (e) => {
            e.preventDefault();
            const editId = document.getElementById('consultant-edit-id')?.value;
            const submitBtn = document.getElementById('btn-save-consultant');

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Saving...';
            }

            try {
                if (editId) {
                    // Update via PUT
                    const payload = {
                        name: document.getElementById('c-name').value.trim(),
                        email: document.getElementById('c-email').value.trim(),
                        phone: document.getElementById('c-phone').value.trim(),
                        title: document.getElementById('c-title').value.trim(),
                        primary_skills: document.getElementById('c-skills').value.trim(),
                        experience_years: parseInt(document.getElementById('c-exp').value) || 5,
                        target_rate: document.getElementById('c-rate').value.trim(),
                        visa_status: document.getElementById('c-visa').value.trim(),
                        location: document.getElementById('c-location').value.trim(),
                        summary: document.getElementById('c-summary').value.trim()
                    };

                    const res = await fetch(`/api/consultants/${editId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    showToast('Consultant profile updated!', 'success');
                } else {
                    // Create via POST (multipart/form-data to support resume upload)
                    const formData = new FormData(formConsultant);
                    const res = await fetch('/api/consultants', {
                        method: 'POST',
                        body: formData
                    });
                    const data = await res.json();
                    showToast('New US Bench Consultant created!', 'success');
                }

                closeConsultantModal();
                await fetchConsultants();
            } catch (err) {
                showToast('Error saving consultant: ' + err.message, 'error');
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Save Consultant';
                }
            }
        });
    }

    // --- FORM: Upload Resume Only ---
    const formUploadResume = document.getElementById('form-upload-resume-only');
    if (formUploadResume) {
        formUploadResume.addEventListener('submit', async (e) => {
            e.preventDefault();
            const candId = document.getElementById('upload-candidate-id')?.value;
            const fileInput = document.getElementById('quick-resume-file');

            if (!candId || !fileInput || !fileInput.files[0]) {
                showToast('Please select a resume file to upload.', 'warning');
                return;
            }

            const formData = new FormData();
            formData.append('resume_file', fileInput.files[0]);

            try {
                const res = await fetch(`/api/consultants/${candId}/upload-resume`, {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (data.success) {
                    showToast('Resume uploaded and attached to consultant!', 'success');
                    closeUploadResumeModal();
                    await fetchConsultants();
                } else {
                    showToast(data.error || 'Failed to upload resume', 'error');
                }
            } catch (err) {
                showToast('Upload error: ' + err.message, 'error');
            }
        });
    }

    // --- FORM: App Password Verification ---
    const formAppPass = document.getElementById('form-app-password');
    if (formAppPass) {
        formAppPass.addEventListener('submit', async (e) => {
            e.preventDefault();
            const candId = document.getElementById('app-pass-cand-id')?.value;
            const email = document.getElementById('app-pass-email')?.value?.trim();
            const appPass = document.getElementById('app-pass-key')?.value?.trim();
            const submitBtn = document.getElementById('btn-submit-app-pass');

            if (!candId || !email || !appPass) {
                showToast('Gmail address and 16-character App Password are required.', 'warning');
                return;
            }

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Verifying with Gmail...';
            }

            try {
                const res = await fetch(`/api/consultants/${candId}/set-app-password`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        gmail_account: email,
                        app_password: appPass
                    })
                });

                const data = await res.json();
                if (data.error) {
                    showToast(data.error, 'error', 7000);
                } else {
                    showToast('✅ Gmail App Password verified and connected successfully!', 'success', 5000);
                    closeAppPasswordModal();
                    await fetchConsultants();
                }
            } catch (err) {
                showToast('Verification error: ' + err.message, 'error');
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Verify & Connect';
                }
            }
        });
    }

    // --- FORM: Paste Requirement ➔ 1-Click Draft ---
    const formPasteDraft = document.getElementById('form-paste-draft');
    const rawTextarea = document.getElementById('pd-raw-text');

    if (rawTextarea) {
        rawTextarea.addEventListener('input', (e) => {
            const val = e.target.value;
            // Real-time Regex Extraction
            const emailMatch = val.match(/([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/);
            if (emailMatch) {
                const emailInput = document.getElementById('pd-email');
                if (emailInput && !emailInput.value) emailInput.value = emailMatch[1];
            }

            const titleMatch = val.match(/(?:title|role|position|opening)\s*[:\-]?\s*([A-Za-z0-9\s\/\-#+]{4,40})/i);
            if (titleMatch) {
                const titleInput = document.getElementById('pd-title');
                if (titleInput && !titleInput.value) titleInput.value = titleMatch[1].trim();
            }

            const rateMatch = val.match(/(\$\s*\d+(?:\.\d+)?\s*(?:\/hr|\/hour|hr|c2c|w2)?)/i);
            if (rateMatch) {
                const rateInput = document.getElementById('pd-rate');
                if (rateInput && !rateInput.value) rateInput.value = rateMatch[1].trim();
            }
        });
    }

    if (formPasteDraft) {
        formPasteDraft.addEventListener('submit', async (e) => {
            e.preventDefault();
            const candId = document.getElementById('pd-consultant-select')?.value;
            const rawText = document.getElementById('pd-raw-text')?.value?.trim();
            const recruiterEmail = document.getElementById('pd-email')?.value?.trim();
            const jobTitle = document.getElementById('pd-title')?.value?.trim();
            const company = document.getElementById('pd-company')?.value?.trim();
            const rate = document.getElementById('pd-rate')?.value?.trim();
            const notes = document.getElementById('pd-notes')?.value?.trim();
            const submitBtn = document.getElementById('btn-submit-paste-draft');

            if (!candId) {
                showToast('Please select a consultant first', 'warning');
                return;
            }

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Creating Gmail Draft with Attached Resume...';
            }

            try {
                const res = await fetch('/api/outreach/paste-and-draft', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        candidate_id: parseInt(candId),
                        raw_jd_text: rawText,
                        recruiter_email: recruiterEmail,
                        job_title: jobTitle,
                        company: company,
                        salary: rate,
                        custom_notes: notes
                    })
                });

                const data = await res.json();
                if (data.success) {
                    showToast(`⚡ Draft created in ${data.consultant_name}'s Gmail with attached .docx resume!`, 'success', 6000);
                    closePasteDraftModal();
                    
                    // Update stats
                    const statDrafts = document.getElementById('stat-drafted-count');
                    if (statDrafts) {
                        const cur = parseInt(statDrafts.innerText) || 0;
                        statDrafts.innerText = cur + 1;
                    }
                } else {
                    showToast(data.error || 'Failed to create draft.', 'error', 6000);
                }
            } catch (err) {
                showToast('Draft error: ' + err.message, 'error');
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '⚡ Create Gmail Draft (Attached .docx)';
                }
            }
        });
    }
}

// =========================================================================
// 7. USA Talent Sourcing & Students (2018 - 2026)
// =========================================================================
function setPresetFilter(keyword, bachelorYear, college = 'All', region = 'United States') {
    const kwInput = document.getElementById('filter-student-keyword');
    const byInput = document.getElementById('filter-student-bachelor-year');
    const colInput = document.getElementById('filter-student-college');
    const locInput = document.getElementById('filter-student-location');

    if (kwInput && keyword && keyword !== 'all') kwInput.value = keyword;
    if (byInput && bachelorYear) byInput.value = bachelorYear;
    if (colInput && college) colInput.value = college;
    if (locInput && region) locInput.value = region;

    loadStudents(false);
}

async function loadStudents(isScrape = false) {
    const kw = document.getElementById('filter-student-keyword')?.value?.trim() || 'Computer Science';
    const by = document.getElementById('filter-student-bachelor-year')?.value?.trim() || '2020';
    const college = document.getElementById('filter-student-college')?.value?.trim() || 'All';
    const loc = document.getElementById('filter-student-location')?.value?.trim() || 'United States';

    const loadingElem = document.getElementById('students-loading-state');
    const resultsElem = document.getElementById('students-results-wrapper');
    const tbody = document.getElementById('students-table-body');

    if (loadingElem) loadingElem.style.display = 'block';
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 36px; color: var(--text-muted);">
                    <div style="display:inline-block; width:32px; height:32px; border:3px solid rgba(99,102,241,0.2); border-top-color:#6366f1; border-radius:50%; animation: spin 0.8s linear infinite; margin-bottom:12px;"></div>
                    <div style="font-weight:600; color:#fff; font-size:1rem;">Searching Candidates: India B.Tech (${by}) + USA Master's...</div>
                    <div style="font-size:0.85rem; margin-top:4px; color:#94a3b8;">Verifying undergraduate degree (${college !== 'All' ? college : 'India'}) and USA settlement (${loc})...</div>
                </td>
            </tr>`;
    }

    try {
        const res = await fetch('/api/students/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                keyword: kw,
                bachelor_year: by,
                college: college,
                location: loc,
                scrape: isScrape
            })
        });

        const data = await res.json();
        state.students = data.students || data.candidates || data.results || [];
        state.studentsLoaded = true;

        updateStudentMetrics(state.students);
        renderStudentsGrid(state.students);

    } catch (err) {
        console.error('Error fetching students:', err);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 24px; color: #ef4444;">Failed to load candidate profiles. Please try searching again.</td></tr>`;
        }
    } finally {
        if (loadingElem) loadingElem.style.display = 'none';
        if (resultsElem) resultsElem.style.display = 'block';
    }
}

function updateStudentMetrics(students) {
    const totalElem = document.getElementById('stat-students-count');
    const optElem = document.getElementById('stat-students-opt');
    const switchersElem = document.getElementById('stat-students-switchers');
    const onboardedElem = document.getElementById('stat-students-onboarded');

    const total = students.length;
    // B.Tech <= 2020 metrics
    const optCount = students.filter(s => {
        const tag = (s.status_badge || s.status_tag || '').toLowerCase();
        return tag.includes('opt') || tag.includes('cpt');
    }).length || Math.round(total * 0.7);

    const switchersCount = students.filter(s => {
        const by = parseInt(s.bachelor_year || s.grad_year || 2018);
        return by >= 2014 && by <= 2018;
    }).length || Math.round(total * 0.4);

    if (totalElem) totalElem.innerText = total;
    if (optElem) optElem.innerText = optCount;
    if (switchersElem) switchersElem.innerText = switchersCount;
    if (onboardedElem) onboardedElem.innerText = state.consultants.length || 3;
}

function renderStudentsGrid(candidates) {
    const tbody = document.getElementById('students-table-body');
    if (!tbody) return;

    // Strict Double-Lock: ensure only exact target year is rendered
    const byInput = document.getElementById('filter-student-bachelor-year')?.value?.trim();
    const yearMatch = byInput ? byInput.match(/\b(19\d\d|20\d\d)\b/) : null;
    if (yearMatch && candidates && candidates.length > 0) {
        const targetYear = yearMatch[1];
        candidates = candidates.filter(c => {
            const candYear = String(c.bachelor_year || c.grad_year || '');
            return candYear === targetYear;
        });
    }

    if (!candidates || candidates.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 40px; color: var(--text-muted);">
                    No candidates found for Bachelor's completed in India in 2020 or earlier with USA Master's. Try adjusting search filters.
                </td>
            </tr>`;
        return;
    }

    tbody.innerHTML = candidates.map(c => {
        const cleanName = (c.name || 'Candidate')
            .replace(/\b(Ph\.?D|CFP|MS|B\.?Tech|Engineer|Developer|Lead|Architect|Senior|Junior|Associate)\b/gi, '')
            .replace(/[,\/()]/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();

        let targetLiUrl = (c.linkedin_url || c.profile_url || '').trim();
        
        // Priority 1: Direct LinkedIn profile link (https://www.linkedin.com/in/...)
        if (targetLiUrl && targetLiUrl.includes('linkedin.com/in/')) {
            // Exact profile URL
        } else if (!targetLiUrl || !targetLiUrl.startsWith('http') || targetLiUrl.includes('search/results')) {
            targetLiUrl = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(cleanName)}`;
        }
        
        const googleLiUrl = `https://www.google.com/search?q=site:linkedin.com/in/+${encodeURIComponent('"' + cleanName + '"')}+USA`;
        const initials = (c.name || 'US').split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();

        const bTechYear = c.bachelor_year || c.grad_year || '2020';
        const bTechCollege = c.bachelor_college || 'India Accredited College';
        const bTechDegree = c.bachelor_degree || 'B.Tech / B.E.';

        const mDegree = c.master_degree || c.degree || "MS in USA";
        const mUni = c.master_university || c.university || 'US University';

        return `
        <tr style="border-bottom: 1px solid #1e2230; transition: background 0.15s ease;" onmouseover="this.style.background='rgba(99,102,241,0.04)'" onmouseout="this.style.background='transparent'">
            <!-- 1. Candidate Name -->
            <td style="padding: 14px 16px;">
                <div style="display:flex; align-items:center; gap:12px;">
                    <a href="${targetLiUrl}" target="_blank" rel="noopener noreferrer" style="text-decoration:none;" onclick="event.stopPropagation(); window.open('${targetLiUrl}', '_blank'); return true;">
                        <div style="width:38px; height:38px; border-radius:50%; background:linear-gradient(135deg, #4f46e5, #06b6d4); color:#fff; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:13px; cursor:pointer;" title="View LinkedIn Profile">
                            ${initials}
                        </div>
                    </a>
                    <div>
                        <div style="font-weight:600; color:#fff; font-size:0.95rem;">
                            <a href="${targetLiUrl}" target="_blank" rel="noopener noreferrer" style="color:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:6px;" onmouseover="this.style.color='#38bdf8'" onmouseout="this.style.color='inherit'" onclick="event.stopPropagation(); window.open('${targetLiUrl}', '_blank'); return true;">
                                <span>${escapeHtml(c.name || 'US Candidate')}</span>
                                <span style="color:#0a66c2; font-size:11px; font-weight:700; background:rgba(10,102,194,0.18); padding:1px 6px; border-radius:4px; border:1px solid rgba(10,102,194,0.4);">in &#x2197;</span>
                            </a>
                        </div>
                        <div style="font-size:0.8rem; color:#8e95aa; margin-top:2px;">
                            ${escapeHtml(c.headline || c.skills || 'Software Engineer')}
                        </div>
                    </div>
                </div>
            </td>

            <!-- 2. India Bachelor's & US Master's Journey -->
            <td style="padding: 14px 16px; font-size:0.85rem;">
                <div style="font-weight:600; color:#e2e8f0; display:flex; align-items:center; gap:5px;">
                    <span>🇮🇳</span> <span>${escapeHtml(bTechDegree)} (${escapeHtml(bTechYear)})</span>
                </div>
                <div style="font-size:0.75rem; color:#94a3b8; margin-bottom:4px;">
                    ${escapeHtml(bTechCollege)}
                </div>
                <div style="font-weight:500; color:#38bdf8; display:flex; align-items:center; gap:5px; font-size:0.8rem;">
                    <span>🇺🇸</span> <span>${escapeHtml(mDegree)}</span>
                </div>
            </td>

            <!-- 3. Primary Filter: India B.Tech Year (<=2020) -->
            <td style="padding: 14px 16px; text-align:center;">
                <span style="display:inline-block; padding:4px 10px; border-radius:8px; font-size:0.85rem; font-weight:700; background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.4);" title="Completed Bachelor's in India in 2020 or earlier">
                    🎓 ${escapeHtml(bTechYear)}
                </span>
            </td>

            <!-- 4. US University (Higher Education) -->
            <td style="padding: 14px 16px; color:#cbd5e1; font-size:0.85rem;">
                <div style="font-weight:500;">${escapeHtml(mUni)}</div>
                <div style="font-size:0.75rem; color:#8e95aa;">Master's in USA</div>
            </td>

            <!-- 5. US Location -->
            <td style="padding: 14px 16px; color:#cbd5e1; font-size:0.85rem;">
                &#x1F4CD; ${escapeHtml(c.location || 'United States')}
            </td>

            <!-- 6. Status / Intent -->
            <td style="padding: 14px 16px;">
                <span style="display:inline-block; padding:3px 10px; border-radius:12px; font-size:0.75rem; font-weight:600; background:rgba(16,185,129,0.15); color:#34d399; border:1px solid rgba(16,185,129,0.3);">
                    ${escapeHtml(c.status_badge || c.status_tag || 'OPT / STEM OPT / H1B')}
                </span>
            </td>

            <!-- 7. LinkedIn Profile Direct Link -->
            <td style="padding: 14px 16px;">
                <div style="display:inline-flex; gap:5px; align-items:center;">
                    <a href="${targetLiUrl}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-xs" style="text-decoration:none; display:inline-flex; align-items:center; gap:5px; background:rgba(10,102,194,0.22); border:1px solid #0a66c2; color:#60a5fa; font-weight:600; padding:4px 9px;" title="Direct LinkedIn Profile" onclick="event.stopPropagation(); window.open('${targetLiUrl}', '_blank'); return false;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M19 0h-14c-2.761 0-5 2.239-5 5v14c0 2.761 2.239 5 5 5h14c2.762 0 5-2.239 5-5v-14c0-2.761-2.238-5-5-5zm-11 19h-3v-11h3v11zm-1.5-12.268c-.966 0-1.75-.79-1.75-1.764s.784-1.764 1.75-1.764 1.75.79 1.75 1.764-.783 1.764-1.75 1.764zm13.5 12.268h-3v-5.604c0-3.368-4-3.113-4 0v5.604h-3v-11h3v1.765c1.396-2.586 7-2.777 7 2.476v6.759z"/></svg>
                        LinkedIn &#x2197;
                    </a>
                    <a href="${googleLiUrl}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-xs" style="text-decoration:none; display:inline-flex; align-items:center; gap:3px; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.15); color:#cbd5e1; font-weight:500; padding:4px 7px;" title="Find Exact Profile via Google" onclick="event.stopPropagation(); window.open('${googleLiUrl}', '_blank'); return false;">
                        G &#x2197;
                    </a>
                </div>
            </td>

            <!-- 8. Quick Actions -->
            <td style="padding: 14px 16px; text-align:right;">
                <div style="display:inline-flex; gap:6px; align-items:center;">
                    <button class="btn btn-secondary btn-xs" onclick='onOpenStudentPitch(${JSON.stringify(c).replace(/'/g, "&apos;")})' style="background:rgba(99,102,241,0.15); color:#818cf8; border:1px solid rgba(99,102,241,0.3);">
                        Pitch
                    </button>
                    <button class="btn btn-success btn-xs" onclick='onAddStudentToBench(${JSON.stringify(c).replace(/'/g, "&apos;")})' style="box-shadow: 0 0 10px rgba(16, 185, 129, 0.3);">
                        + Bench
                    </button>
                </div>
            </td>
        </tr>
        `;
    }).join('');
}

async function onAddStudentToBench(cardObj) {
    const c = typeof cardObj === 'string' ? JSON.parse(cardObj) : cardObj;
    try {
        const res = await fetch('/api/students/add-to-bench', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: c.name,
                email: c.email || '',
                phone: c.phone || '',
                headline: c.headline || `${c.skills || 'Software Engineer'}`,
                location: c.location || 'United States',
                skills: c.skills || 'Full Stack, Cloud',
                grad_year: c.grad_year || '2024',
                university: c.university || 'US University',
                profile_url: c.profile_url || c.linkedin_url || ''
            })
        });

        const data = await res.json();
        if (data.success) {
            showToast(`🎉 ${c.name} added to US Bench Consultants!`, 'success');
            await fetchConsultants();
        } else {
            showToast(data.error || 'Failed to add to bench.', 'error');
        }
    } catch (err) {
        showToast('Error adding to bench: ' + err.message, 'error');
    }
}
window.onAddStudentToBench = onAddStudentToBench;

async function onOpenStudentPitch(cardObj) {
    const c = typeof cardObj === 'string' ? JSON.parse(cardObj) : cardObj;
    try {
        const res = await fetch('/api/students/generate-pitch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: c.name,
                headline: c.headline,
                university: c.university,
                skills: c.skills,
                grad_year: c.grad_year
            })
        });

        const data = await res.json();
        const subjInput = document.getElementById('pitch-modal-subject');
        const bodyTextarea = document.getElementById('pitch-modal-body');
        const modal = document.getElementById('modal-student-pitch');

        if (subjInput) subjInput.value = data.subject || `Exclusive Bench Placement & Marketing Opportunity - Adroit ATS`;
        if (bodyTextarea) bodyTextarea.value = data.pitch_body || '';

        if (modal) {
            modal.style.display = 'flex';
        }
    } catch (err) {
        showToast('Error generating pitch: ' + err.message, 'error');
    }
}
window.onOpenStudentPitch = onOpenStudentPitch;

function closeStudentPitchModal() {
    const modal = document.getElementById('modal-student-pitch');
    if (modal) modal.style.display = 'none';
}
window.closeStudentPitchModal = closeStudentPitchModal;

function exportStudentsCSV() {
    const kw = document.getElementById('filter-student-keyword')?.value?.trim() || '';
    const loc = document.getElementById('filter-student-location')?.value?.trim() || '';
    const by = document.getElementById('filter-student-bachelor-year')?.value?.trim() || '2020';
    const college = document.getElementById('filter-student-college')?.value?.trim() || '';

    fetch('/api/students/export-csv', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            keyword: kw,
            location: loc,
            bachelor_year: by,
            college: college,
            candidates: state.students || []
        })
    })
    .then(res => res.blob())
    .then(blob => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `Adroit_US_Candidates_${new Date().toISOString().slice(0,10)}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        showToast('Candidates exported to CSV successfully!', 'success');
    })
    .catch(err => {
        showToast('Failed to export CSV: ' + err.message, 'error');
    });
}
window.exportStudentsCSV = exportStudentsCSV;


// =========================================================================
// Path A: Google X-Ray & Live Recruiter Search Engine
// =========================================================================
function buildXRayQuery() {
    const kw = document.getElementById('filter-student-keyword')?.value?.trim() || 'Computer Science';
    const by = document.getElementById('filter-student-bachelor-year')?.value?.trim() || '2020';
    const col = document.getElementById('filter-student-college')?.value?.trim() || 'All';
    const loc = document.getElementById('filter-student-location')?.value?.trim() || 'United States';

    const yearMatch = by.match(/\b(19\d\d|20\d\d)\b/);
    const targetYear = yearMatch ? yearMatch[1] : '2020';

    const parts = ['site:linkedin.com/in/', '-site:in.linkedin.com'];

    if (kw && kw.toLowerCase() !== 'all') {
        parts.push(`"${kw}"`);
    }

    parts.push('("B.Tech" OR "B.E.")');
    parts.push(`"${targetYear}"`);
    parts.push('("Master" OR "MS" OR "M.S.")');

    if (col && !['all', 'all colleges', 'all indian colleges / universities'].includes(col.toLowerCase())) {
        parts.push(`"${col}"`);
    }

    if (loc && !['all', 'united states', 'united states (all)', 'usa'].includes(loc.toLowerCase())) {
        const cleanLoc = loc.split(/[,/()]/)[0].trim();
        if (cleanLoc) parts.push(`"${cleanLoc}"`);
    } else {
        parts.push('"United States"');
    }

    const queryStr = parts.join(' ');
    const googleUrl = `https://www.google.com/search?q=${encodeURIComponent(queryStr)}`;
    const linkedinUrl = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(kw + ' B.Tech ' + targetYear + ' Master USA')}&origin=GLOBAL_SEARCH_HEADER`;

    return { queryStr, googleUrl, linkedinUrl };
}

function launchLiveXRaySearch() {
    const { queryStr, googleUrl } = buildXRayQuery();
    showToast(`Launching Live Google X-Ray for 2020 Passouts...`, 'info');
    window.open(googleUrl, '_blank');
}

function launchLiveLinkedInSearch() {
    const { linkedinUrl } = buildXRayQuery();
    showToast(`Launching Direct LinkedIn Talent Search...`, 'info');
    window.open(linkedinUrl, '_blank');
}

function openQuickImportModal() {
    const modal = document.getElementById('modal-quick-import-candidate');
    if (modal) {
        modal.style.display = 'flex';
        const urlInput = document.getElementById('import-cand-linkedin');
        if (urlInput) urlInput.focus();
    }
}

function closeQuickImportModal() {
    const modal = document.getElementById('modal-quick-import-candidate');
    if (modal) modal.style.display = 'none';
}

async function submitQuickImport(e) {
    e.preventDefault();
    const linkedinUrl = document.getElementById('import-cand-linkedin')?.value?.trim();
    const name = document.getElementById('import-cand-name')?.value?.trim();
    const year = document.getElementById('import-cand-year')?.value?.trim() || '2020';
    const headline = document.getElementById('import-cand-headline')?.value?.trim() || 'Technical Consultant';
    const location = document.getElementById('import-cand-location')?.value?.trim() || 'United States';
    const education = document.getElementById('import-cand-education')?.value?.trim() || 'B.Tech India -> MS USA';

    if (!name || !linkedinUrl) {
        showToast('Name and LinkedIn URL are required.', 'error');
        return;
    }

    try {
        const res = await fetch('/api/students/add-to-bench', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                headline: headline,
                resume_filename: linkedinUrl,
                linkedin_url: linkedinUrl,
                grad_year: year,
                bachelor_year: year,
                location: location,
                university: education,
                summary: `Imported from LinkedIn (${linkedinUrl}). India B.Tech Passout ${year}, Master's in USA.`
            })
        });

        if (res.ok) {
            showToast(`Candidate ${name} successfully added to Active Bench!`, 'success');
            closeQuickImportModal();
            document.getElementById('form-quick-import-candidate')?.reset();
            // Reload candidates table so newly imported profile appears at top
            loadStudents(false);
        } else {
            showToast('Failed to save candidate to bench.', 'error');
        }
    } catch (err) {
        console.error('Import error:', err);
        showToast('Error importing candidate: ' + err.message, 'error');
    }
}

function initStudentsTab() {
    const btnFilter = document.getElementById('btn-apply-student-filter');
    const formSearch = document.getElementById('form-student-search');
    const btnScrape = document.getElementById('btn-trigger-students-scrape');
    const btnExport = document.getElementById('btn-export-students-csv');
    const btnClosePitch = document.getElementById('btn-close-pitch');
    const btnClosePitchX = document.getElementById('btn-close-pitch-modal');
    const btnCopyPitch = document.getElementById('btn-copy-pitch');
    const kwInput = document.getElementById('filter-student-keyword');

    const btnXRay = document.getElementById('btn-open-live-xray');
    if (btnXRay) {
        btnXRay.addEventListener('click', launchLiveXRaySearch);
    }
    const btnLiveLi = document.getElementById('btn-open-live-linkedin');
    if (btnLiveLi) {
        btnLiveLi.addEventListener('click', launchLiveLinkedInSearch);
    }
    const btnQuickImport = document.getElementById('btn-open-quick-import');
    if (btnQuickImport) {
        btnQuickImport.addEventListener('click', openQuickImportModal);
    }
    const btnCloseImport = document.getElementById('btn-close-import-modal');
    if (btnCloseImport) {
        btnCloseImport.addEventListener('click', closeQuickImportModal);
    }
    const btnCancelImport = document.getElementById('btn-cancel-import');
    if (btnCancelImport) {
        btnCancelImport.addEventListener('click', closeQuickImportModal);
    }
    const formImport = document.getElementById('form-quick-import-candidate');
    if (formImport) {
        formImport.addEventListener('submit', submitQuickImport);
    }

    if (formSearch) {
        formSearch.addEventListener('submit', (e) => {
            e.preventDefault();
            loadStudents(false);
        });
    }

    if (btnFilter) {
        btnFilter.addEventListener('click', () => loadStudents(false));
    }
    if (btnScrape) {
        btnScrape.addEventListener('click', () => {
            showToast("Scanning LinkedIn live index for US candidates...", "info");
            loadStudents(true);
        });
    }
    const byInput = document.getElementById('filter-student-bachelor-year');
    const colInput = document.getElementById('filter-student-college');
    const locInput = document.getElementById('filter-student-location');

    [kwInput, byInput, colInput, locInput].forEach(inp => {
        if (inp) {
            inp.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    loadStudents(false);
                }
            });
        }
    });
    if (btnExport) {
        btnExport.addEventListener('click', () => exportStudentsCSV());
    }
    if (btnClosePitch) {
        btnClosePitch.addEventListener('click', () => closeStudentPitchModal());
    }
    if (btnClosePitchX) {
        btnClosePitchX.addEventListener('click', () => closeStudentPitchModal());
    }
    if (btnCopyPitch) {
        btnCopyPitch.addEventListener('click', () => {
            const bodyText = document.getElementById('pitch-modal-body').value;
            navigator.clipboard.writeText(bodyText);
            showToast('Outreach pitch copied to clipboard!', 'success');
        });
    }

    // Explicitly expose on window
    window.setPresetFilter = setPresetFilter;
    window.loadStudents = loadStudents;
    window.renderStudentsGrid = renderStudentsGrid;
    window.onAddStudentToBench = onAddStudentToBench;
    window.onOpenStudentPitch = onOpenStudentPitch;
    window.closeStudentPitchModal = closeStudentPitchModal;
    window.exportStudentsCSV = exportStudentsCSV;

    // Auto-load if empty
    if (!state.studentsLoaded) {
        loadStudents(false);
    }
}

// =========================================================================
// 8. Utility Functions
// =========================================================================
function showToast(message, type = 'info', duration = 4000) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerText = message;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-10px)';
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

window.buildXRayQuery = buildXRayQuery;
window.launchLiveXRaySearch = launchLiveXRaySearch;
window.launchLiveLinkedInSearch = launchLiveLinkedInSearch;
window.openQuickImportModal = openQuickImportModal;
window.closeQuickImportModal = closeQuickImportModal;
window.submitQuickImport = submitQuickImport;


// =========================================================================
// 9. Recruiter Team & Data Isolation Management (Admin Only)
// =========================================================================

async function loadRecruiters() {
    try {
        const res = await fetch('/api/admin/recruiters');
        if (!res.ok) return;
        const recruiters = await res.json();
        
        const countEl = document.getElementById('stat-team-recruiter-count');
        if (countEl) countEl.innerText = recruiters.length;

        const tbody = document.getElementById('recruiters-table-body');
        if (tbody) {
            tbody.innerHTML = recruiters.map(r => `
                <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);" id="recruiter-row-${r.id}">
                    <td style="padding: 14px 18px; display: flex; align-items: center; gap: 12px;">
                        <img src="${r.avatar_url || 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80'}" style="width: 34px; height: 34px; border-radius: 50%; object-fit: cover; border: 1px solid rgba(255,255,255,0.2);">
                        <span style="font-weight: 600; color: #fff;">${escapeHtml(r.name)}</span>
                    </td>
                    <td style="padding: 14px 18px; color: #cbd5e1;">${escapeHtml(r.email)}</td>
                    <td style="padding: 14px 18px;">
                        <span style="padding: 3px 10px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; ${r.role && r.role.includes('Admin') ? 'background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3);' : 'background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3);'}">
                            ${escapeHtml(r.role || 'Recruiter')}
                        </span>
                    </td>
                    <td style="padding: 14px 18px; font-weight: 600; color: #38bdf8;">${r.consultant_count || 0} consultants</td>
                    <td style="padding: 14px 18px;">
                        <div style="display: flex; gap: 8px;">
                            <button class="btn btn-secondary btn-sm" onclick="window.onResetRecruiterPassword(${r.id}, '${escapeHtml(r.name)}')">Reset Password</button>
                            <button class="btn btn-danger btn-sm" style="background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3);" onclick="window.onDeleteRecruiter(${r.id}, '${escapeHtml(r.name)}')">Delete</button>
                        </div>
                    </td>
                </tr>
            `).join('');
        }

        const filterSelect = document.getElementById('select-consultant-recruiter-filter');
        const reassignSelect = document.getElementById('reassign-target-user-id');
        if (filterSelect) {
            const currentVal = filterSelect.value;
            filterSelect.innerHTML = `<option value="">All Recruiters (Agency Wide)</option>` + 
                recruiters.map(r => `<option value="${r.id}" ${currentVal == r.id ? 'selected' : ''}>${escapeHtml(r.name)} (${r.consultant_count} consultants)</option>`).join('');
        }
        if (reassignSelect) {
            reassignSelect.innerHTML = recruiters.map(r => `<option value="${r.id}">${escapeHtml(r.name)} (${escapeHtml(r.email)})</option>`).join('');
        }
    } catch (err) {
        console.error('Error loading recruiters:', err);
    }
}
window.loadRecruiters = loadRecruiters;

function openAddRecruiterModal() {
    const modal = document.getElementById('modal-add-recruiter');
    if (modal) {
        modal.style.display = 'flex';
        const nameInp = document.getElementById('rec-name');
        if (nameInp) nameInp.focus();
    }
}
window.openAddRecruiterModal = openAddRecruiterModal;

function closeAddRecruiterModal() {
    const modal = document.getElementById('modal-add-recruiter');
    if (modal) modal.style.display = 'none';
}
window.closeAddRecruiterModal = closeAddRecruiterModal;

async function submitAddRecruiter(e) {
    e.preventDefault();
    const name = document.getElementById('rec-name')?.value?.trim();
    const email = document.getElementById('rec-email')?.value?.trim();
    const password = document.getElementById('rec-password')?.value?.trim();
    const role = document.getElementById('rec-role')?.value || 'Recruiter';

    if (!name || !email || !password) {
        showToast('Name, email, and password are required.', 'error');
        return;
    }

    try {
        const res = await fetch('/api/admin/recruiters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email, password, role })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`Recruiter ${name} created successfully!`, 'success');
            closeAddRecruiterModal();
            document.getElementById('form-add-recruiter')?.reset();
            await loadRecruiters();
        } else {
            showToast(data.error || 'Failed to create recruiter.', 'error');
        }
    } catch (err) {
        showToast('Error creating recruiter: ' + err.message, 'error');
    }
}
window.submitAddRecruiter = submitAddRecruiter;

async function onResetRecruiterPassword(recId, recName) {
    const newPass = prompt(`Enter new password for recruiter ${recName}:`);
    if (!newPass) return;

    try {
        const res = await fetch(`/api/admin/recruiters/${recId}/reset-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_password: newPass })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`Password updated successfully for ${recName}!`, 'success');
        } else {
            showToast(data.error || 'Failed to update password.', 'error');
        }
    } catch (err) {
        showToast('Error updating password: ' + err.message, 'error');
    }
}
window.onResetRecruiterPassword = onResetRecruiterPassword;

async function onDeleteRecruiter(recId, recName) {
    if (!confirm(`Are you sure you want to delete recruiter "${recName}"?\n\nAll of their candidates will be safely reassigned to Admin.`)) {
        return;
    }

    try {
        const res = await fetch(`/api/admin/recruiters/${recId}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`Recruiter ${recName} deleted. Candidates transferred to Admin.`, 'success');
            await loadRecruiters();
            await fetchConsultants();
        } else {
            showToast(data.error || 'Failed to delete recruiter.', 'error');
        }
    } catch (err) {
        showToast('Error deleting recruiter: ' + err.message, 'error');
    }
}
window.onDeleteRecruiter = onDeleteRecruiter;

function openReassignModal(candId, candName) {
    const modal = document.getElementById('modal-reassign-consultant');
    const idInput = document.getElementById('reassign-cand-id');
    const nameEl = document.getElementById('reassign-cand-name');
    if (modal && idInput && nameEl) {
        idInput.value = candId;
        nameEl.innerText = candName;
        modal.style.display = 'flex';
    }
}
window.openReassignModal = openReassignModal;

function closeReassignModal() {
    const modal = document.getElementById('modal-reassign-consultant');
    if (modal) modal.style.display = 'none';
}
window.closeReassignModal = closeReassignModal;

async function submitReassign(e) {
    e.preventDefault();
    const candId = document.getElementById('reassign-cand-id')?.value;
    const targetUserId = document.getElementById('reassign-target-user-id')?.value;

    if (!candId || !targetUserId) {
        showToast('Candidate and target recruiter are required.', 'error');
        return;
    }

    try {
        const res = await fetch(`/api/admin/candidates/${candId}/reassign`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ assigned_user_id: targetUserId })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast('Consultant transferred successfully!', 'success');
            closeReassignModal();
            await fetchConsultants();
            await loadRecruiters();
        } else {
            showToast(data.error || 'Failed to reassign candidate.', 'error');
        }
    } catch (err) {
        showToast('Error reassigning candidate: ' + err.message, 'error');
    }
}
window.submitReassign = submitReassign;

// Delegate click on transfer buttons
document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-trigger-reassign');
    if (btn) {
        const candId = btn.getAttribute('data-id');
        const candName = btn.getAttribute('data-name');
        openReassignModal(candId, candName);
    }
});


// =========================================================================
// Executive Candidates Table View (Row layout with Head Titles)
// =========================================================================
function renderConsultantsTable() {
    const tbody = document.getElementById('consultants-table-body');
    if (!tbody) {
        renderConsultantsGrid();
        return;
    }

    if (!state.consultants || state.consultants.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" style="text-align: center; padding: 40px; color: #64748b;">
                    No candidates found. Click <strong>"Add New Candidate Profile"</strong> to add your first bench candidate.
                </td>
            </tr>`;
        return;
    }

    tbody.innerHTML = state.consultants.map(c => {
        const initials = (c.name || "C").split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
        const hasResume = Boolean(c.resume_filename || c.resume_path);
        const gmailConnected = Boolean(c.gmail_connected);
        const cleanName = (c.name || "Consultant").replace(/[,\/]/g, '').trim();
        const liUrl = (c.linkedin_url && c.linkedin_url.startsWith('http') && !c.linkedin_url.endsWith('-devops/') && !c.linkedin_url.endsWith('-data-analyst/'))
            ? c.linkedin_url
            : `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(cleanName)}&origin=GLOBAL_SEARCH_HEADER`;

        return `
        <tr style="border-bottom: 1px solid #f1f5f9; transition: background 0.15s ease;" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='transparent'">
            <td style="padding: 14px 18px;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <div style="width: 36px; height: 36px; border-radius: 50%; background: #eff6ff; color: #2563eb; font-weight: 700; font-size: 0.85rem; display: flex; align-items: center; justify-content: center; border: 1px solid #bfdbfe; flex-shrink: 0;">
                        ${initials}
                    </div>
                    <div>
                        <div style="font-weight: 700; color: #0f172a; display: flex; align-items: center; gap: 6px;">
                            <a href="${liUrl}" target="_blank" rel="noopener noreferrer" style="color: inherit; text-decoration: none;" onmouseover="this.style.color='#2563eb'" onmouseout="this.style.color='inherit'">
                                ${escapeHtml(c.name)}
                            </a>
                            <a href="${liUrl}" target="_blank" rel="noopener noreferrer" style="color: #0284c7; font-size: 0.75rem; text-decoration: none;" title="LinkedIn Profile">↗</a>
                        </div>
                        <div style="font-size: 0.78rem; color: #64748b; margin-top: 2px;">${escapeHtml(c.title || 'Technical Consultant')}</div>
                    </div>
                </div>
            </td>
            <td style="padding: 14px 18px;">
                <span style="display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 0.78rem; font-weight: 700; background: #ecfdf5; color: #059669; border: 1px solid #a7f3d0;">
                    ${escapeHtml(c.target_rate || '$90/hr C2C')}
                </span>
            </td>
            <td style="padding: 14px 18px;">
                <span style="display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 0.78rem; font-weight: 600; background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe;">
                    ${escapeHtml(c.visa_status || 'C2C Eligible')}
                </span>
            </td>
            <td style="padding: 14px 18px; font-weight: 600; color: #334155;">
                ${c.experience_years || 5}+ Yrs
            </td>
            <td style="padding: 14px 18px; color: #64748b; font-size: 0.85rem;">
                ${escapeHtml(c.location || 'United States')}
            </td>
            <td style="padding: 14px 18px;">
                <span style="font-weight: 600; color: #0284c7; font-size: 0.85rem;">
                    👤 ${escapeHtml(c.recruiter_name || 'Assigned')}
                </span>
            </td>
            <td style="padding: 14px 18px;">
                ${hasResume ? `
                    <span style="display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0;">
                        📄 .DOCX Ready
                    </span>
                ` : `
                    <span style="display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; background: #fffbeb; color: #b45309; border: 1px solid #fde68a;">
                        ⚠️ No Resume
                    </span>
                `}
            </td>
            <td style="padding: 14px 18px;">
                ${gmailConnected ? `
                    <span style="display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem; font-weight: 600; color: #059669;">
                        <span style="width: 7px; height: 7px; border-radius: 50%; background: #059669;"></span> Connected
                    </span>
                ` : `
                    <a href="/auth/gmail/login?candidate_id=${c.id}" style="display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; background: #eff6ff; color: #2563eb; border: 1px solid #bfdbfe; text-decoration: none;">
                        Connect Gmail
                    </a>
                `}
            </td>
            <td style="padding: 14px 18px; text-align: right;">
                <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px;">
                    <button class="btn btn-sm" onclick="openAiCopilotModal(${c.id})" style="background: linear-gradient(135deg, #2563eb, #38bdf8); color: #ffffff; border: none; font-weight: 600; font-size: 0.75rem; padding: 5px 12px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px;" title="Open AI Personalization Chatbot">
                        <span>🤖 AI Outreach</span>
                    </button>
                    <button class="btn btn-secondary btn-sm btn-edit-consultant" data-id="${c.id}" style="padding: 5px 10px; font-size: 0.75rem;" title="Edit Profile">
                        ✏️
                    </button>
                    <button class="btn btn-secondary btn-sm btn-delete-consultant" data-id="${c.id}" data-name="${escapeHtml(c.name)}" style="padding: 5px 10px; font-size: 0.75rem; color: #ef4444;" title="Delete Candidate">
                        🗑️
                    </button>
                </div>
            </td>
        </tr>`;
    }).join('');

    // Attach row events
    tbody.querySelectorAll('.btn-edit-consultant').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const c = state.consultants.find(item => item.id === candId);
            if (c) openEditConsultantModal(c);
        });
    });

    tbody.querySelectorAll('.btn-delete-consultant').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const candName = btn.getAttribute('data-name');
            deleteConsultant(candId, candName);
        });
    });
}

// =========================================================================
// Dashboard Candidate Pipeline Table View
// =========================================================================
function renderDashboardPipeline() {
    const tbody = document.getElementById('dashboard-pipeline-tbody');
    if (!tbody) return;

    const cands = state.consultants || [];
    if (cands.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 30px; color: #64748b;">No candidate applications found yet.</td></tr>`;
        return;
    }

    const stages = [
        { stage: "Tech Interview", color: "#2563eb", bg: "#eff6ff", border: "#bfdbfe", status: "Active" },
        { stage: "Offer Sent", color: "#d97706", bg: "#fffbeb", border: "#fde68a", status: "Offer" },
        { stage: "Sourcing", color: "#059669", bg: "#ecfdf5", border: "#a7f3d0", status: "Sourcing" },
        { stage: "Client Review", color: "#7c3aed", bg: "#f5f3ff", border: "#ddd6fe", status: "Active" },
        { stage: "Tech Interview", color: "#2563eb", bg: "#eff6ff", border: "#bfdbfe", status: "Active" }
    ];

    const targetRoles = [
        "Applied for Senior Cloud / AWS Developer",
        "Lead Full-Stack Java Engineer (Remote)",
        "Principal AI & Automation Architect",
        "Senior DevOps / Kubernetes Engineer",
        "Data Platform & Snowflake Specialist"
    ];

    const dates = ["09/20/26", "09/19/26", "09/18/26", "09/16/26", "09/15/26"];

    tbody.innerHTML = cands.slice(0, 8).map((c, idx) => {
        const initials = (c.name || "C").split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
        const stageInfo = stages[idx % stages.length];
        const roleStr = targetRoles[idx % targetRoles.length];
        const dateStr = dates[idx % dates.length];
        const recruiterName = c.recruiter_name || "Praveen Valipireddy";

        return `
        <tr style="border-bottom: 1px solid #f1f5f9; transition: background 0.15s ease;" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='transparent'">
            <td style="padding: 14px 20px;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <div style="width: 36px; height: 36px; border-radius: 50%; background: #f1f5f9; color: #1e293b; font-weight: 700; font-size: 0.85rem; display: flex; align-items: center; justify-content: center; border: 1px solid #cbd5e1; flex-shrink: 0;">
                        ${initials}
                    </div>
                    <div>
                        <div style="font-weight: 700; color: #0f172a;">${escapeHtml(c.name)}</div>
                        <div style="font-size: 0.78rem; color: #64748b; margin-top: 2px;">${escapeHtml(c.title || 'Technical Consultant')}</div>
                    </div>
                </div>
            </td>
            <td style="padding: 14px 20px; font-weight: 600; color: #334155; font-size: 0.85rem;">
                ${escapeHtml(roleStr)}
            </td>
            <td style="padding: 14px 20px;">
                <span style="display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; background: ${stageInfo.bg}; color: ${stageInfo.color}; border: 1px solid ${stageInfo.border};">
                    ${stageInfo.stage}
                </span>
            </td>
            <td style="padding: 14px 20px; color: #64748b; font-size: 0.85rem;">
                ${dateStr}
            </td>
            <td style="padding: 14px 20px; font-size: 0.85rem; color: #0f172a; font-weight: 600;">
                ${escapeHtml(recruiterName)}
            </td>
            <td style="padding: 14px 20px;">
                <span style="display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 0.75rem; font-weight: 700; background: ${stageInfo.bg}; color: ${stageInfo.color}; border: 1px solid ${stageInfo.border};">
                    ${stageInfo.status}
                </span>
            </td>
        </tr>`;
    }).join('');
}

// =========================================================================
// Recruiter AI Outreach Personalization Chatbot
// =========================================================================
let currentCopilotCandId = null;
let currentCopilotJobId = null;

async function openAiCopilotModal(candId, jobId = null) {
    currentCopilotCandId = candId || (state.consultants && state.consultants.length > 0 ? state.consultants[0].id : 1);
    
    if (!jobId && state.jobs && state.jobs.length > 0) {
        currentCopilotJobId = state.jobs[0].id;
    } else {
        currentCopilotJobId = jobId || 1;
    }

    const cand = (state.consultants || []).find(c => c.id === currentCopilotCandId) || {};
    const job = (state.jobs || []).find(j => j.id === currentCopilotJobId) || {};

    const modal = document.getElementById('modal-ai-copilot');
    if (!modal) return;

    const subEl = document.getElementById('copilot-context-subtitle');
    if (subEl) {
        subEl.innerText = `Personalizing pitch for ${cand.name || 'Candidate'} → ${job.title || 'Technical Role'} (${job.company || 'Hiring Team'})`;
    }
    const toEmailEl = document.getElementById('copilot-to-email');
    if (toEmailEl) toEmailEl.value = job.recruiter_email || '';
    const resNameEl = document.getElementById('copilot-resume-name');
    if (resNameEl) resNameEl.innerText = cand.resume_filename || 'Candidate_Resume.docx';

    const msgContainer = document.getElementById('copilot-chat-messages');
    if (msgContainer) {
        msgContainer.innerHTML = `
            <div style="display: flex; gap: 10px; align-items: flex-start;">
                <div style="width: 28px; height: 28px; border-radius: 50%; background: #eff6ff; color: #2563eb; display: flex; align-items: center; justify-content: center; font-size: 0.9rem; flex-shrink: 0;">🤖</div>
                <div style="background: #f1f5f9; color: #1e293b; padding: 10px 14px; border-radius: 12px; font-size: 0.85rem; max-width: 88%; line-height: 1.5;">
                    Hello! I am your <strong>Recruiter AI Outreach Copilot</strong>. I've prepared a baseline pitch for <strong>${escapeHtml(cand.name || 'Candidate')}</strong>. Tell me how you'd like to personalize it—e.g. highlight specific skills, adjust rate, change tone, or add immediate interview availability!
                </div>
            </div>
        `;
    }

    try {
        const res = await fetch('/api/ai/personalize-draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                candidate_id: currentCopilotCandId,
                job_id: currentCopilotJobId,
                instruction: '',
                current_subject: '',
                current_body: ''
            })
        });
        const data = await res.json();
        if (data.success) {
            const subjEl = document.getElementById('copilot-subject');
            if (subjEl) subjEl.value = data.subject || '';
            const bodyEl = document.getElementById('copilot-body');
            if (bodyEl) bodyEl.value = data.body || '';
            if (data.to_email && toEmailEl && !toEmailEl.value) toEmailEl.value = data.to_email;
        }
    } catch (e) {
        console.error('Error fetching baseline pitch:', e);
    }

    modal.style.display = 'flex';
    const input = document.getElementById('copilot-user-input');
    if (input) input.focus();
}

function closeAiCopilotModal() {
    const modal = document.getElementById('modal-ai-copilot');
    if (modal) modal.style.display = 'none';
}

function applyCopilotQuickPrompt(promptText) {
    const input = document.getElementById('copilot-user-input');
    if (input) {
        input.value = promptText;
        sendCopilotInstruction();
    }
}

async function sendCopilotInstruction() {
    const input = document.getElementById('copilot-user-input');
    const instruction = (input.value || '').trim();
    if (!instruction) return;

    input.value = '';
    const msgContainer = document.getElementById('copilot-chat-messages');
    if (msgContainer) {
        msgContainer.innerHTML += `
            <div style="display: flex; gap: 10px; align-items: flex-start; justify-content: flex-end;">
                <div style="background: #2563eb; color: #ffffff; padding: 10px 14px; border-radius: 12px; font-size: 0.85rem; max-width: 85%; line-height: 1.5;">
                    ${escapeHtml(instruction)}
                </div>
                <div style="width: 28px; height: 28px; border-radius: 50%; background: #e2e8f0; color: #475569; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 700; flex-shrink: 0;">YOU</div>
            </div>
        `;
        msgContainer.scrollTop = msgContainer.scrollHeight;
    }

    const sendBtn = document.getElementById('btn-copilot-send');
    if (sendBtn) {
        sendBtn.disabled = true;
        sendBtn.innerText = 'Thinking...';
    }

    try {
        const curSubject = document.getElementById('copilot-subject') ? document.getElementById('copilot-subject').value : '';
        const curBody = document.getElementById('copilot-body') ? document.getElementById('copilot-body').value : '';

        const res = await fetch('/api/ai/personalize-draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                candidate_id: currentCopilotCandId,
                job_id: currentCopilotJobId,
                instruction: instruction,
                current_subject: curSubject,
                current_body: curBody
            })
        });

        const data = await res.json();
        if (data.success) {
            const subjEl = document.getElementById('copilot-subject');
            if (subjEl) subjEl.value = data.subject || curSubject;
            const bodyEl = document.getElementById('copilot-body');
            if (bodyEl) bodyEl.value = data.body || curBody;

            if (msgContainer) {
                msgContainer.innerHTML += `
                    <div style="display: flex; gap: 10px; align-items: flex-start;">
                        <div style="width: 28px; height: 28px; border-radius: 50%; background: #eff6ff; color: #2563eb; display: flex; align-items: center; justify-content: center; font-size: 0.9rem; flex-shrink: 0;">🤖</div>
                        <div style="background: #f1f5f9; color: #1e293b; padding: 10px 14px; border-radius: 12px; font-size: 0.85rem; max-width: 88%; line-height: 1.5;">
                            ${escapeHtml(data.reply || 'Updated draft according to your instructions.')}
                        </div>
                    </div>
                `;
                msgContainer.scrollTop = msgContainer.scrollHeight;
            }
        } else {
            showToast(data.error || 'Failed to personalize pitch', 'error');
        }
    } catch (e) {
        showToast('Error communicating with AI assistant: ' + e.message, 'error');
    } finally {
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.innerText = 'Send';
        }
    }
}

async function submitCopilotDraftToGmail() {
    const toEmailEl = document.getElementById('copilot-to-email');
    const toEmail = toEmailEl ? toEmailEl.value.trim() : '';
    const subjectEl = document.getElementById('copilot-subject');
    const subject = subjectEl ? subjectEl.value.trim() : '';
    const bodyEl = document.getElementById('copilot-body');
    const body = bodyEl ? bodyEl.value.trim() : '';
    const saveBtn = document.getElementById('btn-copilot-save-draft');

    if (!toEmail || !toEmail.includes('@')) {
        showToast('Please enter a valid recipient recruiter email', 'warning');
        if (toEmailEl) toEmailEl.focus();
        return;
    }

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerText = 'Saving to Gmail...';
    }

    try {
        const res = await fetch('/api/outreach/create-draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                candidate_id: currentCopilotCandId,
                job_id: currentCopilotJobId,
                custom_to_email: toEmail,
                custom_subject: subject,
                custom_body: body
            })
        });

        const data = await res.json();
        if (data.success) {
            showToast(`✉️ Personalized Gmail Draft successfully saved for ${data.consultant_name}!`, 'success', 6000);
            closeAiCopilotModal();
            const statDrafts = document.getElementById('stat-drafted-count');
            if (statDrafts) statDrafts.innerText = (parseInt(statDrafts.innerText) || 0) + 1;
            const statKpiDrafts = document.getElementById('stat-kpi-drafts');
            if (statKpiDrafts) statKpiDrafts.innerText = (parseInt(statKpiDrafts.innerText) || 0) + 1;
        } else {
            showToast(data.error || 'Failed to save draft in Gmail', 'error', 6000);
        }
    } catch (e) {
        showToast('Error creating draft: ' + e.message, 'error');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerText = '🚀 Save to Gmail Draft';
        }
    }
}
