
() => {
  const EXCLUDE = new RegExp(
    '^\\s*(?:x|\u00d7|close|cancel|dismiss|back|skip|next|previous)\\s*$' +
    '|\\b(?:sign ?in|log ?in|sign ?up|signin|login|register|create (?:an )?account' +
    '|forgot|search jobs?|read more|privacy|cookies?|terms)\\b',
    'i'
  );
  const SUBMIT = new RegExp(
    '^\\s*submit\\s*$|\\b(?:submit|send|complete|finish)\\b.*\\b(?:application|form)\\b',
    'i'
  );
  const visible = element => {
    const box = element.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return false;
    const style = getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const dialogs = [...document.querySelectorAll('[role="dialog"], dialog')].filter(visible);
  // A dialog is modal by construction: while one is open it is the only way on.
  const scope = dialogs.length ? dialogs[dialogs.length - 1] : document.body;
  const heading = (() => {
    if (scope === document.body) return '';
    const labelled = scope.getAttribute('aria-labelledby');
    const referenced = labelled ? document.getElementById(labelled) : null;
    return String(
      referenced?.textContent
      || scope.getAttribute('aria-label')
      || scope.querySelector('h1, h2, h3, h4')?.textContent
      || ''
    ).trim().replace(/\s+/g, ' ');
  })();
  const options = [];
  const seen = new Set();
  for (const element of scope.querySelectorAll('button, [role="button"], a[href]')) {
    if (!visible(element) || element.disabled) continue;
    const type = String(element.getAttribute('type') || '').toLowerCase();
    if (type === 'submit' || type === 'reset') continue;
    const text = String(
      element.innerText || element.textContent || element.getAttribute('aria-label') || ''
    ).trim().replace(/\s+/g, ' ');
    if (!text || text.length > 60) continue;
    // A way in is always worded. A glyph (×, an arrow, an icon font) is a
    // close or a decoration, whatever the page's encoding turns it into.
    if (!/[a-z0-9]/i.test(text)) continue;
    const named = [
      text,
      element.getAttribute('aria-label') || '',
      element.getAttribute('title') || '',
    ];
    if (named.some(name => name && (EXCLUDE.test(name) || SUBMIT.test(name)))) continue;
    if (seen.has(text.toLowerCase())) continue;
    seen.add(text.toLowerCase());
    const token = String(options.length);
    element.setAttribute('data-personal-os-gate', token);
    options.push({ text, token });
  }
  if (!options.length) return null;
  // Record what the user clicks, so a gate the worker could not answer is
  // still learned from watching them pass it (R58).
  if (!window.__personalOsGateWatch) {
    window.__personalOsGateWatch = true;
    document.addEventListener('click', event => {
      const control = event.target instanceof Element
        ? event.target.closest('[data-personal-os-gate]') : null;
      if (control) window.__personalOsGateClicked = control.innerText.trim().replace(/\s+/g, ' ');
    }, true);
  }
  return { heading, options: options.slice(0, 8) };
}
