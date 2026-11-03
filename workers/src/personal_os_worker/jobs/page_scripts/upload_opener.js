
element => {
  if (element.parentElement && element.parentElement.getClientRects().length) return null;
  let block = element.parentElement;
  while (block && !block.getClientRects().length) block = block.parentElement;
  if (!block || block === document.body || block === document.documentElement) return null;
  if (block.querySelectorAll('input[type="file"]').length !== 1) return null;
  const openers = [...block.querySelectorAll('a, button, [role="button"]')].filter(node => {
    if (!node.getClientRects().length) return false;
    const text = (node.textContent || '').trim();
    if (!/\b(attach|upload|browse|choose|select)\b/i.test(text)) return false;
    if (/paste|remove|delete|linkedin|dropbox|drive|indeed|submit|apply/i.test(text)) return false;
    if (node.tagName === 'BUTTON' && node.type === 'submit') return false;
    const href = (node.getAttribute('href') || '').trim();
    return !(node.tagName === 'A' && href && !/^(#|javascript:)/i.test(href));
  });
  return openers.length === 1 ? openers[0] : null;
}
