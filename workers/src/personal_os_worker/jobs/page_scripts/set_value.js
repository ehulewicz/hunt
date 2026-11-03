
        (element, value) => {
          const proto = element instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
          if (setter) setter.call(element, value); else element.value = value;
          element.dispatchEvent(new Event('input', {bubbles: true}));
          element.dispatchEvent(new Event('change', {bubbles: true}));
        }
        