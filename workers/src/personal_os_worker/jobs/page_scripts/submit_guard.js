
(() => {
  if (window.__personalOsSubmitGuard) return;
  const submitting = new RegExp([
    '^\\s*submit\\b',
    '\\bsubmit\\s+(?:my\\s+|your\\s+)?application\\b',
    '\\bsend\\s+application\\b',
    '\\bapply\\s+now\\b',
    '\\bfinish\\s+application\\b',
  ].join('|'), 'i');
  const armed = () => ARMED;
  const stop = event => {
    if (!armed()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  window.__personalOsSubmitGuard = true;
  document.addEventListener('submit', stop, true);
  document.addEventListener('click', event => {
    const target = event.target instanceof Element
      ? event.target.closest('button, input[type=submit], input[type=button], [role=button], a')
      : null;
    if (!target) return;
    const text = (target.innerText || target.value || target.getAttribute('aria-label') || '');
    if ((target.matches('input[type=submit]')) || submitting.test(text.trim())) stop(event);
  }, true);
  const submit = HTMLFormElement.prototype.submit;
  const requestSubmit = HTMLFormElement.prototype.requestSubmit;
  HTMLFormElement.prototype.submit = function () {
    if (!armed()) return submit.call(this);
  };
  HTMLFormElement.prototype.requestSubmit = function (...args) {
    if (!armed()) return requestSubmit.apply(this, args);
  };
})();
