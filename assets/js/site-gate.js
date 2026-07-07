(function () {
  var STORAGE_KEY = 'ao-demos-preview-auth';
  var PASSWORD = 'Ansible123!';

  var gate = document.getElementById('site-gate');
  var form = document.getElementById('site-gate-form');
  var input = document.getElementById('site-gate-password');
  var error = document.getElementById('site-gate-error');

  if (!gate || !form) return;

  function unlock() {
    try {
      sessionStorage.setItem(STORAGE_KEY, '1');
    } catch (e) {
      /* ignore */
    }
    gate.setAttribute('hidden', '');
    document.body.classList.remove('site-gate-locked');
  }

  function lock() {
    document.body.classList.add('site-gate-locked');
    gate.removeAttribute('hidden');
    if (input) input.focus();
  }

  try {
    if (sessionStorage.getItem(STORAGE_KEY) === '1') {
      gate.setAttribute('hidden', '');
      return;
    }
  } catch (e) {
    /* ignore */
  }

  lock();

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (!input) return;

    if (input.value === PASSWORD) {
      if (error) error.setAttribute('hidden', '');
      unlock();
      return;
    }

    if (error) error.removeAttribute('hidden');
    input.value = '';
    input.focus();
  });
})();
