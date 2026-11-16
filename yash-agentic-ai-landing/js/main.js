(() => {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Destinations (behaviour kept from the current page)
  const dlg = document.querySelector('dialog');
  document.querySelectorAll('[data-destination]').forEach(btn => btn.addEventListener('click', () => {
    const key = btn.dataset.destination;
    const url = window.YASH_DESTINATIONS?.[key];
    if (url) { window.location.assign(url); return; }
    document.getElementById('destination-title').textContent = key === 'dashboard' ? 'Supply Chain Dashboard' : 'Case Studies';
    document.getElementById('destination-description').textContent = key === 'dashboard'
      ? 'The dashboard destination has not been connected yet.'
      : 'The case studies destination has not been connected yet.';
    closeMenu();
    dlg.showModal();
  }));
  dlg.addEventListener('click', e => {
    if (e.target === dlg) {
      const r = dlg.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dlg.close();
    }
  });

  // Nav state
  const nav = document.getElementById('top-nav');
  const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 40);
  onScroll(); window.addEventListener('scroll', onScroll, { passive: true });

  // Mobile menu
  const toggle = document.querySelector('.menu-toggle');
  const menu = document.getElementById('mobile-menu');
  function closeMenu() { menu.hidden = true; toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-label', 'Open menu'); document.body.style.overflow = ''; }
  toggle.addEventListener('click', () => {
    const open = menu.hidden;
    menu.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    document.body.style.overflow = open ? 'hidden' : '';
    if (open) menu.querySelector('a').focus();
  });
  menu.querySelectorAll('a').forEach(a => a.addEventListener('click', closeMenu));
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !menu.hidden) { closeMenu(); toggle.focus(); } });

  // Scroll reveal
  const io = new IntersectionObserver(entries => entries.forEach(en => {
    if (en.isIntersecting) { en.target.classList.add(en.target.id === 'flow' ? 'drawn' : 'in'); io.unobserve(en.target); }
  }), { threshold: 0.18, rootMargin: '0px 0px -40px 0px' });
  document.querySelectorAll('.reveal, #flow').forEach(el => reduce ? el.classList.add('in', 'drawn') : io.observe(el));

  // Hero video: pause when off-screen; never play with reduced motion
  const hv = document.querySelector('.hero-video');
  if (hv) {
    if (reduce) { hv.removeAttribute('autoplay'); hv.pause(); }
    else new IntersectionObserver(([en]) => en.isIntersecting ? hv.play().catch(() => {}) : hv.pause()).observe(hv);
  }

  // Genie parallax (desktop pointer only)
  const g = document.querySelector('[data-parallax]');
  if (g && !reduce && window.matchMedia('(pointer:fine)').matches) {
    let raf;
    window.addEventListener('mousemove', e => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const x = (e.clientX / innerWidth - .5) * 18, y = (e.clientY / innerHeight - .5) * 12;
        g.style.transform = `translate3d(${x}px,${y}px,0)`;
      });
    });
  }

  // Card tilt
  if (!reduce && window.matchMedia('(pointer:fine)').matches) {
    document.querySelectorAll('.card.agent').forEach(c => {
      c.addEventListener('mousemove', e => {
        const r = c.getBoundingClientRect();
        const rx = ((e.clientY - r.top) / r.height - .5) * -5, ry = ((e.clientX - r.left) / r.width - .5) * 5;
        c.style.transform = `perspective(900px) rotateX(${rx}deg) rotateY(${ry}deg) translateY(-4px)`;
      });
      c.addEventListener('mouseleave', () => { c.style.transform = ''; });
    });
  }

  const y = document.getElementById('year'); if (y) y.textContent = new Date().getFullYear();
})();
