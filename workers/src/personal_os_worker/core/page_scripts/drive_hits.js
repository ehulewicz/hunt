(selector, x, y) => {
  const element = document.querySelector(selector);
  const hit = document.elementFromPoint(x, y);
  if (!element || !hit) return false;
  if (element === hit || element.contains(hit)) return true;
  return [...(element.labels || [])].some(label => label === hit || label.contains(hit));
}
