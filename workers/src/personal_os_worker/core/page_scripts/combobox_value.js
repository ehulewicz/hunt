
    element => {
      const own = String(element.value || '').trim();
      if (own) return own;
      let shell = element.parentElement;
      for (let depth = 0; shell && depth < 4; depth += 1) {
        const chosen = shell.querySelector('[class*="single-value"], [class*="multi-value"]');
        if (chosen && chosen.textContent.trim()) return chosen.textContent.trim();
        const mirror = shell.querySelector('input[aria-hidden="true"]');
        if (mirror && String(mirror.value || '').trim()) return String(mirror.value).trim();
        shell = shell.parentElement;
      }
      return '';
    }
