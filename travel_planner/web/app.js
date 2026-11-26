/* Travel Planner front end. Plain JavaScript, no build step, no external requests.
   All text from the server (including model output) is inserted with textContent,
   never innerHTML, so it cannot inject markup. */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);

  /** Create an element: h('div', {class: 'x', onclick: fn}, 'text', child, ...). */
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const INTERESTS = ['Beaches', 'Food', 'History and culture', 'Nature', 'Adventure', 'Nightlife', 'Shopping', 'Relaxing', 'Photography', 'Family friendly'];
  const DEFAULT_BUDGET = { INR: 60000, USD: 1500, EUR: 1400, GBP: 1200, AED: 5500, SGD: 2000, THB: 50000, JPY: 220000, AUD: 2200, CAD: 2000 };
  const BUDGET_COLORS = { Flights: '#2f6fb0', Accommodation: '#8a5cc2', 'Local transport': '#2c9aa6', Food: '#d9822b', Activities: '#3f9d5a' };
  const MARKS = { pending: '○', running: '●', done: '✓', fallback: '!' };

  const form = $('#trip-form');
  const els = {
    status: $('#status'), statusText: $('#status-text'), error: $('#form-error'), submit: $('#submit'),
    board: $('#board'), boardRows: $('#board-rows'), boardFoot: $('#board-foot'), boardTitle: $('#board-title'), cancel: $('#cancel-btn'),
    empty: $('#empty'), plan: $('#plan'), main: $('#main'),
    history: $('#history'), historyBtn: $('#history-btn'), historyList: $('#history-list'), historyEmpty: $('#history-empty'),
    chips: $('#chips'), customInterest: $('#custom-interest'), models: $('#model-list'),
  };

  const state = { id: null, timer: null, started: 0, failures: 0, budgetTouched: false, interests: new Set() };

  // ------------------------------------------------------------------ helpers
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: ignore */ } },
  };

  async function api(path, options) {
    let res;
    try {
      res = await fetch(path, options);
    } catch {
      throw new Error('Cannot reach the Travel Planner server. Is it still running?');
    }
    if (res.status === 204) return null;
    let body = null;
    try { body = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) throw new Error(describeError(body, res.status));
    return body;
  }

  function describeError(body, status) {
    const detail = body && body.detail;
    if (Array.isArray(detail)) {
      const names = { budget: 'Budget', travelers: 'Travellers', days: 'Days', currency: 'Currency', end_date: 'End date', start_date: 'Start date', destination: 'To', origin: 'From' };
      return detail.map((d) => {
        const field = d.loc && d.loc[d.loc.length - 1];
        const msg = String(d.msg || 'is not valid').replace(/^Value error, /, '');
        return names[field] ? `${names[field]}: ${msg}` : msg;
      }).join(' ');
    }
    if (typeof detail === 'string') return detail;
    return `The server returned an error (${status}).`;
  }

  function money(n, cur) {
    const value = Number(n) || 0;
    try {
      return new Intl.NumberFormat(cur === 'INR' ? 'en-IN' : undefined, { style: 'currency', currency: cur, maximumFractionDigits: 0 }).format(value);
    } catch {
      return `${Math.round(value).toLocaleString()} ${cur}`;
    }
  }

  function showError(message) {
    els.error.textContent = message || '';
    els.error.hidden = !message;
  }

  // ------------------------------------------------------------------ status chip
  async function loadHealth(refresh = false) {
    els.statusText.textContent = 'Checking model...';
    els.status.className = 'status';
    try {
      const data = await api(`/api/health${refresh ? '?refresh=true' : ''}`);
      const llm = data.llm;
      els.models.replaceChildren(...llm.models.map((m) => h('option', { value: m })));
      if (!llm.enabled) {
        setStatus('off', 'Offline estimates only', 'The language model is switched off (LLM_ENABLED=false).');
      } else if (!llm.ok) {
        setStatus('off', 'No model: offline estimates', llm.error || 'The model server is not reachable.');
      } else if (/^(localhost|127\.|\[::1\])/.test(llm.host) && llm.models.length && !llm.models.includes(llm.model)) {
        setStatus('bad', `Model "${llm.model}" not installed`, `The server is up but does not list ${llm.model}. With Ollama run: ollama pull ${llm.model}`);
      } else {
        setStatus('ok', `Model ready: ${llm.model}`, `Connected to ${llm.host}`);
      }
    } catch (err) {
      setStatus('bad', 'Server unreachable', err.message);
    }
  }

  function setStatus(kind, text, title) {
    els.status.className = `status ${kind}`;
    els.statusText.textContent = text;
    els.status.title = `${title}\nClick to check again.`;
  }

  // ------------------------------------------------------------------ form
  function renderChips() {
    els.chips.replaceChildren(...[...INTERESTS, ...[...state.interests].filter((i) => !INTERESTS.includes(i))].map((name) => {
      const chip = h('button', { type: 'button', class: 'chip', 'aria-pressed': state.interests.has(name) ? 'true' : 'false' }, name);
      chip.addEventListener('click', () => {
        if (state.interests.has(name)) state.interests.delete(name); else state.interests.add(name);
        renderChips();
      });
      return chip;
    }));
  }

  els.customInterest.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const value = els.customInterest.value.trim().slice(0, 40);
    if (value) { state.interests.add(value); els.customInterest.value = ''; renderChips(); }
  });

  function syncDates() {
    const start = form.start_date.value, end = form.end_date.value;
    const both = Boolean(start && end);
    form.days.disabled = both;
    form.end_date.min = start || '';
    if (both) {
      const days = Math.round((new Date(end) - new Date(start)) / 86400000) + 1;
      if (days >= 1) form.days.value = days;
    }
  }
  form.start_date.addEventListener('input', syncDates);
  form.end_date.addEventListener('input', syncDates);
  form.budget.addEventListener('input', () => { state.budgetTouched = true; });
  form.currency.addEventListener('change', () => {
    if (!state.budgetTouched && DEFAULT_BUDGET[form.currency.value]) form.budget.value = DEFAULT_BUDGET[form.currency.value];
  });

  function readPayload() {
    const start = form.start_date.value, end = form.end_date.value;
    const payload = {
      origin: form.origin.value.trim(),
      destination: form.destination.value.trim(),
      travelers: Number(form.travelers.value),
      budget: Number(form.budget.value),
      currency: form.currency.value,
      style: form.style.value,
      interests: [...state.interests],
      notes: form.notes.value.trim(),
      include_flights: form.include_flights.checked,
      model: form.model.value.trim() || null,
    };
    if (start && end) { payload.start_date = start; payload.end_date = end; }
    else { if (start) payload.start_date = start; payload.days = Number(form.days.value) || 5; }
    return payload;
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    showError('');
    const payload = readPayload();
    if (!(payload.budget > 0)) return showError('Budget: enter an amount greater than zero.');
    if (payload.end_date && payload.start_date && payload.end_date < payload.start_date) return showError('End date: choose a day on or after the start date.');
    store.set('tp.form', JSON.stringify({ origin: payload.origin, currency: payload.currency, travelers: payload.travelers, style: payload.style, budget: payload.budget }));

    els.submit.disabled = true;
    els.submit.textContent = 'Planning...';
    try {
      const { id } = await api('/api/plans', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      openPlan(id);
    } catch (err) {
      showError(err.message);
    } finally {
      els.submit.disabled = false;
      els.submit.textContent = 'Plan my trip';
    }
  });

  // ------------------------------------------------------------------ progress board
  function openPlan(id) {
    stopPolling();
    state.id = id;
    state.started = Date.now();
    state.failures = 0;
    history.replaceState(null, '', `#plan=${id}`);
    els.empty.hidden = true;
    els.plan.hidden = true;
    els.plan.replaceChildren();
    poll();
  }

  function stopPolling() {
    if (state.timer) { clearTimeout(state.timer); state.timer = null; }
  }

  async function poll() {
    const id = state.id;
    try {
      const data = await api(`/api/plans/${id}`);
      if (id !== state.id) return;
      state.failures = 0;
      renderBoard(data);
      if (data.status === 'done' && data.result) {
        els.board.hidden = true;
        renderPlan(data.result);
        loadHistory();
        els.main.focus({ preventScroll: true });
        return;
      }
      if (data.status === 'failed' || data.status === 'cancelled') {
        els.cancel.hidden = true;
        showBoardFoot(data.error || 'The plan did not finish.', true);
        loadHistory();
        return;
      }
    } catch (err) {
      if (id !== state.id) return;
      state.failures += 1;
      if (state.failures >= 5) { showBoardFoot(err.message, true); return; }
    }
    state.timer = setTimeout(poll, 1200);
  }

  function renderBoard(data) {
    els.board.hidden = false;
    els.boardTitle.textContent = 'Departures';
    els.cancel.hidden = !(data.status === 'queued' || data.status === 'running');
    els.boardRows.replaceChildren(...Object.entries(data.progress || {}).map(([, step]) => {
      const msg = step.status === 'pending' ? 'Waiting' : step.message || (step.status === 'done' ? 'Done' : '');
      return h('li', { class: 'board-row', 'data-status': step.status },
        h('span', { class: 'mark', 'aria-hidden': 'true' }, MARKS[step.status] || '○'),
        h('span', { class: 'label' }, step.label),
        h('span', { class: 'msg' }, msg));
    }));
    const secs = Math.round((Date.now() - state.started) / 1000);
    showBoardFoot(data.status === 'queued' ? 'Waiting for a free slot...' : `${secs}s elapsed. Small local models can take a few minutes, so keep this tab open.`, false);
  }

  function showBoardFoot(text, isError) {
    els.board.hidden = false;
    els.boardFoot.textContent = text;
    els.boardFoot.style.color = isError ? 'var(--sign)' : '';
  }

  els.cancel.addEventListener('click', async () => {
    if (!state.id) return;
    els.cancel.disabled = true;
    try { await api(`/api/plans/${state.id}/cancel`, { method: 'POST' }); } catch { /* the poll will show the state */ }
    els.cancel.disabled = false;
  });

  // ------------------------------------------------------------------ plan view
  const sourceTag = (source) => h('span', { class: `tag ${source === 'ai' ? 'ai' : ''}` }, source === 'ai' ? 'Model suggestion' : 'Offline estimate');

  function section(title, source, ...children) {
    return h('section', { class: 'plan-section' }, h('h3', {}, title, source ? sourceTag(source) : null), ...children);
  }

  function renderPlan(plan) {
    const req = plan.request, cur = req.currency, d = plan.destination;
    const when = req.start_date && req.end_date ? `${req.start_date} to ${req.end_date}` : `${req.days} days`;
    const root = els.plan;
    root.replaceChildren();

    root.append(h('div', { class: 'plan-head' },
      h('div', {},
        h('h2', {}, d.name || 'Your trip', d.country ? `, ${d.country}` : ''),
        h('p', { class: 'plan-sub' }, `${when}, ${req.travelers} ${req.travelers === 1 ? 'traveller' : 'travellers'}, ${req.style} style${req.origin ? `, from ${req.origin}` : ''}`)),
      h('div', { class: 'actions' },
        h('a', { class: 'btn', href: `/api/plans/${plan.id}/export?format=md`, download: '' }, 'Download Markdown'),
        h('a', { class: 'btn', href: `/api/plans/${plan.id}/export?format=json`, download: '' }, 'Download JSON'),
        h('button', { class: 'btn', type: 'button', onclick: () => window.print() }, 'Print'),
        h('button', { class: 'btn', type: 'button', onclick: newPlan }, 'New plan'))));

    if (plan.mode === 'estimate') {
      root.append(h('div', { class: 'notice' }, h('strong', {}, 'Offline estimate. '), 'No language model answered, so the amounts below are a suggested split of your budget, not real prices. Start your model (or check the status at the top) and plan again for a detailed trip.'));
    } else if (plan.mode === 'mixed') {
      root.append(h('div', { class: 'notice' }, h('strong', {}, 'Partly estimated. '), 'Some sections came from the model and others are offline estimates; each section says which.'));
    }
    for (const w of plan.warnings || []) root.append(h('div', { class: 'notice' }, w));

    // overview
    const facts = [d.best_time && ['Best time', d.best_time], d.language && ['Language', d.language], d.currency_note && ['Money', d.currency_note]].filter(Boolean);
    root.append(section('Overview', d.source,
      d.description ? h('p', { class: 'lead' }, d.description) : null,
      facts.map(([k, v]) => h('p', { class: 'kv' }, h('b', {}, `${k}: `), v)),
      h('div', { class: 'cols2' },
        d.highlights.length ? h('div', {}, h('b', {}, 'Highlights'), h('ul', { class: 'plain' }, d.highlights.map((x) => h('li', {}, x)))) : null,
        d.local_tips.length ? h('div', {}, h('b', {}, 'Local tips'), h('ul', { class: 'plain' }, d.local_tips.map((x) => h('li', {}, x)))) : null)));

    // itinerary
    root.append(section('Day by day', plan.itinerary.source,
      h('div', { class: 'days' }, plan.itinerary.days.map((day) => h('div', { class: 'day' },
        h('div', { class: 'day-tag' }, h('span', {}, 'Day'), h('b', {}, String(day.day)), day.date ? h('span', {}, day.date.slice(5)) : null),
        h('div', { class: 'day-body' },
          h('h4', {}, day.title),
          h('ul', { class: 'items' }, day.items.map((it) => h('li', { class: 'item' },
            h('span', { class: 'time' }, it.time),
            h('span', { class: 'what' }, h('b', {}, it.title), it.description ? h('small', {}, it.description) : null),
            h('span', { class: 'cost' }, it.cost ? money(it.cost, cur) : ''))))))))));

    // flights
    const fl = plan.flights;
    root.append(section('Flights', fl.skipped ? null : fl.source,
      fl.skipped ? h('p', { class: 'muted' }, fl.note) : [
        h('div', { class: 'options' }, fl.options.map((o) => h('div', { class: `option ${o.recommended ? 'rec' : ''}` },
          h('div', {},
            h('div', { class: 'title' }, o.airline || 'Flight', o.recommended ? h('span', { class: 'badge' }, 'Suggested') : null),
            h('div', { class: 'meta' }, [o.origin && o.destination ? `${o.origin} to ${o.destination}` : '', o.depart && o.arrive ? `${o.depart} to ${o.arrive}` : '', o.duration, o.stops ? `${o.stops} ${o.stops === 1 ? 'stop' : 'stops'}` : 'non-stop', o.notes].filter(Boolean).join(', '))),
          h('div', { class: 'price' }, h('b', {}, money(o.total_price, cur)), h('span', { class: 'meta' }, `${money(o.price_per_person, cur)} each, round trip`))))),
        fl.note ? h('p', { class: 'fine' }, fl.note) : null]));

    // lodging
    const lo = plan.lodging;
    root.append(section('Where to stay', lo.source,
      h('p', { class: 'muted' }, `${lo.nights} ${lo.nights === 1 ? 'night' : 'nights'}, ${lo.rooms} ${lo.rooms === 1 ? 'room' : 'rooms'}`),
      h('div', { class: 'options' }, lo.options.map((o) => h('div', { class: `option ${o.recommended ? 'rec' : ''}` },
        h('div', {},
          h('div', { class: 'title' }, o.name, o.recommended ? h('span', { class: 'badge' }, 'Suggested') : null),
          h('div', { class: 'meta' }, [o.kind, o.area, o.rating ? `${o.rating.toFixed(1)} of 5` : '', o.amenities.join(', ')].filter(Boolean).join(', ')),
          o.why ? h('div', { class: 'meta' }, o.why) : null),
        h('div', { class: 'price' }, h('b', {}, money(o.total_price, cur)), h('span', { class: 'meta' }, `${money(o.price_per_night, cur)} per room per night`)))))));

    // transport
    const tr = plan.transport;
    root.append(section('Getting around', tr.source,
      h('div', { class: 'options' }, tr.items.map((i) => h('div', { class: 'option' },
        h('div', {}, h('div', { class: 'title' }, i.mode), h('div', { class: 'meta' }, i.description)),
        h('div', { class: 'price' }, h('b', {}, money(i.cost, cur)))))),
      tr.tips.length ? h('ul', { class: 'plain' }, tr.tips.map((t) => h('li', {}, t))) : null));

    // budget
    const b = plan.budget;
    const colorFor = (name) => BUDGET_COLORS[name] || '#8a98a5';
    root.append(section('Budget', null,
      h('p', { class: `verdict ${b.status}` }, b.status === 'within'
        ? `${money(b.estimated_total, cur)} estimated, ${money(b.remaining, cur)} under your ${money(b.total_budget, cur)} budget`
        : `${money(b.estimated_total, cur)} estimated, ${money(-b.remaining, cur)} over your ${money(b.total_budget, cur)} budget`),
      h('div', { class: 'bar', role: 'img', 'aria-label': 'Budget split: ' + b.breakdown.map((l) => `${l.category} ${l.percent.toFixed(0)}%`).join(', ') },
        b.breakdown.map((l) => h('i', { style: { width: `${l.percent}%`, background: colorFor(l.category) }, title: `${l.category}: ${money(l.amount, cur)}` }))),
      h('table', { class: 'budget' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Category'), h('th', {}, 'Amount'), h('th', {}, 'Share'))),
        h('tbody', {}, b.breakdown.map((l) => h('tr', {}, h('td', {}, h('span', { class: 'swatch', style: { background: colorFor(l.category) } }), l.category), h('td', {}, money(l.amount, cur)), h('td', {}, `${l.percent.toFixed(0)}%`)))),
        h('tfoot', {}, h('tr', {}, h('td', {}, 'Estimated total'), h('td', {}, money(b.estimated_total, cur)), h('td', {}, '100%')))),
      b.tips.length ? h('ul', { class: 'plain', style: { marginTop: '.75rem' } }, b.tips.map((t) => h('li', {}, t))) : null));

    // sources + footer
    const links = (d.sources || []).filter((s) => /^https?:\/\//i.test(s.url));
    if (links.length) {
      root.append(section('Sources', null, h('ul', { class: 'plain sources' }, links.map((s) => h('li', {}, h('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer' }, s.title || s.url))))));
    }
    root.append(h('p', { class: 'fine' }, `Prices are estimates. Check live prices and opening hours before you book.${plan.model ? ` Made with ${plan.model} in ${plan.elapsed_seconds}s.` : ''}`));

    els.empty.hidden = true;
    root.hidden = false;
    root.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  }

  function newPlan() {
    stopPolling();
    state.id = null;
    history.replaceState(null, '', location.pathname);
    els.plan.hidden = true;
    els.board.hidden = true;
    els.empty.hidden = false;
    form.destination.focus();
    window.scrollTo({ top: 0 });
  }

  // ------------------------------------------------------------------ history drawer
  async function loadHistory() {
    try {
      const items = await api('/api/plans?limit=40');
      els.historyEmpty.hidden = items.length > 0;
      els.historyList.replaceChildren(...items.map((p) => h('li', {},
        h('button', { class: 'open', type: 'button', onclick: () => { toggleHistory(false); openPlan(p.id); } },
          h('span', { class: 't' }, p.destination || 'Surprise me'),
          h('span', { class: 's' }, `${p.days || '?'} days, ${money(p.budget, p.currency || 'INR')}, ${p.status}${p.mode === 'estimate' ? ', estimate' : ''}`),
          h('span', { class: 's' }, new Date(p.created_at).toLocaleString())),
        h('button', { class: 'del', type: 'button', 'aria-label': `Delete plan for ${p.destination || 'trip'}`, onclick: async () => {
          try { await api(`/api/plans/${p.id}`, { method: 'DELETE' }); } catch { /* already gone */ }
          if (state.id === p.id) newPlan();
          loadHistory();
        } }, 'Delete'))));
    } catch { /* history is optional */ }
  }

  function toggleHistory(open) {
    const show = open === undefined ? els.history.hidden : open;
    els.history.hidden = !show;
    els.historyBtn.setAttribute('aria-expanded', String(show));
    if (show) loadHistory();
  }
  els.historyBtn.addEventListener('click', () => toggleHistory());
  $('#history-close').addEventListener('click', () => toggleHistory(false));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !els.history.hidden) toggleHistory(false); });
  els.status.addEventListener('click', () => loadHealth(true));

  // ------------------------------------------------------------------ start
  async function init() {
    renderChips();
    try {
      const saved = JSON.parse(store.get('tp.form') || 'null');
      if (saved) {
        if (saved.origin) form.origin.value = saved.origin;
        if (saved.travelers) form.travelers.value = saved.travelers;
        if (saved.style) form.style.value = saved.style;
        if (saved.currency) form.currency.value = saved.currency;
        if (saved.budget) { form.budget.value = saved.budget; state.budgetTouched = true; }
      }
    } catch { /* ignore bad saved data */ }

    api('/api/config').then((cfg) => {
      form.days.max = cfg.max_days;
      if (!store.get('tp.form') && [...form.currency.options].some((o) => o.value === cfg.default_currency)) {
        form.currency.value = cfg.default_currency;
        form.budget.value = DEFAULT_BUDGET[cfg.default_currency] || form.budget.value;
      }
    }).catch(() => {});

    loadHealth();
    const match = location.hash.match(/^#plan=([a-f0-9]+)$/);
    if (match) openPlan(match[1]);
  }
  init();
})();
