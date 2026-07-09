(function () {
  var dialog = document.getElementById('ao-term-dialog');
  if (!dialog) return;

  var titleEl = document.getElementById('ao-term-dialog-title');
  var categoryEl = document.getElementById('ao-term-dialog-category');
  var definitionEl = document.getElementById('ao-term-dialog-definition');
  var moreEl = document.getElementById('ao-term-dialog-more');
  var terms = {};

  var dataEl = document.getElementById('ao-glossary-page-terms');
  if (dataEl) {
    try {
      terms = JSON.parse(dataEl.textContent);
    } catch (e) {
      terms = {};
    }
  }

  var glossaryBase = moreEl ? moreEl.getAttribute('href').replace(/\/$/, '') : '/glossary';

  function openTerm(key) {
    var term = terms[key];
    if (!term) return;

    categoryEl.textContent = term.category || '';
    titleEl.textContent = term.label || key;
    definitionEl.textContent = term.short || '';
    moreEl.href = glossaryBase + '/#' + key;
    moreEl.textContent = 'Learn more';

    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
    } else {
      dialog.setAttribute('open', '');
    }
  }

  function closeDialog() {
    if (dialog.open) {
      dialog.close();
    } else {
      dialog.removeAttribute('open');
    }
  }

  document.querySelectorAll('.ao-block[data-ao-term]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      openTerm(btn.getAttribute('data-ao-term'));
    });
  });

  dialog.querySelectorAll('[data-ao-dialog-close]').forEach(function (btn) {
    btn.addEventListener('click', closeDialog);
  });

  dialog.addEventListener('click', function (e) {
    if (e.target === dialog) closeDialog();
  });

  dialog.addEventListener('cancel', function (e) {
    e.preventDefault();
    closeDialog();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && dialog.open) closeDialog();
  });
})();
