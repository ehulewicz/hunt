(element, on) => {
  const target = element.closest('fieldset, [role=radiogroup], [role=group]') || element;
  target.style.outline = on ? '3px solid #d946ef' : '';
  target.style.outlineOffset = on ? '2px' : '';
}