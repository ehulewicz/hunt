
element => {
  const box = element.closest(
    'li, fieldset, [class*="question" i], [class*="field" i], [class*="form-group" i]'
  ) || element.parentElement;
  const out = [];
  for (const node of box.querySelectorAll('*')) {
    if (node.children.length || node === element || node.closest('label > :first-child')) continue;
    const text = (node.textContent || '').trim();
    const rect = node.getBoundingClientRect();
    if (text && rect.width > 0 && rect.height > 0 && text.length < 160) out.push(text);
  }
  return out;
}
