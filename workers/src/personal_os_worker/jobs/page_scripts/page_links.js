
() => [...document.querySelectorAll('a[href], [role="link"], [role="button"], button')]
  .map((node, index) => ({
    index,
    text: (node.innerText || node.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim(),
    href: node.href || '',
    visible: Boolean(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
  }))
  .filter(item => item.visible && item.text && item.text.length <= 160)
