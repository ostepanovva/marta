// Марта: мини-приложение. Данные приходят с сервера Марты, правила — те же, что у бота.
'use strict';

const tg = window.Telegram && window.Telegram.WebApp;
const DAY = 24 * 60;
const OFFSETS = [[3 * DAY, 'за 3 дня'], [DAY, 'за день'], [60, 'за час'], [0, 'в момент события']];
const KIND_LABELS = { task: 'Дело', payment: 'Платеж', birthday: 'День рождения', note: 'Заметка', shopping: 'Покупка' };
const TAB_TITLES = { today: 'Сегодня', calendar: 'Календарь', payments: 'Платежи', ideas: 'Идеи', shopping: 'Покупки' };
const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const WEEKDAYS_LONG = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября',
  'октября', 'ноября', 'декабря'];

const state = { tab: 'today', meta: null, month: null, selected: null, ideaFilter: 'Все', sheetBack: null };
const $ = (id) => document.getElementById(id);

// --- мелочи ---

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

let toastTimer = 0;
function toast(text) {
  const el = $('toast');
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3000);
}

function haptic(kind) {
  try {
    if (kind === 'success' || kind === 'error') tg.HapticFeedback.notificationOccurred(kind);
    else tg.HapticFeedback.impactOccurred('light');
  } catch (e) { /* не в Telegram */ }
}

function confirmAsk(text) {
  return new Promise((resolve) => {
    if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast('6.2')) tg.showConfirm(text, resolve);
    else resolve(window.confirm(text));
  });
}

function openLink(url) {
  if (tg && tg.openLink) tg.openLink(url);
  else window.open(url, '_blank', 'noopener');
}

function longDay(iso) {
  const d = new Date(iso + 'T12:00:00');
  return `${WEEKDAYS_LONG[d.getDay()]}, ${d.getDate()} ${MONTHS_GEN[d.getMonth()]}`;
}

function shortHost(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return url; }
}

class ApiError extends Error {}

async function api(path, method = 'GET', data) {
  const options = { method, headers: { Authorization: 'tma ' + (tg ? tg.initData : '') } };
  if (data !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(data);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    throw new ApiError('Нет связи с Мартой, попробуй еще раз');
  }
  let json = {};
  try { json = await response.json(); } catch (e) { /* пустой ответ */ }
  if (response.status === 403) {
    showBlocked('Сессия устарела. Закрой Марту и открой снова.');
    throw new ApiError('Доступа нет');
  }
  if (!response.ok) throw new ApiError(json.error || 'Что-то пошло не так, попробуй еще раз');
  return json;
}

async function act(fn) {
  try {
    return await fn();
  } catch (e) {
    haptic('error');
    toast(e instanceof ApiError ? e.message : 'Что-то пошло не так, попробуй еще раз');
    if (!(e instanceof ApiError)) console.error(e);
    return null;
  }
}

function showBlocked(text) {
  document.querySelector('.top').hidden = true;
  document.querySelector('.tabs').hidden = true;
  $('fab').hidden = true;
  closeSheet();
  $('main').replaceChildren(h('p', { class: 'center', text }));
}

// --- общие куски экрана ---

function section(title, children, opts = {}) {
  return h('section', { class: 'section' }, h('h2', { class: opts.danger ? 'danger' : '', text: title }), children);
}

function card(children, extraClass = '') {
  return h('div', { class: 'card ' + extraClass }, children);
}

function subText(item, opts = {}) {
  const parts = [];
  if (opts.repeat) parts.push('будущий повтор');
  else {
    if (!opts.noWhen && item.when) parts.push(item.when);
    else if (opts.noWhen && item.time) parts.push(item.time);
    if (!opts.noCategory) parts.push(item.category);
    if (item.recurrence !== 'none' && item.kind !== 'birthday') parts.push(item.recurrence_label);
  }
  if (item.amount_label) parts.push(item.amount_label);
  return parts.join(' · ');
}

