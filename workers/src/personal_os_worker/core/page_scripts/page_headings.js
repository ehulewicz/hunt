
() => [document.title, ...[...document.querySelectorAll('h1, h2, [role="heading"]')]
  .slice(0, 6).map(node => node.innerText || '')]
  .map(text => text.replace(/\s+/g, ' ').trim()).filter(Boolean)
