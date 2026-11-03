
const native = 'input, textarea, select';
const questionOf = node => {
  const labelledBy = node.getAttribute('aria-labelledby');
  const referenced = labelledBy
    ? labelledBy.split(/\s+/).map(id => document.getElementById(id)?.textContent || '')
        .join(' ').trim()
    : '';
  const explicit = node.id
    ? document.querySelector(`label[for="${CSS.escape(node.id)}"]`)?.textContent || '' : '';
  let nearby = '';
  let wrapper = node.parentElement;
  for (let depth = 0; wrapper && !nearby && depth < 5; depth += 1) {
    const heading = [...wrapper.children].find(child => (
      ['LABEL', 'LEGEND', 'P', 'SPAN', 'DIV', 'H3', 'H4'].includes(child.tagName)
      && !child.contains(node) && (child.textContent || '').trim()
      && !child.querySelector(native + ', [role]')
      && (child.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)
    ));
    nearby = heading ? heading.textContent : '';
    wrapper = wrapper.parentElement;
  }
  return (node.getAttribute('aria-label') || referenced || explicit || nearby || '')
    .replace(/\s+/g, ' ').trim().replace(/\s*\*\s*$/, '');
};
