
  return elements.map((element, position) => {
    if (!(element.offsetWidth || element.offsetHeight || element.getClientRects().length)) {
      return null;
    }
    let held = {};
    try { held = read(element, readArg) || {}; } catch (error) { held = {}; }
    const label = String(held.label || questionOf(element) || '')
      .replace(/\s+/g, ' ').trim().replace(/\s*\*\s*$/, '');
    const value = held.value === undefined || held.value === null ? '' : String(held.value);
    return {
      position,
      label,
      value,
      options: Array.isArray(held.options) ? held.options.map(String) : [],
      required: Boolean(held.required) || element.getAttribute('aria-required') === 'true',
      filled: held.filled === undefined ? value.trim().length > 0 : Boolean(held.filled),
      selector: element.id ? `#${CSS.escape(element.id)}` : '',
      role: element.getAttribute('role'),
      tag: element.tagName.toLowerCase(),
    };
  }).filter(Boolean);
}