function itemRow(item, opts = {}) {
  const canDone = !item.repeat && item.status === 'active' && (item.kind === 'task' || item.kind === 'payment');
  const left = canDone
    ? h('button', {
      type: 'button', class: 'check', 'aria-label': (item.kind === 'payment' ? 'Оплачено: ' : 'Готово: ') + item.title,
      onclick: () => markDone(item),
    })
    : h('span', { class: 'spacer', 'aria-hidden': 'true' });
  return h('div', { class: 'row' + (item.repeat ? ' repeat' : '') },
    left,
    h('button', { type: 'button', class: 'row-main', onclick: () => openItem(item) },
      opts.noIcon ? null : h('span', { class: 'emoji', 'aria-hidden': 'true', text: item.icon }),
      h('span', { class: 'lines' },
        h('span', { class: 'title', text: item.title }),
        h('span', { class: 'sub' + (item.overdue ? ' danger' : ''), text: subText(item, opts) }))));
}

function openItem(item) {
  if (item.repeat) toast('Это будущий повтор. Изменить можно ближайшую запись.');
  else if (item.kind === 'shopping') { closeSheet(); switchTab('shopping'); }
  else editSheet(item);
}

function rows(items, opts) {
  return card(items.map((i) => itemRow(i, opts)));
}

async function markDone(item) {
  const result = await act(() => api(`/api/items/${item.id}/done`, 'POST'));
  if (!result) return;
  haptic('success');
  if (result.next) toast(`Готово. Следующий раз — ${result.next}`);
  else toast(item.kind === 'payment' ? 'Отметила: оплачено' : 'Готово, отметила');
  refresh();
}

function draftCard(item, after) {
  const box = h('div', { class: 'draft' },
    h('div', { class: 'draft-head' },
      h('span', { class: 'emoji', 'aria-hidden': 'true', text: item.icon }),
      h('span', { class: 'lines' },
        h('span', { class: 'title', text: item.title }),
        h('span', { class: 'sub', text: [subText(item), item.offsets_label ? 'напомню ' + item.offsets_label : ''].filter(Boolean).join(' · ') }))),
    h('div', { class: 'actions' },
      h('button', {
        type: 'button', class: 'btn primary', text: 'Сохранить',
        onclick: async () => {
          if (await act(() => api(`/api/items/${item.id}/save`, 'POST'))) { haptic('success'); toast('Записала'); after(); }
        },
      }),
      h('button', { type: 'button', class: 'btn', text: 'Изменить', onclick: () => editSheet(item) }),
      h('button', {
        type: 'button', class: 'btn plain', text: 'Не надо',
        onclick: async () => {
          if (await act(() => api(`/api/items/${item.id}`, 'DELETE'))) { toast('Ок, не записываю'); after(); }
        },
      })));
  return box;
}

// --- вкладки ---

async function renderToday() {
  const d = await api('/api/today');
  const parts = [];
  if (d.quote) {
    parts.push(h('section', { class: 'quote-card', 'aria-label': 'Фраза дня' },
      h('span', { class: 'quote-label', text: '☀️ Фраза дня' }),
      h('p', { class: 'quote-text', text: d.quote })));
  }
  if (d.drafts.length) parts.push(section('Ждет подтверждения', card(d.drafts.map((i) => draftCard(i, refresh)))));
  if (d.overdue.length) parts.push(section(`Просрочено · ${d.overdue.length}`, rows(d.overdue), { danger: true }));
  parts.push(section(`Сегодня · ${longDay(d.date)}`,
    d.today.length ? rows(d.today, { noWhen: true }) : card(h('p', { class: 'empty', text: 'На сегодня дел нет 🙌' }))));
  const byDay = new Map();
  for (const item of d.upcoming) {
    if (!byDay.has(item.date)) byDay.set(item.date, []);
    byDay.get(item.date).push(item);
  }
  const week = [];
  const tomorrow = new Date(d.date + 'T12:00:00');
  tomorrow.setDate(tomorrow.getDate() + 1);
  for (const [date, items] of byDay) {
    const diff = Math.round((new Date(date + 'T12:00:00') - tomorrow) / 86400000);
    const prefix = diff === 0 ? 'Завтра · ' : diff === 1 ? 'Послезавтра · ' : '';
    week.push(h('h3', { text: prefix + longDay(date) }), rows(items, { noWhen: true }));
  }
  parts.push(section('Ближайшие 7 дней', week.length ? week : card(h('p', { class: 'empty', text: 'Ничего не запланировано' }))));
  if (d.done.length) {
    parts.push(section('Сделано сегодня', card(d.done.map((c) => h('div', { class: 'row' },
      h('span', { class: 'check on', 'aria-hidden': 'true' }),
      h('span', { class: 'row-main' }, h('span', { class: 'title strike', text: c.title })))))));
  }
  return parts;
}

