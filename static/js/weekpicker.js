/**
 * WeekPicker — 7-day × hourly grid for selecting face-to-face meeting times.
 *
 * Usage:
 *   const wp = new WeekPicker(mountEl, { existingSlots, defaultLocation });
 *   form.addEventListener('submit', e => {
 *     if (!wp.validate()) { e.preventDefault(); return; }
 *     wp.injectHiddenInputs(form);
 *   });
 *
 * existingSlots: Array<{ date: 'YYYY/MM/DD' | 'YYYY-MM-DD', time: 'HH:MM', location: string }>
 */
class WeekPicker {
  constructor(mountEl, { existingSlots = [], defaultLocation = '' } = {}) {
    this.mountEl = mountEl;
    this.defaultLocation = defaultLocation;

    // Pre-select existing slots, but only those within the current 7-day window
    const validDates = new Set(this._days().map(d => this._fmtDate(d)));
    this.selected = new Set(
      existingSlots
        .map(s => `${this._normDate(s.date)}|${s.time}`)
        .filter(key => validDates.has(key.split('|')[0]))
    );

    this._dragging  = false;
    this._dragMode  = null; // 'select' | 'deselect'
    this.render();
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  _normDate(s) {
    return String(s).replace(/-/g, '/');
  }

  _days() {
    const list = [];
    const base = new Date();
    base.setHours(0, 0, 0, 0);
    for (let i = 0; i < 7; i++) {
      const d = new Date(base);
      d.setDate(base.getDate() + i);
      list.push(d);
    }
    return list;
  }

  _fmtDate(d) {
    return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
  }

  _dayLabel(d) {
    const dow = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
    const isToday = this._isToday(d);
    return `(${dow})<br>${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}${isToday ? '<br><span class="wp-today-tag">今天</span>' : ''}`;
  }

  _isToday(d) {
    const t = new Date();
    return d.getDate() === t.getDate() && d.getMonth() === t.getMonth() && d.getFullYear() === t.getFullYear();
  }

  _isPast(d, h) {
    const now = new Date();
    const hour = parseInt(h, 10);
    const cellEnd = new Date(d.getFullYear(), d.getMonth(), d.getDate(), hour + 1, 0);
    return cellEnd <= now;
  }

  _hours() {
    const list = [];
    for (let h = 8; h <= 21; h++) list.push(`${String(h).padStart(2, '0')}:00`);
    return list;
  }

  // ── Render ────────────────────────────────────────────────────────────────

  render() {
    const days  = this._days();
    const hours = this._hours();

    const headCells = days
      .map(d => `<th class="wp-day-col${this._isToday(d) ? ' wp-today-col' : ''}">${this._dayLabel(d)}</th>`)
      .join('');

    const bodyRows = hours.map(h => {
      const cells = days.map(d => {
        const key  = `${this._fmtDate(d)}|${h}`;
        const past = this._isPast(d, h);
        const sel  = this.selected.has(key);
        return `<td class="wp-slot${sel ? ' wp-selected' : ''}${past ? ' wp-past' : ''}"
                    data-date="${this._fmtDate(d)}" data-time="${h}"${past ? ' data-past="1"' : ''}></td>`;
      }).join('');
      return `<tr><td class="wp-time-lbl">${h}</td>${cells}</tr>`;
    }).join('');

    this.mountEl.innerHTML = `
      <div class="wp-hint small text-muted mb-2">
        <i class="bi bi-hand-index me-1"></i>點擊或拖曳選取可面交的時段（選取後填寫地點）
      </div>
      <div class="wp-scroll">
        <table class="wp-table" id="wpTable" draggable="false">
          <thead><tr>
            <th class="wp-time-lbl wp-head-empty"></th>
            ${headCells}
          </tr></thead>
          <tbody>${bodyRows}</tbody>
        </table>
      </div>
      <div class="mt-3">
        <label class="form-label fw-semibold">
          <i class="bi bi-geo-alt me-1 text-muted"></i>面交地點
          <span class="text-danger">*</span>
        </label>
        <input type="text" class="form-control" id="wpLocation"
               placeholder="例：圖書館一樓大廳、資工系館 101"
               value="${this.defaultLocation.replace(/"/g, '&quot;')}">
        <div class="form-text">所有選取時段使用相同地點</div>
      </div>
      <div class="mt-2 small" id="wpSummary"></div>
    `;

    this._bindEvents();
    this._updateSummary();
  }

  // ── Events ────────────────────────────────────────────────────────────────

  _bindEvents() {
    const table = this.mountEl.querySelector('#wpTable');

    // Mouse
    table.addEventListener('mousedown', e => {
      const cell = e.target.closest('.wp-slot');
      if (!cell || cell.dataset.past) return;
      e.preventDefault();
      this._dragging = true;
      const key = `${cell.dataset.date}|${cell.dataset.time}`;
      this._dragMode = this.selected.has(key) ? 'deselect' : 'select';
      this._applyCell(cell, key);
    });

    table.addEventListener('mouseover', e => {
      if (!this._dragging) return;
      const cell = e.target.closest('.wp-slot');
      if (!cell || cell.dataset.past) return;
      const key = `${cell.dataset.date}|${cell.dataset.time}`;
      if (this._dragMode === 'select'   && !this.selected.has(key)) this._applyCell(cell, key);
      if (this._dragMode === 'deselect' &&  this.selected.has(key)) this._applyCell(cell, key);
    });

    document.addEventListener('mouseup', () => { this._dragging = false; });

    // Touch
    table.addEventListener('touchstart', e => {
      const t = e.touches[0];
      const cell = document.elementFromPoint(t.clientX, t.clientY)?.closest('.wp-slot');
      if (!cell || cell.dataset.past) return;
      this._dragging = true;
      const key = `${cell.dataset.date}|${cell.dataset.time}`;
      this._dragMode = this.selected.has(key) ? 'deselect' : 'select';
      this._applyCell(cell, key);
    }, { passive: true });

    table.addEventListener('touchmove', e => {
      if (!this._dragging) return;
      const t = e.touches[0];
      const cell = document.elementFromPoint(t.clientX, t.clientY)?.closest('.wp-slot');
      if (!cell || cell.dataset.past) return;
      const key = `${cell.dataset.date}|${cell.dataset.time}`;
      if (this._dragMode === 'select'   && !this.selected.has(key)) this._applyCell(cell, key);
      if (this._dragMode === 'deselect' &&  this.selected.has(key)) this._applyCell(cell, key);
    }, { passive: true });

    document.addEventListener('touchend', () => { this._dragging = false; });
  }

  _applyCell(cell, key) {
    if (this._dragMode === 'select') {
      this.selected.add(key);
      cell.classList.add('wp-selected');
    } else {
      this.selected.delete(key);
      cell.classList.remove('wp-selected');
    }
    this._updateSummary();
  }

  _updateSummary() {
    const el = this.mountEl.querySelector('#wpSummary');
    if (!el) return;
    const n = this.selected.size;
    el.innerHTML = n === 0
      ? '<span class="text-muted"><i class="bi bi-calendar-x me-1"></i>尚未選取任何時段</span>'
      : `<span class="text-success"><i class="bi bi-check-circle me-1"></i>已選取 <strong>${n}</strong> 個時段</span>`;
  }

  // ── Public API ────────────────────────────────────────────────────────────

  validate() {
    if (this.selected.size === 0) {
      alert('請至少選取一個可交易時段！');
      return false;
    }
    const loc = this.mountEl.querySelector('#wpLocation')?.value.trim();
    if (!loc) {
      this.mountEl.querySelector('#wpLocation')?.focus();
      alert('請填寫面交地點！');
      return false;
    }
    return true;
  }

  injectHiddenInputs(form) {
    form.querySelectorAll('input[data-wp]').forEach(el => el.remove());
    const loc = this.mountEl.querySelector('#wpLocation')?.value.trim() || '';
    const add = (name, value) => {
      const inp = document.createElement('input');
      inp.type = 'hidden'; inp.name = name; inp.value = value; inp.dataset.wp = '1';
      form.appendChild(inp);
    };
    for (const key of this.selected) {
      const [date, time] = key.split('|');
      add('slot_date', date);
      add('slot_time', time);
      add('slot_location', loc);
    }
  }
}
