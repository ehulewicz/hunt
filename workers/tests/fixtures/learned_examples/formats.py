import re

from personal_os_worker.jobs.registry.api import Format, FormatInput


def grad_menu(render_input):
    """A site words its graduation menu 'Completed Month YYYY'."""
    value = str(render_input.value or "")
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if match is None:
        return None
    months = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    wanted = f"Completed {months[int(match.group(2)) - 1]} {match.group(1)}"
    for option in render_input.options:
        if option.strip() == wanted:
            return option
    return None


FORMAT = Format(
    name="completed_menu",
    fact_keys=("education.graduation_date",),
    render=grad_menu,
    site="fixture.example",
    examples=(
        (
            FormatInput(
                value="2027-05-15",
                value_type="date",
                options=("Completed May 2027", "Completed June 2027"),
                question="When do you graduate?",
                input_type="",
            ),
            "Completed May 2027",
        ),
    ),
)
