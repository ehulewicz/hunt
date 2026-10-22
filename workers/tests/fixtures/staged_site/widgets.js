const slider = document.querySelector('#years');
function setYears(value) {
  const years = Math.max(0, Math.min(10, Math.round(value)));
  slider.setAttribute('aria-valuenow', String(years));
  document.querySelector('#years-value').textContent = `${years} years`;
  slider.style.setProperty('--years', `${years * 10}%`);
}
slider.addEventListener('click', event => {
  const bounds = slider.getBoundingClientRect();
  setYears((event.clientX - bounds.left) / bounds.width * 10);
  slider.focus();
});
slider.addEventListener('keydown', event => {
  const current = Number(slider.getAttribute('aria-valuenow'));
  const values = {
    ArrowRight: current + 1, ArrowUp: current + 1,
    ArrowLeft: current - 1, ArrowDown: current - 1,
    PageUp: current + 2, PageDown: current - 2, Home: 0, End: 10,
  };
  if (event.key in values) {
    event.preventDefault();
    setYears(values[event.key]);
  }
});

const radios = [...document.querySelectorAll('#team [role="radio"]')];
function selectTeam(selected) {
  radios.forEach((radio, index) => {
    radio.setAttribute('aria-checked', String(index === selected));
    radio.tabIndex = index === selected ? 0 : -1;
  });
  radios[selected].focus();
}
radios.forEach((radio, index) => {
  radio.addEventListener('click', () => selectTeam(index));
  radio.addEventListener('keydown', event => {
    const choices = {
      ArrowRight: (index + 1) % radios.length, ArrowDown: (index + 1) % radios.length,
      ArrowLeft: (index + radios.length - 1) % radios.length,
      ArrowUp: (index + radios.length - 1) % radios.length,
      Home: 0, End: radios.length - 1, ' ': index, Enter: index,
    };
    if (event.key in choices) {
      event.preventDefault();
      selectTeam(choices[event.key]);
    }
  });
});

document.querySelector('#application').addEventListener('submit', event => event.preventDefault());
