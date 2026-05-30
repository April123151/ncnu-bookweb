/**
 * WeekPicker — dropdown-based slot picker for face-to-face meeting times.
 * Each row: date <select> (next 7 days) + time <select> (hourly) + location <input>.
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
    this.initSlots = existingSlots.length > 0 ? existingSlots : [];
    this._dateOpts = this._buildDateOpts();
    this._timeOpts = this._buildTimeOpts();
    this.render();
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  _days() {
    const days = [];
    const base = new Date();
    base.setHours(0, 0, 0, 0);
    for (let i = 0; i < 7; i++) {
      const d = new Date(base);
      d.setDate(base.getDate() + i);
      days.push(d);
    }
    return days;
  }

  _fmtDate(d) {
    return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
  }

  _normDate(s) {
    return String(s).replace(/-/g, '/');
  }

  _buildDateOpts() {
    const DOW = ['日', '一', '二', '三', '四', '五', '六'];
    const today = new Date();
    return this._days().map(d => {
      const val  = this._fmtDate(d);
      const dow  = DOW[d.getDay()];
      const isToday = d.getDate() === today.getDate() && d.getMonth() === today.getMonth();
      const label = `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')} (${dow})${isToday ? ' 今天' : ''}`;
      return `<option value="${val}">${label}</option>`;
    }).join('');
  }

  _buildTimeOpts() {
    const opts = [];
    for (let h = 8; h <= 21; h++) {
      const val = `${String(h).padStart(2, '0')}:00`;
      opts.push(`<option value="${val}">${val}</option>`);
    }
    return opts.join('');
  }

  // ── Render ────────────────────────────────────────────────────────────────

  render() {
    this.mountEl.innerHTML = `
      <p class="text-muted small mb-3">
        <i class="bi bi-info-circle me-1"></i>可新增多個時段，買家選擇後下單
      </p>
      <div id="wpSlotRows"></div>
      <button type="button" class="btn btn-outline-primary btn-sm mt-1" id="wpAddBtn">
        <i class="bi bi-plus me-1"></i>新增時段
      </button>
    `;

    const rowsEl = this.mountEl.querySelector('#wpSlotRows');
    const init = this.initSlots.length > 0
      ? this.initSlots
      : [{ date: '', time: '', location: this.defaultLocation }];

    init.forEach(s => rowsEl.appendChild(this._makeRow(s)));

    this.mountEl.querySelector('#wpAddBtn').addEventListener('click', () => {
      rowsEl.appendChild(this._makeRow({ date: '', time: '', location: this.defaultLocation }));
      this._syncDelBtns();
    });

    this._syncDelBtns();
  }

  // ── Row ───────────────────────────────────────────────────────────────────

  _makeRow(slot) {
    const dateVal = slot.date ? this._normDate(slot.date) : '';
    const timeVal = slot.time || slot.time_str || '';
    const locVal  = (slot.location || '').replace(/"/g, '&quot;');

    const row = document.createElement('div');
    row.className = 'slot-row';
    row.innerHTML = `
      <div>
        <label class="form-label">日期</label>
        <select class="form-select form-select-sm wp-date">
          <option value="">選擇日期</option>
          ${this._dateOpts}
        </select>
      </div>
      <div>
        <label class="form-label">時間</label>
        <select class="form-select form-select-sm wp-time">
          <option value="">選擇時間</option>
          ${this._timeOpts}
        </select>
      </div>
      <div>
        <label class="form-label">地點</label>
        <input type="text" class="form-control form-control-sm wp-location"
               placeholder="例：圖書館一樓大廳" value="${locVal}">
      </div>
      <div class="d-flex align-items-end pb-1">
        <button type="button" class="btn btn-sm btn-outline-danger wp-del">
          <i class="bi bi-trash"></i>
        </button>
      </div>
    `;

    // Pre-select date
    if (dateVal) {
      for (const opt of row.querySelector('.wp-date').options) {
        if (opt.value === dateVal) { opt.selected = true; break; }
      }
    }
    // Pre-select time
    if (timeVal) {
      for (const opt of row.querySelector('.wp-time').options) {
        if (opt.value === timeVal) { opt.selected = true; break; }
      }
    }

    row.querySelector('.wp-del').addEventListener('click', () => {
      row.remove();
      this._syncDelBtns();
    });

    return row;
  }

  _syncDelBtns() {
    const rows = this.mountEl.querySelectorAll('.slot-row');
    rows.forEach(row => {
      row.querySelector('.wp-del').style.visibility = rows.length > 1 ? 'visible' : 'hidden';
    });
  }

  // ── Public API ────────────────────────────────────────────────────────────

  hasValidSlots() {
    for (const row of this.mountEl.querySelectorAll('.slot-row')) {
      if (row.querySelector('.wp-date').value &&
          row.querySelector('.wp-time').value &&
          row.querySelector('.wp-location').value.trim()) return true;
    }
    return false;
  }

  validate() {
    if (!this.hasValidSlots()) {
      alert('請至少新增一個完整的面交時段（日期、時間、地點）！');
      return false;
    }
    return true;
  }

  injectHiddenInputs(form) {
    form.querySelectorAll('input[data-wp]').forEach(el => el.remove());
    const add = (name, val) => {
      const inp = document.createElement('input');
      inp.type = 'hidden'; inp.name = name; inp.value = val; inp.dataset.wp = '1';
      form.appendChild(inp);
    };
    for (const row of this.mountEl.querySelectorAll('.slot-row')) {
      const date = row.querySelector('.wp-date').value;
      const time = row.querySelector('.wp-time').value;
      const loc  = row.querySelector('.wp-location').value.trim();
      if (date && time && loc) {
        add('slot_date', date);
        add('slot_time', time);
        add('slot_location', loc);
      }
    }
  }
}
