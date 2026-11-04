
        elements => {
        const uploadShown = element => {
  const files = [...(element.files || [])];
  if (!files.length) return false;
  if (element.parentElement && element.parentElement.getClientRects().length) return true;
  const text = element.ownerDocument.body ? element.ownerDocument.body.innerText : '';
  return files.some(file => text.includes(file.name));
};
        return elements.filter(element => {
          const type = (element.getAttribute('type') || '').toLowerCase();
          // React-select mirrors the chosen value into an aria-hidden input that
          // carries the required flag; the visible combobox is the real control.
          if (element.getAttribute('aria-hidden') === 'true') return false;
          // Responsive widgets can keep a hidden native select beside their
          // visible combobox. That is one question, never a second filled field.
          if (element.tagName === 'SELECT' && !element.getClientRects().length
              && element.parentElement?.querySelector('[role="combobox"]')) return false;
          // A captcha's own response box answers the captcha, never a question.
          const naming = `${element.getAttribute('name') || ''} ${element.id || ''}`.toLowerCase();
          if (/captcha|turnstile/.test(naming)) return false;
          if (element.closest('[data-sitekey], .g-recaptcha, .h-captcha, .cf-turnstile')) {
            return false;
          }
          // A text box nobody can see (an XML mirror, a "paste your resume" tab
          // that is not open) is not a question. Hidden radios, checkboxes,
          // files and selects stay: widgets style over them and still use them.
          const textLike = element.tagName === 'TEXTAREA' || (
            element.tagName === 'INPUT' && !['radio', 'checkbox', 'file'].includes(type)
          );
          if (textLike && !(element.offsetWidth || element.offsetHeight
              || element.getClientRects().length)) {
            return false;
          }
          // A collapsed popup's own search box (a phone field's country picker)
          // belongs to that control, never to the form.
          const popup = element.closest('[role="dialog"], [role="listbox"], [role="menu"]');
          if (popup && !popup.offsetWidth && !popup.offsetHeight) return false;
          return !['hidden', 'password', 'submit', 'button', 'reset'].includes(type);
        }).map((element, index) => {
          const id = element.id;
          // The document, or the shadow root the control lives in (SmartRecruiters).
          const home = element.getRootNode();
          const explicit = id ? home.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
          // A label that names another, existing control by its id is that
          // control's (Ashby's education labels name ids that do not exist).
          const ownLabel = node => !node.htmlFor || node.htmlFor === id
            || !(home.getElementById ? home.getElementById(node.htmlFor)
              : document.getElementById(node.htmlFor));
          const wrapping = element.closest('label');
          const choice = ['radio', 'checkbox'].includes(element.type);
          // A container a choice's question is read from must hold that one
          // question: every radio in it shares this one's name, and at most
          // one label or legend in it is no option's own. A section ("Job
          // specific questions") holds several, and its heading asks nothing.
          const oneQuestion = container => {
            if (!choice || !container) return true;
            const options = [...container.querySelectorAll(`input[type="${element.type}"]`)];
            if (element.type === 'radio' && options.some(option => option.name !== element.name)) {
              return false;
            }
            const nodes = [...container.querySelectorAll('label, legend')].filter(node => (
              (node.textContent || '').trim() && !options.some(
                option => node.contains(option) || (node.htmlFor && node.htmlFor === option.id)
              )
            ));
            return nodes.filter(
              node => !nodes.some(other => other !== node && other.contains(node))
            ).length <= 1;
          };
          const fieldset = element.closest('fieldset');
          // Ashby writes the group question as a heading label, not a legend.
          const legend = oneQuestion(fieldset)
            ? fieldset?.querySelector(':scope > legend, :scope > label') : null;
          const marksRequired = node => Boolean(node && (
            node.getAttribute('aria-required') === 'true'
            || /required/i.test(String(node.className))
            || /\*\s*$/.test(node.textContent || '')
            || node.querySelector('[class*="required"], [aria-required="true"]')
          ));
          const groupRequired = marksRequired(legend);
          const group = element.closest('[role="group"], [role="radiogroup"]');
          // A group names its question by aria-label, or by the element its
          // aria-labelledby points at.
          const groupNames = group?.getAttribute('aria-labelledby');
          const groupLabel = group && oneQuestion(group)
            ? (group.getAttribute('aria-label') || (choice && groupNames
                ? groupNames.split(/\s+/)
                    .map(id => document.getElementById(id)?.textContent || '').join(' ').trim()
                : '')) || null
            : null;
          const nearby = element.closest('[class*="field"], [class*="question"], [data-testid]');
          const nearbyChild = nearby
            ? [...nearby.children].find(child => {
                const text = String(child.textContent || '').trim();
                return text && !child.contains(element)
                  && !child.querySelector('input, textarea, select, [role="combobox"]');
              }) || null
            : null;
          const nearbyQuestion = oneQuestion(nearby) ? nearbyChild : null;
          // Composite controls (notably Ashby's Education History widget) do
          // not associate every nested input with its label. Walk outward for
          // the nearest label that is a direct child of a control-local
          // wrapper before falling back to the outer field/section heading.
          let localLabel = null;
          let localWrapper = element.parentElement || (home !== document ? home : null);
          for (let depth = 0; localWrapper && depth < 5; depth += 1) {
            localLabel = [...localWrapper.children].find(
              child => child.tagName === 'LABEL' && !child.contains(element) && ownLabel(child)
            ) || null;
            if (localLabel) break;
            localWrapper = localWrapper.parentElement;
          }
          const labelledBy = element.getAttribute('aria-labelledby');
          const referenced = labelledBy
            ? labelledBy.split(/\s+/).map(id => document.getElementById(id)?.textContent || '')
                .join(' ')
            : '';
          // Lever's custom questions and its EEO section put the question text
          // in a sibling `.application-label`, never associated with the control
          // by id or by wrapping it together with a single-input field; a radio
          // or checkbox is additionally wrapped in its own tiny label holding
          // only that option's text, which is what `wrapping` above already
          // captures and must keep winning for those.
          const leverQuestion = element.closest('.application-question');
          const leverQuestionText = (
            leverQuestion?.querySelector('.application-label')?.textContent || ''
          ).replace(/[*\u2731]\s*$/, '').trim() || null;
          // The EEO section nests a long description inside the same label as
          // the option text; this span is the option's own text alone.
          const leverOptionLabel = wrapping
            ?.querySelector('.application-answer-alternative')?.textContent?.trim() || null;
          // A choice rendered as aria-pressed buttons with a hidden checkbox that
          // mirrors their state (Ashby's yes/no is one). Found by the ARIA state
          // that screen readers rely on rather than by a class name, so the same
          // detection holds on any site: the nearest ancestor holding two or more
          // pressable buttons and no other form input.
          let yesnoContainer = null;
          if (element.type === 'checkbox') {
            let ancestor = element.parentElement;
            for (let depth = 0; ancestor && depth < 3 && !yesnoContainer; depth += 1) {
              const pressable = ancestor.querySelectorAll('button[aria-pressed]').length;
              const inputs = ancestor.querySelectorAll(
                'input:not([type="hidden"]), textarea, select'
              ).length;
              if (pressable >= 2 && inputs === 1) yesnoContainer = ancestor;
              ancestor = ancestor.parentElement;
            }
          }
          const educationEntry = element.closest(
            '.ashby-application-form-input-education-entry'
          );
          // The question sits beside the button group: Ashby's title label, or
          // the first label or legend among the group's siblings.
          const yesnoLabel = yesnoContainer
            ? (
                yesnoContainer.parentElement?.querySelector(
                  'label.ashby-application-form-question-title'
                )
                || [...(yesnoContainer.parentElement?.children || [])].find(
                  child => ['LABEL', 'LEGEND'].includes(child.tagName)
                )
                || null
              )
            : null;
          const yesnoButtons = yesnoContainer
            ? [...yesnoContainer.querySelectorAll('button[aria-pressed]')] : [];
          const yesnoPressed = yesnoButtons.find(
            button => button.getAttribute('aria-pressed') === 'true'
          ) || null;
          const finalType = yesnoContainer ? 'yesno' : element.getAttribute('type');
          // A placeholder such as "Start typing..." is the last resort; Ashby
          // comboboxes have no id, so their question lives on the field's label.
          // A Lever structured question (notably the location typeahead) nests
          // the whole field inside the question's own label, so the wrapping
          // label carries the live dropdown text too. `.application-label` is
          // the question alone; only a radio or checkbox still needs the
          // wrapping label, which there holds just that option's text.
          const optionWrapping = ['radio', 'checkbox'].includes(element.type)
            ? wrapping?.textContent : null;
          const baseLabel = (
              yesnoLabel?.textContent
              || explicit?.textContent
              || leverOptionLabel
              || optionWrapping
              || leverQuestionText
              || wrapping?.textContent
              || element.getAttribute('aria-label')
              || referenced
              || localLabel?.textContent
              || [...(nearby?.querySelectorAll('label') || [])].find(ownLabel)?.textContent
              || element.getAttribute('placeholder')
              || ''
            ).trim();
          // A pair of native selects under one date label is otherwise
          // indistinguishable. Its empty first option supplies the missing
          // Month/Year part of the label, but is not itself a valid answer.
          const selectPart = element.tagName === 'SELECT'
            ? String(element.options[0]?.textContent || '').trim().replace(/\.{2,}$/, '')
            : '';
          const rawLabel = (
            selectPart && /^(month|year)$/i.test(selectPart)
              && !baseLabel.toLowerCase().split(/\s+/).includes(selectPart.toLowerCase())
              ? `${baseLabel} ${selectPart}` : baseLabel
          ).trim();
          // A trailing asterisk marks a required field on Greenhouse boards.
          const label = rawLabel.replace(/\s*\*\s*$/, '');
          let shell = element.parentElement;
          let mirroredRequired = false;
          let mirroredValue = '';
          for (let depth = 0; shell && depth < 4; depth += 1) {
            const mirror = shell.querySelector('input[aria-hidden="true"]');
            if (mirror) {
              mirroredRequired = mirroredRequired || mirror.required === true;
              mirroredValue = mirroredValue || String(mirror.value || '').trim();
              if (mirroredRequired) break;
            }
            shell = shell.parentElement;
          }
          // How many inputs answer the same question: options sharing a name,
          // or, unnamed, sharing a fieldset or group. One checkbox is a toggle;
          // several are a multi-choice group.
          // Options with no shared name, fieldset or group role (JazzHR's Yes/No
          // checkboxes): the smallest ancestor holding two or more of them is the
          // question's block, and the question is the nearest label before them
          // that is not an option's own.
          let blockOptions = [];
          let blockQuestion = null;
          if (choice && !legend && !groupLabel && !yesnoContainer) {
            let ancestor = element.parentElement;
            for (let depth = 0; ancestor && depth < 5; depth += 1) {
              if (!oneQuestion(ancestor)) break;
              const options = [...ancestor.querySelectorAll(`input[type="${element.type}"]`)];
              if (options.length >= 2) {
                const optionLabel = node => options.some(
                  option => node.contains(option) || (node.htmlFor && node.htmlFor === option.id)
                );
                // One question only: a container holding several questions'
                // options is a form, not a question's block.
                const questions = [...ancestor.querySelectorAll('label, legend')].filter(
                  node => !optionLabel(node) && (node.textContent || '').trim()
                );
                if (questions.length === 1 && (questions[0].compareDocumentPosition(element)
                    & Node.DOCUMENT_POSITION_FOLLOWING)) {
                  blockOptions = options;
                  blockQuestion = questions[0];
                } else if (questions.length === 0 && element.type === 'radio'
                    && options.every(option => option.name === element.name)) {
                  // A radio group whose question is plain text before its
                  // options ("<p>Are you 18?</p>"): the block's first child
                  // that holds text and no control. A lone Yes is no question.
                  blockQuestion = [...ancestor.children].find(child => (
                    (child.textContent || '').trim()
                    && !child.querySelector('input, textarea, select, [role="combobox"]')
                    && !optionLabel(child)
                    && (child.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
                  )) || null;
                  if (blockQuestion) blockOptions = options;
                }
                // The first shared ancestor may contain only the options.
                // Keep walking to their question, but never across questions.
                if (blockQuestion) break;
              }
              ancestor = ancestor.parentElement;
            }
          }
          // What a file control sits in, crossing shadow roots: a dropzone
          // labelled only "Choose a file" inside <oc-resume-upload> is the resume.
          const containerHints = [];
          if (element.type === 'file') {
            let node = element;
            for (let depth = 0; depth < 8; depth += 1) {
              const root = node.getRootNode();
              node = node.parentElement || (root instanceof ShadowRoot ? root.host : null);
              if (!node || node === document.body) break;
              const classes = typeof node.className === 'string' ? node.className : '';
              containerHints.push(`${node.tagName} ${node.id || ''} ${classes}`.toLowerCase());
            }
          }
          let groupSize = 1;
          if (['radio', 'checkbox'].includes(element.type) && !yesnoContainer) {
            const sameType = `input[type="${element.type}"]`;
            groupSize = element.name
              ? (element.form || document).querySelectorAll(
                  `${sameType}[name="${CSS.escape(element.name)}"]`
                ).length
              : element.closest('fieldset, [role="group"], [role="radiogroup"]')
                  ?.querySelectorAll(sameType).length || 1;
            if (groupSize < 2 && blockOptions.length >= 2) groupSize = blockOptions.length;
          }
          const isCombobox = element.getAttribute('role') === 'combobox';
          // react-select empties its search input after a pick and renders the
          // choice in a sibling; that sibling is the value of the control.
          let chosenText = '';
          if (isCombobox) {
            let holder = element.parentElement;
            for (let depth = 0; holder && depth < 4; depth += 1) {
              const chosen = holder.querySelector(
                '[class*="single-value"], [class*="multi-value"]'
              );
              if (chosen) { chosenText = chosen.textContent.trim(); break; }
              holder = holder.parentElement;
            }
          }
          // A radio or checkbox shares its name with its group: its value
          // tells it apart from the other options.
          const optionValue = ['radio', 'checkbox'].includes(element.type)
            && element.hasAttribute('value')
            ? `[value="${CSS.escape(element.getAttribute('value'))}"]` : '';
          const selector = id
            ? `#${CSS.escape(id)}`
            : element.getAttribute('name')
              ? `${element.tagName.toLowerCase()}[name="${
                  CSS.escape(element.getAttribute('name'))
                }"]${optionValue}`
              : '';
          return {
            index,
            // The position among every control the frame holds, filtered or
            // not: what `fresh_control` counts when the selector is ambiguous.
            control_index: elements.indexOf(element),
            tag: element.tagName.toLowerCase(),
            type: finalType,
            name: element.getAttribute('name'),
            label,
            group: (
              legend?.textContent
              || groupLabel
              || nearbyQuestion?.textContent
              || (['radio', 'checkbox'].includes(element.type) ? leverQuestionText : null)
              || blockQuestion?.textContent
              || ''
            ).trim().replace(/\s*\*\s*$/, ''),
            required: element.required === true
              || element.getAttribute('aria-required') === 'true'
              || marksRequired(localLabel)
              || /\*\s*$/.test(rawLabel)
              || (['radio', 'checkbox'].includes(element.type) && groupRequired)
              || marksRequired(blockQuestion)
              || Boolean(group && oneQuestion(group)
                && group.getAttribute('aria-required') === 'true')
              || (element.getAttribute('role') === 'combobox' && mirroredRequired)
              || Boolean(yesnoContainer && /_required_/.test(yesnoLabel?.className || '')),
            selector,
            role: element.getAttribute('role'),
            // -1 when the control declares no cap; a draft has to fit what it does.
            max_length: element.maxLength > 0 ? element.maxLength : null,
            placeholder: element.getAttribute('placeholder'),
            step: element.getAttribute('step'),
            autocomplete: element.getAttribute('autocomplete'),
            section: educationEntry ? 'education' : null,
            options: yesnoContainer
              ? yesnoButtons.map(button => button.textContent.trim()).filter(Boolean)
              : element.tagName === 'SELECT'
                ? [...element.options]
                    .filter(option => option.value !== '' && !option.disabled)
                    .map(option => option.textContent.trim()).filter(Boolean)
                : [],
            value: yesnoContainer
              ? (yesnoPressed ? yesnoPressed.textContent.trim() : null)
              : ['radio', 'checkbox'].includes(element.type)
                ? (element.getAttribute('value') || '') : null,
            multiple: element.tagName === 'SELECT' && element.multiple === true,
            group_size: groupSize,
            container: containerHints.join(' | ').slice(0, 600) || null,
            aria: {
              autocomplete: element.getAttribute('aria-autocomplete'),
              haspopup: element.getAttribute('aria-haspopup'),
            },
            fingerprint: [element.tagName, finalType,
              element.getAttribute('name'), label, element.getAttribute('role')
            ].join('|').toLowerCase(),
            filled: yesnoContainer
              ? Boolean(yesnoPressed)
              : element.type === 'file'
                ? uploadShown(element)
                : ['checkbox', 'radio'].includes(element.type)
                  ? element.checked === true
                  // A phone box prefilled with its dial code ("+1") or a mask
                  // ("(___) ___-____") holds no number yet.
                  : element.type === 'tel'
                    ? String(element.value || '').replace(/\D/g, '').length >= 7
                    : String(element.value || '').trim().length > 0
                      || (isCombobox && (mirroredValue.length > 0 || chosenText.length > 0)),
            // The option a select or a typeahead shows, read for the follow-up below it.
            selected: element.tagName === 'SELECT'
              ? (element.value ? (element.selectedOptions[0]?.textContent || '').trim() : null)
              : isCombobox ? (chosenText || mirroredValue || null) : null,
          };
        });
        }
        