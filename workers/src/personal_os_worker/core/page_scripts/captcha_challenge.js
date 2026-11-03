
() => [...document.querySelectorAll(
  'iframe[src*="recaptcha/api2/bframe" i], iframe[src*="recaptcha/enterprise/bframe" i],'
  + ' iframe[src*="hcaptcha" i][src*="frame=challenge" i], iframe[title*="challenge" i]'
)].some(element => {
  const box = element.getBoundingClientRect();
  if (box.width < 2 || box.height < 2) return false;
  const style = getComputedStyle(element);
  return style.visibility !== 'hidden' && style.display !== 'none'
    && Number(style.opacity || 1) > 0;
})