async function renderCalendar() {
  const d = await api('/api/calendar' + (state.month ? `?month=${state.month}` : ''));
  state.month = d.month;
  const [year, month] = d.month.split('-').map(Number);
  if (!state.selected || !state.selected.startsWith(d.month)) {
    state.selected = d.today.startsWith(d.month) ? d.today : `${d.month}-01`;
  }
  const shift = (delta) => {
    const m = new Date(year, month - 1 + delta, 1);
    state.month = `${m.getFullYear()}-${String(m.getMonth() + 1).padStart(2, '0')}`;
    state.selected = null;
    refresh();
  };
  const grid = h('div', { class: 'cal-grid' },
    WEEKDAYS.map((w, i) => h('span', { class: 'cal-wd' + (i > 4 ? ' weekend' : ''), text: w })));
  const lead = (new Date(year, month - 1, 1).getDay() + 6) % 7;
  for (let i = 0; i < lead; i++) grid.append(h('span'));
  const daysInMonth = new Date(year, month, 0).getDate();
  for (let day = 1; day <= daysInMonth; day++) {
    const iso = `${d.month}-${String(day).padStart(2, '0')}`;
    const events = d.days[iso] || [];
    const real = events.some((e) => !e.repeat);
    const classes = ['cal-day'];
    if (iso === d.today) classes.push('today');
    if (iso === state.selected) classes.push('selected');
    grid.append(h('button', {
      type: 'button', class: classes.join(' '),
      'aria-label': `${day}, ${events.length ? 'записей: ' + events.length : 'пусто'}`,
      'aria-pressed': iso === state.selected ? 'true' : 'false',
      onclick: () => { state.selected = iso; refresh(); },
    }, h('span', { text: day }), h('span', { class: 'dot' + (real ? ' real' : events.length ? ' repeat' : ''), 'aria-hidden': 'true' })));
  }
  const selected = d.days[state.selected] || [];
  return [
    card([
      h('div', { class: 'cal-head' },
        h('h2', { text: d.title }),
        h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Предыдущий месяц', onclick: () => shift(-1) },
          svgPath('M15 5l-7 7 7 7')),
        h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Следующий месяц', onclick: () => shift(1) },
          svgPath('M9 5l7 7-7 7'))),
      grid,
      h('div', { class: 'legend' },
        h('span', {}, h('span', { class: 'dot real', style: 'color: var(--accent)' }), 'запись'),
        h('span', {}, h('span', { class: 'dot repeat' }), 'будущий повтор')),
    ]),
    section(longDay(state.selected), selected.length
      ? card(selected.map((i) => itemRow(i, { noWhen: true, repeat: i.repeat })))
      : card(h('p', { class: 'empty', text: 'Ничего не запланировано' }))),
  ];
}

function svgPath(d) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', d);
  svg.append(path);
  return svg;
}

async function renderPayments() {
  const d = await api('/api/payments');
  const payRows = (items) => card(items.map((i) => itemRow(i, { noCategory: true, noIcon: true })));
  const parts = [h('div', { class: 'totals' },
    h('div', { class: 'total' }, h('b', { text: d.left_label }), h('span', { text: `осталось оплатить в ${monthPrep(d.month)}` })),
    h('div', { class: 'total' }, h('b', { text: d.paid_label }), h('span', { text: `оплачено в ${monthPrep(d.month)}` })))];
  if (d.overdue.length) parts.push(section(`Просрочено · ${d.overdue.length}`, payRows(d.overdue), { danger: true }));
  parts.push(section('Скоро', d.soon.length ? payRows(d.soon) : card(h('p', { class: 'empty', text: 'Платежей нет' }))));
  if (d.paid.length) {
    parts.push(section(`Оплачено в ${monthPrep(d.month)}`, card(d.paid.map((p) => h('div', { class: 'row' },
      h('span', { class: 'check on', 'aria-hidden': 'true' }),
      h('span', { class: 'row-main' }, h('span', { class: 'lines' },
        h('span', { class: 'title', text: p.title }), h('span', { class: 'sub', text: 'оплачено ' + p.when }))),
      p.amount_label ? h('span', { class: 'amount', text: p.amount_label }) : null)))));
  }
  return parts;
}

