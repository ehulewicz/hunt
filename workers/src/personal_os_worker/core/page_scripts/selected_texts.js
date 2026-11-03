element => (element.tagName === 'SELECT'
  ? [...element.selectedOptions]
  : [...element.querySelectorAll(
      '[aria-selected="true"], [aria-pressed="true"], [aria-checked="true"]')])
    .map(node => String(node.textContent || '').trim()).join(', ')