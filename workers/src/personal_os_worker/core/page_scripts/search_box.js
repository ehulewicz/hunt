
pattern => {
  const matches = new RegExp(pattern, 'i');
  const boxes = [...document.querySelectorAll(
    'input[type="search"], [role="searchbox"], input[type="text"], input:not([type])'
  )].filter(node => node.offsetWidth || node.offsetHeight || node.getClientRects().length);
  const named = node => [node.getAttribute('placeholder'), node.getAttribute('aria-label'),
    node.getAttribute('name'), node.id, node.getAttribute('type')].join(' ');
  const index = boxes.findIndex(node => node.type === 'search'
    || node.getAttribute('role') === 'searchbox' || matches.test(named(node)));
  if (index < 0) return null;
  const all = [...document.querySelectorAll('input, [role="searchbox"]')];
  return all.indexOf(boxes[index]);
}
