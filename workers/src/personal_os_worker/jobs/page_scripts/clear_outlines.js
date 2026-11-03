
() => {
  for (const node of document.querySelectorAll('[data-personal-os-outline]')) {
    node.style.outline = '';
    node.style.outlineOffset = '';
    node.removeAttribute('data-personal-os-outline');
  }
}
