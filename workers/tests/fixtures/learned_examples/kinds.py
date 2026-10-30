from personal_os_worker.jobs.registry.api import Example, Kind

# A widget no built-in captures: a div radio group where each option is a
# button-like div carrying aria-checked.
KIND = Kind(
    name="div_radio",
    selector='div[role="radio"]',
    reads="attribute:aria-checked",
    strategies=("click_div",),
    site="fixture.example",
    examples=(
        Example(
            fixture="radios.html",
            selector='#opt-yes',
            value="",
            expected="true",
        ),
    ),
)
