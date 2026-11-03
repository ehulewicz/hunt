
element => {
  let node = element;
  while (node && !node.getClientRects().length) node = node.parentElement;
  if (!node || node === document.body || node === document.documentElement) return;
  node.style.outline = '2px solid #f59e0b';
  node.style.outlineOffset = '2px';
  node.setAttribute('data-personal-os-outline', '');
}
