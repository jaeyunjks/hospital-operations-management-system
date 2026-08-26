/*
 * Staff & Shift Management — frontend behaviour.
 *
 * Progressive enhancement over the static markup: the page renders without
 * JavaScript, and this script populates it from the backend/API microservice
 * when that service is reachable.
 *
 * Only endpoints that exist in student-5/backend are called. No endpoint is
 * invented here.
 */
(function () {
  'use strict';

  // Backend base URL. Same-origin when served by Flask; falls back to the
  // local development port when the page is opened directly from disk.
  var API = (window.location.protocol === 'file:')
    ? 'http://127.0.0.1:5050'
    : '';

  function el(id) { return document.getElementById(id); }

  function getJSON(path, options) {
    return fetch(API + path, options).then(function (response) {
      if (!response.ok) { throw new Error(path + ' returned ' + response.status); }
      return response.json();
    });
  }

  function text(node, value) { if (node) { node.textContent = value; } }

  /* ------------------------------------------------------------- status */
  function setServiceStatus(ok, message) {
    var wrap = el('service-status');
    if (!wrap) { return; }
    wrap.className = 'status ' + (ok ? 'status--success' : 'status--danger');
    text(el('service-status-text'), message);
  }

  /* ----------------------------------------------------------- coverage */
  function coverageVariant(status) {
    if (status === 'Fully staffed') { return 'success'; }
    if (status === 'Unstaffed') { return 'danger'; }
    if (status === 'Understaffed') { return 'warning'; }
    return 'neutral';
  }

  function renderCoverage(data) {
    var summary = data.summary || {};
    text(el('kpi-total'), summary.total_shifts != null ? summary.total_shifts : '—');
    text(el('kpi-filled'), summary.fully_staffed != null ? summary.fully_staffed : '—');
    text(el('kpi-under'), summary.understaffed != null ? summary.understaffed : '—');
    text(el('kpi-gap'), summary.total_shortfall != null ? summary.total_shortfall : '—');

    var alertBox = el('coverage-alert');
    if (alertBox) {
      if (summary.total_shortfall > 0) {
        text(el('coverage-alert-text'),
          summary.understaffed + ' of ' + summary.total_shifts +
          ' shifts are short by ' + summary.total_shortfall + ' staff member(s) in total.');
        alertBox.hidden = false;
      } else {
        alertBox.hidden = true;
      }
    }

    var shifts = data.shifts || [];
    text(el('shift-count'), shifts.length + ' shifts');

    var body = el('shift-rows');
    if (!body) { return; }

    if (!shifts.length) {
      body.innerHTML = '<tr><td colspan="6"><div class="empty">' +
        '<p class="empty__title">No shifts scheduled</p>' +
        '<p class="empty__body">Shifts added to the roster will appear here.</p>' +
        '</div></td></tr>';
      return;
    }

    body.innerHTML = shifts.map(function (row) {
      var variant = coverageVariant(row.coverage_status);
      var pct = row.required_staff_count
        ? Math.min(100, Math.round((row.assigned_staff_count / row.required_staff_count) * 100))
        : 0;
      return '<tr>' +
        '<td class="table__name">' + escapeHTML(row.department) + '</td>' +
        '<td class="table__id">' + escapeHTML(row.shift_date) + '</td>' +
        '<td><span class="shift-time">' + escapeHTML(row.start_time) + '–' +
            escapeHTML(row.end_time) + '</span></td>' +
        '<td class="table__muted">' + escapeHTML(row.required_role) + '</td>' +
        '<td><div class="coverage-cell">' +
            '<span class="coverage-cell__figure">' + row.assigned_staff_count + ' / ' +
            row.required_staff_count + '</span>' +
            '<div class="meter meter--' + variant + '">' +
              '<div class="meter__fill" style="width:' + pct + '%"></div>' +
            '</div>' +
          '</div></td>' +
        '<td><span class="badge-' + variant + '">' +
            escapeHTML(row.coverage_status) + '</span></td>' +
        '</tr>';
    }).join('');
  }

  /* -------------------------------------------------------------- staff */
  function availabilityVariant(status) {
    if (status === 'Available') { return 'success'; }
    if (status === 'Unavailable') { return 'danger'; }
    return 'neutral';
  }

  function renderStaff(records) {
    text(el('staff-count'), records.length + ' records');
    var body = el('staff-rows');
    if (!body) { return; }

    if (!records.length) {
      body.innerHTML = '<tr><td colspan="6"><div class="empty">' +
        '<p class="empty__title">No staff match these filters</p>' +
        '<p class="empty__body">Adjust the search or availability filter.</p>' +
        '</div></td></tr>';
      return;
    }

    body.innerHTML = records.map(function (person) {
      return '<tr>' +
        '<td class="table__id">S-' + String(person.staff_id).padStart(3, '0') + '</td>' +
        '<td class="table__name">' + escapeHTML(person.name) + '</td>' +
        '<td>' + escapeHTML(person.role) + '</td>' +
        '<td class="table__muted">' + escapeHTML(person.department) + '</td>' +
        '<td><span class="badge-' + availabilityVariant(person.availability_status) + '">' +
            escapeHTML(person.availability_status) + '</span></td>' +
        '<td class="table__muted">' + escapeHTML(person.employment_status) + '</td>' +
        '</tr>';
    }).join('');
  }

  /* ---------------------------------------------------- recommendations */
  function renderRecommendations(data) {
    var list = el('recommendation-list');
    if (!list) { return; }

    var suggestions = data.suggestions || [];
    if (!suggestions.length) {
      list.innerHTML = '<div class="empty">' +
        '<p class="empty__title">No candidates found</p>' +
        '<p class="empty__body">No available staff match this shift.</p></div>';
      return;
    }

    var maxScore = suggestions[0].score || 1;
    list.innerHTML = suggestions.map(function (item, index) {
      var confidence = Math.round((item.score / maxScore) * 100);
      return '<div class="recommendation">' +
        '<span class="recommendation__rank">' + (index + 1) + '</span>' +
        '<div class="recommendation__main">' +
          '<div class="recommendation__name">' + escapeHTML(item.name) + '</div>' +
          '<p class="ai-basis">' + escapeHTML((item.reasons || []).join(' · ')) + '</p>' +
          '<div class="confidence">' +
            '<span class="confidence__label"><span>Confidence</span><span>' +
              confidence + '%</span></span>' +
            '<div class="confidence__track">' +
              '<div class="confidence__fill" style="width:' + confidence + '%"></div>' +
            '</div>' +
          '</div>' +
        '</div>' +
        '<div class="ai-decision">' +
          '<button class="btn-secondary btn-compact" type="button">Approve</button>' +
          '<button class="btn-ghost btn-compact" type="button">Dismiss</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function escapeHTML(value) {
    if (value == null) { return ''; }
    return String(value).replace(/[&<>"']/g, function (character) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
                '"': '&quot;', "'": '&#39;' })[character];
    });
  }

  /* --------------------------------------------------------------- load */
  function loadCoverage() {
    return getJSON('/api/shifts/coverage')
      .then(renderCoverage)
      .then(function () { setServiceStatus(true, 'Service online'); });
  }

  function loadStaff() {
    var term = (el('staff-search') || {}).value || '';
    var availability = (el('availability-filter') || {}).value || '';
    var query = [];
    if (term) { query.push('q=' + encodeURIComponent(term)); }
    if (availability) { query.push('availability_status=' + encodeURIComponent(availability)); }

    var path = term
      ? '/api/staff/search' + (query.length ? '?' + query.join('&') : '')
      : '/api/staff' + (availability ? '?availability_status=' + encodeURIComponent(availability) : '');

    return getJSON(path).then(function (data) { renderStaff(data.staff || []); });
  }

  function loadRecommendations() {
    // Suggest against the first shift with a shortfall, using the real
    // POST /api/shifts/suggest-staff endpoint.
    return getJSON('/api/shifts/coverage').then(function (coverage) {
      var gap = (coverage.shifts || []).filter(function (row) { return row.shortfall > 0; })[0];
      if (!gap) {
        var list = el('recommendation-list');
        if (list) {
          list.innerHTML = '<div class="empty">' +
            '<p class="empty__title">No staffing gaps</p>' +
            '<p class="empty__body">Every shift currently meets its requirement.</p></div>';
        }
        return null;
      }
      return getJSON('/api/shifts/suggest-staff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shift_id: gap.shift_id, limit: 5 })
      }).then(renderRecommendations);
    });
  }

  function loadSummary() {
    return getJSON('/api/shifts/coverage-summary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    }).then(function (data) { text(el('summary-text'), data.headline || ''); });
  }

  function handleFailure(error) {
    setServiceStatus(false, 'Service unavailable');
    var body = el('shift-rows');
    if (body) {
      body.innerHTML = '<tr><td colspan="6"><div class="empty">' +
        '<p class="empty__title">Cannot reach the backend service</p>' +
        '<p class="empty__body">Start the Staff &amp; Shift API, then refresh.</p>' +
        '</div></td></tr>';
    }
    var staffBody = el('staff-rows');
    if (staffBody) {
      staffBody.innerHTML = '<tr><td colspan="6"><div class="empty">' +
        '<p class="empty__title">Cannot reach the backend service</p>' +
        '<p class="empty__body">Start the Staff &amp; Shift API, then refresh.</p>' +
        '</div></td></tr>';
    }
    if (window.console) { console.warn('Staff & Shift API unavailable:', error.message); }
  }

  function refresh() {
    Promise.all([loadCoverage(), loadStaff(), loadSummary()]).catch(handleFailure);
  }

  /* ------------------------------------------------------------- events */
  document.addEventListener('DOMContentLoaded', function () {
    refresh();

    var refreshBtn = el('refresh-btn');
    if (refreshBtn) { refreshBtn.addEventListener('click', refresh); }

    var suggestBtn = el('suggest-btn');
    if (suggestBtn) {
      suggestBtn.addEventListener('click', function () {
        loadRecommendations().catch(handleFailure);
      });
    }

    var search = el('staff-search');
    if (search) {
      var timer;
      search.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(function () { loadStaff().catch(handleFailure); }, 250);
      });
    }

    var availability = el('availability-filter');
    if (availability) {
      availability.addEventListener('change', function () {
        loadStaff().catch(handleFailure);
      });
    }

    var clear = el('clear-filters');
    if (clear) {
      clear.addEventListener('click', function () {
        if (search) { search.value = ''; }
        if (availability) { availability.value = ''; }
        loadStaff().catch(handleFailure);
      });
    }
  });
})();