function monthPrep(name) {
  // «октябрь» → «октябре», «май» → «мае», «март» → «марте»
  if (name.endsWith('ь') || name.endsWith('й')) return name.slice(0, -1) + 'е';
  return name + 'е';
}

async function renderIdeas() {
  const d = await api('/api/notes');
  const present = state.meta.categories.map((c) => c.name).filter((c) => d.notes.some((n) => n.category === c));
  if (state.ideaFilter !== 'Все' && !present.includes(state.ideaFilter)) state.ideaFilter = 'Все';
  const chips = h('div', { class: 'chips', role: 'toolbar', 'aria-label': 'Фильтр по категории' },
    ['Все', ...present].map((c) => h('button', {
      type: 'button', class: 'chip', text: c, 'aria-pressed': state.ideaFilter === c ? 'true' : 'false',
      onclick: () => { state.ideaFilter = c; refresh(); },
    })));
  const notes = d.notes.filter((n) => state.ideaFilter === 'Все' || n.category === state.ideaFilter);
  if (!d.notes.length) {
    return [card(h('p', { class: 'empty', text: 'Заметок пока нет. Напиши Марте, например: «посоветовали фильм „Достать ножи“».' }))];
  }
  return [chips, h('div', { class: 'list' }, notes.map(noteCard))];
}

function noteCard(n) {
  return h('div', { class: 'note' },
    h('span', { class: 'emoji', 'aria-hidden': 'true', text: n.icon }),
    h('span', { class: 'lines' },
      h('button', { type: 'button', class: 'row-main', style: 'min-height: 0; padding: 0', onclick: () => editSheet(n) },
        h('span', { class: 'lines' },
          h('span', { class: 'title', text: n.title }),
          h('span', { class: 'sub', text: n.category + (n.tags ? ' · ' + n.tags.split(' ').map((t) => '#' + t).join(' ') : '') }))),
      n.links.length ? h('span', { class: 'note-links' }, n.links.map((url) => h('button', {
        type: 'button', class: 'link-btn', text: '🔗 ' + shortHost(url), onclick: () => openLink(url),
      }))) : null));
}

