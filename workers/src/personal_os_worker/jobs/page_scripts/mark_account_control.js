
({ ids, include, exclude, scoped, inputs }) => {
  const visible = element => {
    const box = element.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return false;
    const style = getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const words = element => String(
    element.innerText || element.textContent || element.getAttribute('aria-label')
    || element.getAttribute('value') || ''
  ).trim().replace(/\s+/g, ' ');
  const CONTROLS = 'button, [role="button"], a[href], input[type="submit"], input[type="button"]';
  for (const old of document.querySelectorAll('[MARK]')) old.removeAttribute('MARK');
  const mark = element => {
    // Workday lays a click_filter over its buttons, and the click has to land on it.
    const filter = element.parentElement
      ? element.parentElement.querySelector(':scope > [data-automation-id="click_filter"]')
      : null;
    (filter && visible(filter) ? filter : element).setAttribute('MARK', '1');
    return words(element) || element.getAttribute('data-automation-id') || 'control';
  };
  for (const id of ids) {
    const element = [...document.querySelectorAll(`[data-automation-id="${id}"]`)].find(visible);
    if (element) return mark(element);
  }
  if (!include) return null;
  const want = new RegExp(include, 'i');
  const avoid = exclude ? new RegExp(exclude, 'i') : null;
  let pool = [...document.querySelectorAll(CONTROLS)].filter(visible);
  if (scoped) {
    // The account form's own button sits with its inputs, and is a button.
    const scopes = [...document.querySelectorAll(inputs)].filter(visible).map(input => {
      let scope = input.closest('form') || input.parentElement;
      for (let depth = 0; scope && depth < 6 && !scope.querySelector(CONTROLS); depth += 1) {
        scope = scope.parentElement;
      }
      return scope;
    }).filter(Boolean);
    // No account inputs, no account form: nothing on the page is its button.
    pool = pool.filter(element => scopes.some(scope => scope.contains(element)));
    pool = pool.filter(element => element.tagName !== 'A');
  }
  const found = pool.find(element => {
    const text = `${words(element)} ${element.getAttribute('aria-label') || ''}`;
    return want.test(text) && !(avoid && avoid.test(text));
  });
  return found ? mark(found) : null;
}
