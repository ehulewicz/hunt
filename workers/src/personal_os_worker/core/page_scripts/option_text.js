
element => {
  const own = (element.innerText || '').trim();
  if (own) return own;
  const slotted = [...element.querySelectorAll('slot')]
    .flatMap(slot => slot.assignedNodes({flatten: true}))
    .map(node => node.textContent || '').join(' ').replace(/\s+/g, ' ').trim();
  if (slotted) return slotted;
  const host = element.getRootNode().host;
  return host ? (host.innerText || host.textContent || '').replace(/\s+/g, ' ').trim() : '';
}