async function renderShopping() {
  const d = await api('/api/shopping');
  const input = h('input', { type: 'text', placeholder: 'Что купить', 'aria-label': 'Новая покупка', maxlength: '200' });
  const add = async () => {
    const title = input.value.trim();
    if (!title) return;
    if (await act(() => api('/api/items', 'POST', { kind: 'shopping', title }))) { haptic(); refresh(); }
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
  const toggle = async (item, text) => {
    if (await act(() => api(`/api/shopping/${item.id}/toggle`, 'POST'))) { haptic(); toast(text + item.title); refresh(); }
  };
  const parts = [
    h('div', { class: 'add-row' }, input, h('button', { type: 'button', class: 'btn primary', text: 'Добавить', onclick: add })),
    section(`Купить · ${d.active.length}`, card(d.active.length ? d.active.map((i) => h('div', { class: 'row' },
      h('button', { type: 'button', class: 'check square', 'aria-label': 'Куплено: ' + i.title, onclick: () => toggle(i, 'Куплено: ') }),
      h('button', { type: 'button', class: 'row-main', onclick: () => toggle(i, 'Куплено: ') }, h('span', { class: 'title', text: i.title })),
    )) : h('p', { class: 'empty', text: 'Всё куплено 🎉' }))),
  ];
  if (d.bought.length) {
    parts.push(section('Куплено · исчезнет через сутки', card(d.bought.map((i) => h('div', { class: 'row' },
      h('span', { class: 'check square on', 'aria-hidden': 'true' }),
      h('span', { class: 'row-main' }, h('span', { class: 'title strike', text: i.title })),
      h('button', { type: 'button', class: 'side-btn', text: 'Вернуть', onclick: () => toggle(i, 'Вернула в список: ') }))))));
  }
  return parts;
}

const RENDER = { today: renderToday, calendar: renderCalendar, payments: renderPayments, ideas: renderIdeas, shopping: renderShopping };

let renderSeq = 0;
async function refresh() {
  const seq = ++renderSeq;
  $('title').textContent = TAB_TITLES[state.tab];
  document.querySelectorAll('.tabs button').forEach((b) => {
    if (b.dataset.tab === state.tab) b.setAttribute('aria-current', 'page');
    else b.removeAttribute('aria-current');
  });
  const main = $('main');
  if (!main.childElementCount) main.replaceChildren(h('p', { class: 'loading', text: 'Загружаю…' }));
  const parts = await act(() => RENDER[state.tab]());
  if (parts && seq === renderSeq) main.replaceChildren(...parts);
}

function switchTab(tab) {
  if (tab === state.tab) return;
  state.tab = tab;
  $('main').replaceChildren();
  window.scrollTo(0, 0);
  haptic();
  refresh();
}

// --- шторка ---

function openSheet(title, content, back) {
  $('sheet-title').textContent = title;
  $('sheet-body').replaceChildren(...[content].flat().filter(Boolean));
  $('sheet-body').scrollTop = 0;
  state.sheetBack = back || null;
  $('sheet-back').hidden = !back;
  $('sheet').hidden = false;
  document.body.style.overflow = 'hidden';
  if (tg && tg.BackButton) tg.BackButton.show();
}

function closeSheet() {
  $('sheet').hidden = true;
  document.body.style.overflow = '';
  state.sheetBack = null;
  if (tg && tg.BackButton) tg.BackButton.hide();
}

function sheetBack() {
  if (state.sheetBack) state.sheetBack();
  else closeSheet();
}

function field(label, control) {
  return h('label', { class: 'field' }, label, control);
}

function defaultOffsets(kind, allDay) {
  if (kind === 'birthday') return [3 * DAY, 0];
  if (kind === 'payment') return [DAY, 0];
  if (kind === 'task') return allDay ? [DAY] : [DAY, 60];
  return [];
}

// Форма записи: изменить существующую (item) или создать вручную (item = null)
function editSheet(item, preset = {}) {
  const isNew = !item;
  const data = item || { kind: 'task', title: '', category: 'Дом', date: '', time: '', recurrence: 'none', amount: null, source: '', offsets: null, status: 'new', ...preset };
  let offsets = data.offsets ? [...data.offsets] : null;
  let offsetsTouched = false;

  const kind = h('select', {}, Object.entries(KIND_LABELS).map(([v, l]) => h('option', { value: v, text: l })));
  kind.value = data.kind;
  const title = h('input', { type: 'text', value: data.title, maxlength: '200', placeholder: 'Что нужно сделать' });
  const category = h('select', {}, state.meta.categories.map((c) => h('option', { value: c.name, text: `${c.icon} ${c.name}` })));
  category.value = data.category;
  const amount = h('input', { type: 'text', inputmode: 'decimal', value: data.amount ?? '', placeholder: 'Например, 4200' });
  const date = h('input', { type: 'date', value: data.date });
  const time = h('input', { type: 'time', value: data.time });
  const recurrence = h('select', {}, state.meta.recurrences.map((r) => h('option', { value: r.value, text: r.label })));
  recurrence.value = data.recurrence;
  const source = h('textarea', { rows: '4', maxlength: '4000' });
  source.value = data.source || '';
  const chips = h('div', { class: 'chip-row' });

  const blocks = {
    kind: field('Вид записи', kind),
    category: field('Категория', category),
    amount: field('Сумма, ₽', amount),
    due: h('div', { class: 'grid2' }, field('Дата', date), field('Время (пусто — весь день)', time)),
    recurrence: field('Повтор', recurrence),
    remind: h('div', { class: 'field' }, 'Напомнить', chips),
    noteHint: h('p', { class: 'hint', text: 'Добавь дату — и заметка станет делом с напоминанием.' }),
    source: field('Полный текст и ссылки', source),
  };
  const linkRow = data.links && data.links.length
    ? h('div', { class: 'note-links' }, data.links.map((url) => h('button', { type: 'button', class: 'link-btn', text: '🔗 ' + shortHost(url), onclick: () => openLink(url) })))
    : null;

  const update = () => {
    const k = kind.value;
    const hasDate = Boolean(date.value);
    blocks.kind.hidden = !isNew;
    blocks.category.hidden = k === 'payment' || k === 'shopping';
    blocks.amount.hidden = k !== 'payment';
    blocks.due.hidden = k === 'shopping';
    blocks.recurrence.hidden = k === 'shopping' || k === 'birthday' || (k === 'note' && !hasDate);
    blocks.remind.hidden = k === 'shopping' || !hasDate;
    blocks.noteHint.hidden = k !== 'note' || hasDate;
    blocks.source.hidden = k !== 'note' || isNew;
    if (!offsetsTouched || offsets === null) {
      const effective = k === 'note' && hasDate ? 'task' : k;
      offsets = item && item.offsets && item.date && effective === item.kind && !time.value === !item.time
        ? [...item.offsets] : defaultOffsets(effective, !time.value);
    }
    chips.replaceChildren(...OFFSETS.map(([value, label]) => h('button', {
      type: 'button', class: 'chip', text: label, 'aria-pressed': offsets.includes(value) ? 'true' : 'false',
      onclick: () => {
        offsetsTouched = true;
        offsets = offsets.includes(value) ? offsets.filter((o) => o !== value) : [...offsets, value];
        update();
      },
    })));
  };
  [kind, date, time].forEach((el) => el.addEventListener('change', update));
  update();

  const form = () => {
    const out = { kind: kind.value, title: title.value.trim() };
    if (kind.value === 'shopping') return out;
    Object.assign(out, { category: category.value, date: date.value, time: time.value, recurrence: recurrence.value });
    if (kind.value === 'payment') out.amount = amount.value.trim();
    if (offsetsTouched) out.offsets = offsets;
    if (!isNew && data.kind === 'note') out.source = source.value;
    return out;
  };

  const save = async () => {
    if (!title.value.trim()) { toast('Напиши название'); title.focus(); return; }
    const result = await act(async () => {
      if (isNew) return api('/api/items', 'POST', form());
      const res = await api(`/api/items/${item.id}`, 'PATCH', form());
      if (item.status === 'draft') await api(`/api/items/${item.id}/save`, 'POST');
      return res;
    });
    if (!result) return;
    haptic('success');
    toast(isNew || item.status === 'draft' ? 'Записала' : (item.category !== category.value && kind.value !== 'payment'
      ? `Сохранено. Запомню, что такое — «${category.value}»` : 'Сохранено'));
    closeSheet();
    refresh();
  };

  const buttons = [h('button', { type: 'button', class: 'btn primary big', text: 'Сохранить', onclick: save })];
  const second = [];
  if (!isNew && item.status === 'active' && (item.kind === 'task' || item.kind === 'payment')) {
    second.push(h('button', { type: 'button', class: 'btn big', text: item.kind === 'payment' ? 'Оплачено' : 'Готово', onclick: () => { closeSheet(); markDone(item); } }));
  }
  if (!isNew) {
    second.push(h('button', {
      type: 'button', class: 'btn big danger', text: 'Удалить',
      onclick: async () => {
        if (!(await confirmAsk(`Удалить «${item.title}»?`))) return;
        if (await act(() => api(`/api/items/${item.id}`, 'DELETE'))) { toast('Удалила'); closeSheet(); refresh(); }
      },
    }));
  }
  if (second.length) buttons.push(h('div', { class: 'actions' }, second));

  openSheet(isNew ? 'Новая запись' : (item.status === 'draft' ? 'Черновик' : KIND_LABELS[item.kind]), [
    blocks.kind, field('Название', title), linkRow, blocks.category, blocks.amount, blocks.due, blocks.noteHint,
    blocks.recurrence, blocks.remind, blocks.source, ...buttons,
  ], preset.back);
}

// Новая запись текстом: разбирает GigaChat, как сообщение в чате
function newSheet() {
  let pending = '';
  const text = h('textarea', { rows: '3', maxlength: '2000', placeholder: 'Например: в пятницу сдать анализы в 8:30' });
  const result = h('div', { class: 'list' });
  const send = h('button', { type: 'button', class: 'btn primary big', text: 'Записать' });
  send.addEventListener('click', async () => {
    const value = text.value.trim();
    if (!value) { text.focus(); return; }
    send.disabled = true;
    send.textContent = 'Думаю…';
    const res = await act(() => api('/api/parse', 'POST', { text: value, pending }));
    send.disabled = false;
    send.textContent = 'Записать';
    if (!res) return;
    pending = '';
    text.value = '';
    result.replaceChildren();
    if (res.kind === 'clarify') {
      pending = res.pending;
      result.append(h('div', { class: 'answer', text: res.text }));
      text.placeholder = 'Ответ на вопрос Марты';
      text.focus();
      return;
    }
    if (res.kind === 'answer') {
      result.append(h('div', { class: 'answer', text: res.text }));
      return;
    }
    haptic('success');
    if (res.shopping.length) result.append(h('div', { class: 'answer', text: '🛒 Добавила в покупки: ' + res.shopping.join(', ').toLowerCase() }));
    for (const item of res.items) {
      if (item.status === 'draft') {
        result.append(card(draftCard(item, () => { closeSheet(); refresh(); })));
      } else {
        result.append(card(h('div', { class: 'draft' },
          h('div', { class: 'draft-head' }, h('span', { class: 'emoji', 'aria-hidden': 'true', text: item.icon }),
            h('span', { class: 'lines' }, h('span', { class: 'title', text: item.title }),
              h('span', { class: 'sub', text: subText(item) + ' · записала ✅' }))),
          h('div', { class: 'actions' }, h('button', { type: 'button', class: 'btn', text: 'Изменить', onclick: () => editSheet(item) })))));
      }
    }
    refresh();
  });
  openSheet('Новая запись', [
    field('Напиши, что запомнить, или задай вопрос', text),
    send,
    result,
    h('p', { class: 'hint', text: 'Марта разберет текст так же, как в чате. Вопрос вроде «во сколько завтра сантехник?» тоже сработает. Голосовые — в чате с ботом.' }),
    h('button', { type: 'button', class: 'btn link big', text: 'Заполнить вручную', onclick: () => editSheet(null, { back: newSheet }) }),
  ]);
  setTimeout(() => text.focus(), 50);
}

async function categoriesSheet() {
  const d = await act(() => api('/api/categories'));
  if (!d) return;
  const plural = (n) => (n % 10 === 1 && n % 100 !== 11 ? 'запись' : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? 'записи' : 'записей');
  openSheet('Категории', h('div', { class: 'tiles' }, d.categories.map((c) => h('button', {
    type: 'button', class: 'tile',
    onclick: () => (c.name === 'Покупки' ? (closeSheet(), switchTab('shopping')) : categorySheet(c)),
  }, h('span', { class: 'emoji', 'aria-hidden': 'true', text: c.icon }),
  h('span', { class: 'lines' }, h('span', { class: 'title', text: c.name }),
    h('span', { class: 'sub', text: `${c.count} ${plural(c.count)}` }))))));
}

async function categorySheet(cat) {
  const d = await act(() => api('/api/category?name=' + encodeURIComponent(cat.name)));
  if (!d) return;
  openSheet(`${cat.icon} ${cat.name}`, d.items.length ? rows(d.items, { noCategory: true })
    : card(h('p', { class: 'empty', text: 'Здесь пока пусто' })), categoriesSheet);
}

function searchSheet() {
  const input = h('input', { type: 'search', placeholder: 'Например: врач', 'aria-label': 'Поиск по записям' });
  const out = h('div', { class: 'list' }, h('p', { class: 'hint', text: 'Ищет по всем записям: делам, платежам, идеям и покупкам.' }));
  let timer = 0;
  let seq = 0;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      const my = ++seq;
      if (!q) { out.replaceChildren(h('p', { class: 'hint', text: 'Ищет по всем записям: делам, платежам, идеям и покупкам.' })); return; }
      const d = await act(() => api('/api/search?q=' + encodeURIComponent(q)));
      if (!d || my !== seq) return;
      out.replaceChildren(d.items.length ? card(d.items.map((i) => itemRow(i))) : h('p', { class: 'hint', text: 'Ничего не нашлось.' }));
    }, 300);
  });
  openSheet('Поиск', [h('label', { class: 'field' }, 'Название, категория или тег', input), out]);
  setTimeout(() => input.focus(), 50);
}

