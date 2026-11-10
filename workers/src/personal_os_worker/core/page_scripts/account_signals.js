
() => {
  // A captcha's own document knows nothing of the account, nor that its parent hides
  // it; the frame holding it is what reports the captcha.
  if (/(^|\.)(hcaptcha\.com|recaptcha\.net)$/.test(location.hostname)
      || (/(^|\.)google\.com$/.test(location.hostname)
        && location.pathname.startsWith('/recaptcha'))) {
    return {
      passwords: 0, emails: 0, codes: 0, google: 0, captcha: 0, text: '',
      controls: [], scopes: [], alerts: [], headings: [], automation: [],
    };
  }
  const visible = element => {
    const box = element.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return false;
    const style = getComputedStyle(element);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const words = element => String(
    element.innerText || element.textContent || element.getAttribute('aria-label')
    || element.getAttribute('value') || element.getAttribute('title') || ''
  ).trim().replace(/\s+/g, ' ');
  const all = selector => [...document.querySelectorAll(selector)].filter(visible);
  const CONTROLS = 'button, [role="button"], a[href], input[type="submit"], input[type="button"]';
  const passwords = all('input[type="password"]');
  const emails = all(
    'input[type="email"], input[autocomplete="email"], input[autocomplete="username"],'
    + ' input[data-automation-id="email"], input[name*="email" i], input[id*="email" i]'
  ).filter(input => !['hidden', 'password', 'checkbox', 'radio'].includes(
    String(input.type || '').toLowerCase()
  ));
  const codes = all(
    'input[autocomplete="one-time-code"], input[name*="code" i], input[id*="code" i],'
    + ' input[data-automation-id*="code" i]'
  );
  // What an email box's own form offers, with the words around it.
  const scopes = emails.map(input => {
    let scope = input.closest('form');
    if (!scope) {
      scope = input.parentElement;
      for (let depth = 0; scope && depth < 6 && !scope.querySelector(CONTROLS); depth += 1) {
        scope = scope.parentElement;
      }
    }
    if (!scope) return { controls: '', text: '' };
    return {
      controls: [...scope.querySelectorAll(CONTROLS)].filter(visible).map(words).join(' | '),
      text: words(scope).slice(0, 400),
      // Any other box to fill makes this a step of a form, not of a sign-in.
      others: [...scope.querySelectorAll('input, textarea, select')].filter(visible).filter(
        other => other !== input && !['hidden', 'checkbox', 'radio', 'submit', 'button']
          .includes(String(other.type || '').toLowerCase())
      ).length,
    };
  });
  const alerts = all(
    '[role="alert"], [aria-live="assertive"], [data-automation-id="errorMessage"],'
    + ' [data-automation-id="inputAlert"], [data-automation-id*="error" i], [class*="error" i]'
  ).filter(element => !element.querySelector('input, button, select, textarea'))
    .map(words).filter(text => text && text.length <= 300);
  return {
    passwords: passwords.length,
    emails: emails.length,
    codes: codes.length,
    controls: all(CONTROLS).map(words).filter(Boolean).map(text => text.slice(0, 80)),
    scopes,
    alerts: [...new Set(alerts)].slice(0, 10),
    headings: all('h1, h2, h3, [role="heading"], legend').map(words).filter(Boolean).slice(0, 10),
    automation: [...new Set(all('[data-automation-id]').map(
      element => element.getAttribute('data-automation-id')
    ))],
    // Google's rendered button, not One Tap's prompt, which floats over any page.
    google: all(
      'iframe[src*="accounts.google.com/gsi/button" i],'
      + ' iframe[title*="sign in with google button" i]'
    ).length,
    captcha: all(
      'iframe[src*="recaptcha" i][src*="anchor" i]:not([src*="size=invisible" i]),'
      + ' iframe[src*="hcaptcha" i][src*="frame=checkbox" i]:not([src*="checkbox-invisible" i])'
    ).length,
    text: String(document.body?.innerText || '').slice(0, 3000),
  };
}
