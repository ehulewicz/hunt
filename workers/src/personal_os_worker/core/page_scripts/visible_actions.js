
() => [...document.querySelectorAll('a[href], button, [role="button"], [role="link"]')]
  .filter(node => node.offsetWidth || node.offsetHeight || node.getClientRects().length)
  .map(node => (node.innerText || node.getAttribute('aria-label') || '')
    .replace(/\s+/g, ' ').trim())
  .filter(text => text && text.length <= 80)
