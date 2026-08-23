# Autofill source review

Personal OS does not vendor, execute, or depend on either reviewed extension.
Only the concepts listed below were reimplemented in Python behind Personal OS'
own safety and persistence boundaries.

## Little AI Helper

- Repository: `ritsth/job-autofill-extension`
- Reviewed commit: `ee967c2c5a41ee6f7b237f25094fd6bf68810a7b`
- License: MIT
- Concepts retained: separate ATS adapters, generic label/ARIA inspection,
  React combobox handling, dynamic-page rescanning, and editable long-form drafts.
- Concepts not retained: Chrome extension runtime, Gemini/proxy providers, local
  extension storage, automatic navigation, tailoring, eligibility overlays, or
  submission behavior.

## jobApplier

- Repository: `17nbist/jobApplier`
- Reviewed commit: `d025a1c6bc09a7cfbbe3580ad4a3638b110366bb`
- License: MIT
- Concepts retained: native value setters plus input/change events, fresh element
  lookup before each action, exact/token option matching with ambiguity refusal,
  resume-first rerender recovery, value readback, and localized sanitized failure
  snapshots.
- Concepts not retained: its extension, OpenRouter calls, profile storage,
  tailoring, broad ATS configuration engine, or automatic navigation.

The Python implementation is original code. These pins are the review baseline
for future comparisons; upstream changes are not pulled automatically.
