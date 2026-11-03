
element => {
  const files = [...(element.files || [])];
  if (!files.length) return false;
  if (element.parentElement && element.parentElement.getClientRects().length) return true;
  const text = element.ownerDocument.body ? element.ownerDocument.body.innerText : '';
  return files.some(file => text.includes(file.name));
}
