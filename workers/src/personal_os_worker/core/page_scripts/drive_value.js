selector => {
  const element = document.querySelector(selector);
  if (!element) return null;
  if (element.tagName === 'SELECT') {
    const option = element.selectedOptions[0];
    return option ? String(option.textContent || '').trim() : '';
  }
  if (element.type === 'radio' || element.type === 'checkbox') {
    const group = element.name
      ? [...document.querySelectorAll(`input[name="${CSS.escape(element.name)}"]`)]
      : [element];
    return group.filter(node => node.checked).map(node => node.value);
  }
  return element.value ?? null;
}
