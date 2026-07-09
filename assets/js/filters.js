(function () {
  const statusPills = document.querySelectorAll('[data-filter-status]');
  const blockPills = document.querySelectorAll('[data-filter-block]');
  const cards = document.querySelectorAll('.demo-card');
  const navLinks = document.querySelectorAll('[data-filter-nav]');
  const searchInput = document.getElementById('demo-search');
  const emptyState = document.getElementById('filter-empty');

  if (!cards.length) return;

  let activeStatus = 'all';
  let activeBlock = null;

  function cardMatchesStatus(card, status) {
    if (status === 'all') return true;
    return card.dataset.status === status;
  }

  function cardMatchesBlock(card, block) {
    if (!block) return true;
    const blocks = (card.dataset.buildingBlocks || '').split(',').filter(Boolean);
    return blocks.indexOf(block) >= 0;
  }

  function cardMatchesSearch(card, query) {
    if (!query) return true;
    const haystack = (card.dataset.search || '').toLowerCase();
    return haystack.indexOf(query) >= 0;
  }

  function setActiveStatusPill(status) {
    statusPills.forEach(function (pill) {
      pill.classList.toggle('active', pill.dataset.filterStatus === status);
    });
  }

  function setActiveBlockPill(block) {
    blockPills.forEach(function (pill) {
      pill.classList.toggle('active', block && pill.dataset.filterBlock === block);
    });
  }

  function applyFilters() {
    const query = searchInput ? searchInput.value.trim().toLowerCase() : '';
    let visibleCount = 0;

    cards.forEach(function (card) {
      const show =
        cardMatchesStatus(card, activeStatus) &&
        cardMatchesBlock(card, activeBlock) &&
        cardMatchesSearch(card, query);
      card.classList.toggle('hidden', !show);
      if (show) visibleCount += 1;
    });

    document.querySelectorAll('.section').forEach(function (section) {
      const visible = section.querySelectorAll('.demo-card:not(.hidden)').length;
      section.style.display = visible > 0 ? '' : 'none';
    });

    if (emptyState) {
      emptyState.classList.toggle('hidden', visibleCount > 0);
    }
  }

  statusPills.forEach(function (pill) {
    pill.addEventListener('click', function () {
      activeStatus = pill.dataset.filterStatus;
      setActiveStatusPill(activeStatus);
      applyFilters();
    });
  });

  blockPills.forEach(function (pill) {
    pill.addEventListener('click', function () {
      const block = pill.dataset.filterBlock;
      activeBlock = activeBlock === block ? null : block;
      setActiveBlockPill(activeBlock);
      applyFilters();
    });
  });

  if (searchInput) {
    searchInput.addEventListener('input', applyFilters);
  }

  navLinks.forEach(function (link) {
    link.addEventListener('click', function (e) {
      const filter = link.dataset.filterNav;
      if (filter && statusPills.length) {
        e.preventDefault();
        activeStatus = filter;
        setActiveStatusPill(activeStatus);
        applyFilters();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    });
  });

  var hash = window.location.hash.replace('#', '');
  if (hash && ['active', 'coming-soon'].indexOf(hash) >= 0) {
    activeStatus = hash;
    setActiveStatusPill(activeStatus);
  }

  applyFilters();
})();
