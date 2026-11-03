(element, [group_selector, question_selector]) => {
  const group = element.closest(group_selector);
  return group
    ? (group.querySelector(question_selector)?.textContent || '')
        .replace(/\s+/g, ' ').trim()
    : '';
}