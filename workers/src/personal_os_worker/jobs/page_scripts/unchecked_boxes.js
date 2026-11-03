
() => {
  const visible = element => Boolean(
    element && (element.offsetWidth || element.offsetHeight || element.getClientRects().length)
  );
  return [...document.querySelectorAll('input[type="checkbox"]')]
    .filter(box => !box.checked && !box.disabled)
    .map((box, index) => {
      const home = box.getRootNode();
      const label = (box.id ? home.querySelector(`label[for="${CSS.escape(box.id)}"]`) : null)
        || box.closest('label');
      if (!visible(box) && !visible(label)) return null;
      const token = String(index);
      box.setAttribute('data-personal-os-terms', token);
      const text = String(
        (label && label.innerText) || box.getAttribute('aria-label')
        || (box.parentElement && box.parentElement.innerText) || ''
      ).trim().replace(/\s+/g, ' ').slice(0, 300);
      return {
        token, label: text, name: box.name || box.id || '',
        automation: box.getAttribute('data-automation-id') || '',
      };
    })
    .filter(Boolean);
}
