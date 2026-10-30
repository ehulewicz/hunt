from personal_os_worker.jobs.registry.api import Example, Strategy


def slide(widget, value):
    """An ARIA spinbutton: arrow-key presses until its value matches."""
    for press in range(60):
        now = widget.text() or "0"
        if float(now) >= float(value) or press == 59:
            break
        widget.press("ArrowUp")
    widget.wait(50)
    return widget.text()


STRATEGY = Strategy(
    name="slide_to_value",
    kind="slider_widget",
    operate=slide,
    site="fixture.example",
    examples=(
        Example(
            fixture="slider.html",
            selector="#years",
            value="3",
            expected="3",
        ),
    ),
)
