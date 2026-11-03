(() => {
  const boxes = [], width = document.documentElement.clientWidth;
  const add = (box, weight) => {
    if (box.width >= 2 && box.height >= 2 && box.right > 0 && box.left < width) {
      boxes.push([box.left, box.right, weight]);
    }
  };
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  for (let node; (node = walk.nextNode()) && boxes.length < 5000;) {
    const text = node.data.trim();
    if (!text) continue;
    range.selectNodeContents(node);
    for (const box of range.getClientRects()) add(box, text.length);
  }
  const controls = 'input:not([type=hidden]),textarea,select,button,[role=combobox]';
  for (const control of document.querySelectorAll(controls)) {
    add(control.getBoundingClientRect(), 10);
  }
  return {width, boxes, scroll: document.documentElement.scrollWidth};
})()