async function settingsSheet() {
  const s = await act(() => api('/api/settings'));
  if (!s) return;
  const put = (data, message) => act(async () => {
    const res = await api('/api/settings', 'PUT', data);
    haptic('success');
    toast(message);
    return res;
  });
  const sw = h('button', { type: 'button', class: 'switch', role: 'switch', 'aria-checked': String(s.confirm), 'aria-labelledby': 'confirm-label' });
  sw.addEventListener('click', async () => {
    const next = sw.getAttribute('aria-checked') !== 'true';
    if (await put({ confirm: next }, next ? 'Буду спрашивать подтверждение' : 'Буду сохранять сразу, без подтверждения')) {
      sw.setAttribute('aria-checked', String(next));
    }
  });
  const timeInput = (key, value, label) => {
    const input = h('input', { type: 'time', value, 'aria-label': label });
    input.addEventListener('change', async () => {
      if (!input.value) return;
      if (!(await put({ [key]: input.value }, `${label} теперь в ${input.value}`))) input.value = value;
      else value = input.value;
    });
    return h('label', { class: 'time-row' }, h('span', { text: label }), input);
  };
  openSheet('Настройки', [
    card(h('div', { class: 'switch-row', style: 'padding: 4px 14px' },
      h('span', { class: 'lines' },
        h('span', { class: 'title', id: 'confirm-label', text: 'Подтверждать новые записи' }),
        h('span', { class: 'sub', text: 'Марта покажет карточку с кнопкой «Сохранить»' })),
      sw)),
    section('Сводки, по Москве', card(h('div', { style: 'padding: 4px 14px' },
      timeInput('morning_at', s.morning_at, 'Утренняя сводка'),
      timeInput('evening_at', s.evening_at, 'Вечерний итог')))),
    h('p', { class: 'hint', text: 'Светлая и темная тема включаются сами, как в Telegram.' }),
  ]);
}

