
  const roles = ['slider', 'spinbutton', 'radiogroup', 'listbox', 'textbox', 'switch',
                 'checkbox', 'radio', 'option'];
  const candidates = [...document.querySelectorAll(
    roles.map(role => `[role="${role}"]`).join(', ')
    + ', [contenteditable=""], [contenteditable="true"], button[aria-haspopup="listbox"]'
  )];
  const chrome = 'nav, header, footer, [role="navigation"], [role="banner"], '
    + '[role="contentinfo"], [data-sitekey], .g-recaptcha, .h-captcha, .cf-turnstile';
  const visible = node => Boolean(node.offsetWidth || node.offsetHeight
    || node.getClientRects().length);
  // A popup a captured combobox or input names is that control's, never another.
  const owned = new Set();
  document.querySelectorAll('[aria-controls], [aria-owns]').forEach(node => {
    `${node.getAttribute('aria-controls') || ''} ${node.getAttribute('aria-owns') || ''}`
      .split(/\s+/).filter(Boolean).forEach(id => owned.add(id));
  });
  const ownedPopup = node => {
    for (let at = node; at; at = at.parentElement) {
      if (at.id && owned.has(at.id)) return true;
    }
    return false;
  };
  const filledOf = node => {
    const role = node.getAttribute('role');
    if (['checkbox', 'radio', 'switch', 'option'].includes(role)) {
      return ['true', 'mixed'].includes(node.getAttribute('aria-checked') || '')
        || node.getAttribute('aria-selected') === 'true';
    }
    if (role === 'radiogroup' || role === 'listbox') {
      return Boolean(node.querySelector('[aria-checked="true"], [aria-selected="true"]'));
    }
    if (role === 'slider' || role === 'spinbutton') {
      const now = node.getAttribute('aria-valuenow');
      return now !== null && now !== node.getAttribute('aria-valuemin');
    }
    if (node.tagName === 'BUTTON') return false;
    return Boolean((node.textContent || '').trim());
  };
  const found = [];
  for (const node of candidates) {
    if (!visible(node) || node.closest(chrome) || node.closest('[aria-hidden="true"]')) continue;
    if (node.matches(native) || node.querySelector(native) || node.closest(native)) continue;
    if (node.closest('[role="combobox"]') || ownedPopup(node)) continue;
    if (customSelectors.some(selector => {
      // A selector one learned file got wrong is no match, never a dead scan.
      try { return node.matches(selector) || node.closest(selector); }
      catch (error) { return false; }
    })) {
      continue;
    }
    const role = node.getAttribute('role') || (node.isContentEditable ? 'textbox' : 'button');
    // An option, radio, or checkbox inside a group is that group, reported once.
    const parentGroup = node.parentElement?.closest('[role="radiogroup"], [role="listbox"]');
    if (['option', 'radio', 'checkbox'].includes(role) && parentGroup) continue;
    if (role === 'option') continue;
    const options = [...node.querySelectorAll('[role="radio"], [role="option"], [role="checkbox"]')]
      .map(option => (option.getAttribute('aria-label') || option.textContent || '').trim())
      .filter(Boolean).slice(0, 40);
    found.push({
      question: questionOf(node),
      role,
      tag: node.tagName.toLowerCase(),
      required: node.getAttribute('aria-required') === 'true'
        || /\*\s*$/.test(questionOf(node) || ''),
      filled: filledOf(node),
      options,
      selector: node.id ? `#${CSS.escape(node.id)}` : '',
      html: (node.parentElement || node).outerHTML.slice(0, 4000),
    });
  }
  return found;
}
