() => {
  const visible = node => {
    const box = node.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) return false;
    const style = getComputedStyle(node);
    return style.visibility !== 'hidden' && style.display !== 'none'
      && Number(style.opacity || 1) > 0;
  };
  const frames = [...document.querySelectorAll('iframe')].map(frame => {
    const box = frame.getBoundingClientRect();
    return { src: frame.src || '', width: box.width, height: box.height, visible: visible(frame) };
  });
  const errors = [...document.querySelectorAll('[aria-invalid="true"]')].filter(visible).length;
  const text = document.body ? document.body.innerText || '' : '';
  return { url: location.href, text: text.slice(0, 100000), frames, errors };
}