// --- запуск ---

function applyTheme() {
  document.documentElement.dataset.scheme = tg && tg.colorScheme === 'dark' ? 'dark'
    : (!tg && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}

async function init() {
  applyTheme();
  if (!tg || !tg.initData) {
    showBlocked('Открой Марту в Telegram: кнопка «Марта» в чате с @MartaSecretaryBot.');
    return;
  }
  tg.ready();
  tg.expand();
  try { tg.setHeaderColor('secondary_bg_color'); tg.setBackgroundColor('secondary_bg_color'); } catch (e) { /* старый Telegram */ }
  try { if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); } catch (e) { /* старый Telegram */ }
  tg.onEvent('themeChanged', applyTheme);
  tg.BackButton.onClick(sheetBack);

  document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  $('fab').addEventListener('click', newSheet);
  $('btn-cats').addEventListener('click', categoriesSheet);
  $('btn-search').addEventListener('click', searchSheet);
  $('btn-settings').addEventListener('click', settingsSheet);
  $('sheet-overlay').addEventListener('click', closeSheet);
  $('sheet-close').addEventListener('click', closeSheet);
  $('sheet-back').addEventListener('click', sheetBack);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && $('sheet').hidden) refresh(); });

  state.meta = await act(() => api('/api/meta'));
  if (state.meta) refresh();
}

init();
