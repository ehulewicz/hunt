
  const controls = 'input, textarea, select, [role="combobox"], [role="radiogroup"], [role="listbox"]';
  const visible = node => Boolean(node.offsetWidth || node.offsetHeight
    || node.getClientRects().length);
  const words = node => (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
  const shown = ids => (ids || '').split(/\s+/).filter(Boolean)
    .map(id => document.getElementById(id))
    .filter(node => node && visible(node)).map(words).filter(Boolean);
  // Help text is described the same way an error is, so it is told apart by how it reads.
  const errorLike = /\b(error|invalid|required|must|please|enter|too (short|long)|not valid)\b/i;
  const described = node => {
    const texts = shown(node.getAttribute('aria-describedby'));
    return texts.find(text => errorLike.test(text)) || '';
  };
  const formError = /\b(errors?|invalid|required|fix|correct|problem|missing|unable|failed)\b/i;
  const out = [];
  const found = new Set();
  const report = (node, message, native) => {
    found.add(node);
    out.push({
      question: questionOf(node), name: node.getAttribute('name') || '', id: node.id || '',
      message, native,
    });
  };
  for (const node of document.querySelectorAll('[aria-invalid="true"]')) {
    if (!visible(node)) continue;
    const message = shown(node.getAttribute('aria-errormessage'))[0] || described(node)
      || 'the page marks this field invalid';
    report(node, message, false);
  }
  const alerts = [...document.querySelectorAll('[role="alert"]')]
    .filter(node => visible(node) && words(node));
  for (const alert of alerts) {
    let box = alert.parentElement;
    let owners = [];
    for (let depth = 0; box && !owners.length && depth < 3; depth += 1) {
      owners = [...box.querySelectorAll(controls)].filter(visible);
      box = box.parentElement;
    }
    if (owners.length === 1) {
      if (!found.has(owners[0])) report(owners[0], words(alert), false);
    } else if (formError.test(words(alert))) {
      // A toast ("Submitting...") is no error, and a press must not answer it with another.
      out.push({ question: '', name: '', id: '', message: words(alert), native: false });
    }
  }
  for (const node of document.querySelectorAll('input, textarea, select')) {
    if (found.has(node) || !visible(node) || !node.willValidate || node.validity.valid) continue;
    report(node, node.validationMessage || 'the browser marks this field invalid', true);
  }
  return out;
}
