
        body => {
          const clone = body.cloneNode(true);
          clone.querySelectorAll(
            '[data-sitekey], iframe[src*="captcha" i]'
          ).forEach(e => e.remove());
          clone.querySelectorAll('input[type="password"]').forEach(e => e.removeAttribute('value'));
          return clone.outerHTML;
        }
        