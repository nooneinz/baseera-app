/* Halo motion layer: scroll reveal, auto-hiding header, wipe-fill rows, accordion,
   counters and the dismissible floating call-to-action. Progressive enhancement:
   without JS (or with prefers-reduced-motion) everything is simply visible. */
(function () {
  'use strict';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return [].slice.call((r || document).querySelectorAll(s)); };
  var isAr = (document.documentElement.lang || '') === 'ar' || document.documentElement.dir === 'rtl';

  function ready(fn) { if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn); else fn(); }

  /* 1) Reveal: elements marked [data-hw-reveal] settle in once; children of [data-hw-stagger] arrive one after another */
  function initReveal() {
    $$('[data-hw-stagger]').forEach(function (g) {
      $$('[data-hw-reveal]', g).forEach(function (el, i) { el.style.setProperty('--hw-d', (i * 0.12) + 's'); });
    });
    var els = $$('[data-hw-reveal]');
    if (reduce || !('IntersectionObserver' in window)) { els.forEach(function (e) { e.classList.add('hw-in'); }); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('hw-in'); io.unobserve(e.target); } });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
    els.forEach(function (e) { io.observe(e); });
  }

  /* 2) Header hides while scrolling down, returns while scrolling up, gets a blurred backing after the top */
  function initNav() {
    var nav = $('.hw-nav'); if (!nav) return;
    var last = window.scrollY, ticking = false;
    function update() {
      var y = window.scrollY;
      nav.classList.toggle('is-solid', y > 24);
      if (!reduce) nav.classList.toggle('is-hidden', y > last && y > 120);
      last = y; ticking = false;
    }
    window.addEventListener('scroll', function () { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
    nav.addEventListener('focusin', function () { nav.classList.remove('is-hidden'); });
    update();
  }

  /* 3) Rows: a clone of the row, inverted, is revealed by clip-path so the text flips exactly at the wipe edge */
  function initRows() {
    $$('.hw-row').forEach(function (row) {
      var fill = document.createElement('div');
      fill.className = 'hw-row__fill'; fill.setAttribute('aria-hidden', 'true');
      $$(':scope > *', row).forEach(function (c) { fill.appendChild(c.cloneNode(true)); });
      row.appendChild(fill);
      row.tabIndex = 0;
      var on = function () { row.classList.add('is-on'); }, off = function () { row.classList.remove('is-on'); };
      row.addEventListener('mouseenter', on); row.addEventListener('mouseleave', off);
      row.addEventListener('focus', on); row.addEventListener('blur', off);
    });
  }

  /* 4) Accordion: one open at a time, first item open by default */
  function initAccordions() {
    $$('.hw-acc').forEach(function (acc) {
      var items = $$('.hw-acc__item', acc);
      function set(item, open) {
        item.classList.toggle('is-open', open);
        var b = $('.hw-acc__btn', item); if (b) b.setAttribute('aria-expanded', open ? 'true' : 'false');
        var p = $('.hw-acc__panel', item); if (p) p.setAttribute('aria-hidden', open ? 'false' : 'true');
      }
      items.forEach(function (item, i) {
        set(item, i === 0 && acc.hasAttribute('data-open-first'));
        $('.hw-acc__btn', item).addEventListener('click', function () {
          var willOpen = !item.classList.contains('is-open');
          items.forEach(function (o) { set(o, false); });
          set(item, willOpen);
        });
      });
    });
  }

  /* 5) Numbers count up once when they enter the viewport */
  function initCounters() {
    var els = $$('[data-hw-count]'); if (!els.length) return;
    function toAr(s) { return isAr ? s.replace(/[0-9]/g, function (d) { return '٠١٢٣٤٥٦٧٨٩'[d]; }) : s; }
    function paint(el, v) { el.textContent = (el.getAttribute('data-prefix') || '') + toAr(Number(v).toLocaleString('en-US')) + (el.getAttribute('data-suffix') || ''); }
    function run(el) {
      var to = parseFloat(el.getAttribute('data-hw-count')) || 0;
      if (reduce) { paint(el, to); return; }
      var t0 = null, dur = 1400;
      (function step(t) { if (!t0) t0 = t; var p = Math.min((t - t0) / dur, 1), e = 1 - Math.pow(1 - p, 4); paint(el, Math.round(to * e)); if (p < 1) requestAnimationFrame(step); })(performance.now());
    }
    if (!('IntersectionObserver' in window)) { els.forEach(function (e) { run(e); }); return; }
    var io = new IntersectionObserver(function (entries) { entries.forEach(function (e) { if (e.isIntersecting) { run(e.target); io.unobserve(e.target); } }); }, { threshold: 0.4 });
    els.forEach(function (e) { io.observe(e); });
  }

  /* 6) Floating CTA: appears after the first screen, dismissible for the session */
  function initFloat() {
    var f = $('.hw-float'); if (!f) return;
    try { if (sessionStorage.getItem('hwFloatClosed') === '1') { f.remove(); return; } } catch (e) { }
    f.classList.add('is-hidden');
    window.addEventListener('scroll', function () { f.classList.toggle('is-hidden', window.scrollY < window.innerHeight * 0.8); }, { passive: true });
    var x = $('.hw-float__x', f);
    if (x) x.addEventListener('click', function () { f.remove(); try { sessionStorage.setItem('hwFloatClosed', '1'); } catch (e) { } });
  }

  /* 7) Card spotlight: a soft violet light follows the pointer over bento cards */
  function initSpotlight() {
    if (reduce) return;
    $$('[data-sc-spot] .sc-card').forEach(function (card) {
      card.addEventListener('pointermove', function (e) {
        var r = card.getBoundingClientRect();
        card.style.setProperty('--mx', (e.clientX - r.left) + 'px');
        card.style.setProperty('--my', (e.clientY - r.top) + 'px');
      });
    });
  }

  ready(function () { initReveal(); initSpotlight(); initNav(); initRows(); initAccordions(); initCounters(); initFloat(); if (window.lucide) lucide.createIcons(); });
})();
