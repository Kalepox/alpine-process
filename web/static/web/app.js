/* Monora UI: small vanilla helpers. No build step. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const icons = () => window.lucide && lucide.createIcons();

  icons();
  document.body.addEventListener('htmx:afterSwap', icons);

  // restart the pop-in animation on an element that was just shown
  function replay(el) { if (!el || el.classList.contains('hidden')) return; el.classList.remove('pop-in'); void el.offsetWidth; el.classList.add('pop-in'); }
  // fade in content that htmx swaps in (map side panel, ask answers, inbox rows)
  document.body.addEventListener('htmx:afterSwap', (e) => { replay(e.target.id === 'panel' ? e.target.firstElementChild : null); });

  // theme
  $('#theme-toggle')?.addEventListener('click', () => {
    const root = document.documentElement;
    root.classList.add('theme-anim');
    setTimeout(() => root.classList.remove('theme-anim'), 400);
    const dark = root.classList.toggle('dark');
    try { localStorage.setItem('monora-theme', dark ? 'dark' : 'light'); } catch (e) {}
    window.dispatchEvent(new CustomEvent('monora:theme', { detail: { dark } }));
  });

  // sidebar (mobile)
  const sb = $('#sidebar'), scrim = $('#sidebar-scrim');
  const setSb = (open) => { sb?.classList.toggle('-translate-x-full', !open); scrim?.classList.toggle('hidden', !open); };
  $('#sidebar-open')?.addEventListener('click', () => setSb(true));
  scrim?.addEventListener('click', () => setSb(false));

  // dropdown menus
  $$('[data-menu]').forEach((m) => {
    const panel = $('[data-menu-panel]', m);
    $('[data-menu-btn]', m).addEventListener('click', (e) => { e.stopPropagation(); panel.classList.toggle('hidden'); replay(panel); });
    document.addEventListener('click', (e) => { if (!m.contains(e.target)) panel.classList.add('hidden'); });
  });

  // toasts
  $$('.toast').forEach((t, i) => {
    const kill = () => { t.style.transition = 'opacity .3s ease, transform .3s ease'; t.style.opacity = 0; t.style.transform = 'translateY(-6px)'; setTimeout(() => t.remove(), 300); };
    $('[data-dismiss]', t)?.addEventListener('click', kill);
    setTimeout(kill, 5000 + i * 400);
  });

  // confirm dialog for forms with data-confirm
  const dlg = $('#confirm');
  document.addEventListener('submit', (e) => {
    const f = e.target;
    if (!f.matches('form[data-confirm]') || f.dataset.confirmed) return;
    e.preventDefault();
    const submitter = e.submitter;
    $('[data-confirm-title]', dlg).textContent = f.dataset.confirmTitle || f.dataset.confirm;
    $('[data-confirm-body]', dlg).textContent = f.dataset.confirmTitle ? f.dataset.confirm : '';
    const ok = $('[data-confirm-ok]', dlg);
    ok.textContent = f.dataset.confirmOk || 'Continue';
    ok.className = 'btn ' + (f.dataset.confirmTone === 'danger' ? 'btn-danger' : 'btn-primary');
    dlg.showModal();
    dlg.addEventListener('close', function onClose() {
      dlg.removeEventListener('close', onClose);
      if (dlg.returnValue === 'ok') { f.dataset.confirmed = '1'; submitter ? f.requestSubmit(submitter) : f.requestSubmit(); }
    });
  }, true);

  // ask panel
  const askPanel = $('#ask-panel'), askBtn = $('#ask-open');
  const askToggle = (open) => { askPanel?.classList.toggle('hidden', !open); askBtn?.classList.toggle('hidden', open); if (open) { replay(askPanel); $('textarea', askPanel)?.focus(); } };
  askBtn?.addEventListener('click', () => askToggle(true));
  $('#ask-close')?.addEventListener('click', () => askToggle(false));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') askToggle(false); });
  $$('[data-ask]').forEach((b) => b.addEventListener('click', () => {
    const ta = $('textarea', askPanel); ta.value = b.textContent.trim(); ta.form.requestSubmit();
  }));
  $$('textarea[data-autosize]').forEach((ta) => {
    const fit = () => { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; };
    ta.addEventListener('input', fit);
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ta.form.requestSubmit(); } });
  });
  document.body.addEventListener('htmx:beforeRequest', (e) => {
    if (e.target.closest('#ask-panel')) { $('#ask-suggestions')?.classList.add('hidden'); }
  });
  document.body.addEventListener('htmx:afterSwap', (e) => {
    if (e.target.id === 'ask-log') e.target.scrollTop = e.target.scrollHeight;
  });

  // dropzone
  $$('[data-dropzone]').forEach((z) => {
    const input = $('input[type=file]', z), form = z.closest('form');
    ['dragenter', 'dragover'].forEach((ev) => z.addEventListener(ev, (e) => { e.preventDefault(); z.dataset.over = '1'; }));
    ['dragleave', 'drop'].forEach((ev) => z.addEventListener(ev, (e) => { e.preventDefault(); delete z.dataset.over; }));
    z.addEventListener('drop', (e) => { input.files = e.dataTransfer.files; form.requestSubmit(); });
    input.addEventListener('change', () => input.files.length && form.requestSubmit());
  });

  // batch selection with running total
  $$('form[data-batch]').forEach((f) => {
    const boxes = $$('input[data-check]', f), all = $('input[data-check-all]', f);
    const fmt = new Intl.NumberFormat('en-IE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
    const update = () => {
      const sel = boxes.filter((b) => b.checked);
      $$('[data-sel-count]', f).forEach((el) => (el.textContent = sel.length));
      $$('[data-sel-total]', f).forEach((el) => (el.textContent = fmt.format(sel.reduce((s, b) => s + parseFloat(b.dataset.amount || 0), 0))));
      $$('[data-needs-sel]', f).forEach((el) => (el.disabled = !sel.length));
      if (all) all.checked = sel.length && sel.length === boxes.length;
      f.dataset.confirm = `${sel.length} payment(s) · ${fmt.format(sel.reduce((s, b) => s + parseFloat(b.dataset.amount || 0), 0))}`;
    };
    all?.addEventListener('change', () => { boxes.forEach((b) => (b.checked = all.checked)); update(); });
    boxes.forEach((b) => b.addEventListener('change', update));
    update();
  });

  // clickable rows
  document.addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-href]');
    if (tr && !e.target.closest('a,button,input,label')) location.href = tr.dataset.href;
  });

  // override needs a note: reveal textarea
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-reveal]');
    if (!b) return;
    const el = document.getElementById(b.dataset.reveal);
    el?.classList.toggle('hidden');
    $('textarea', el)?.focus();
  });
})();
