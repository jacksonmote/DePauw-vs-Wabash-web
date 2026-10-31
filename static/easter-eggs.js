(() => {
  const button = document.getElementById("tiger-entrance");
  const status = document.getElementById("entrance-status");
  const wrapper = document.querySelector(".entrance-wrap");
  if (!button || !status || !wrapper) return;
  let activations = 0;
  let unlocked = false;
  button.addEventListener("click", () => {
    if (unlocked) return;
    activations = Math.min(5, activations + 1);
    if (activations === 5) {
      unlocked = true;
      status.textContent = "Your education has been verified.";
      wrapper.classList.add("entrance-unlocked");
    }
  });
  button.hidden = false;
})();

(() => {
  const note = document.querySelector(".tiger-note");
  const facts = note ? Array.from(note.querySelectorAll(".tiger-fact")) : [];
  if (!note || facts.length < 2) return;
  let last = facts.findIndex((fact) => !fact.hidden);
  note.addEventListener("toggle", () => {
    if (!note.open) return;
    let next;
    do { next = Math.floor(Math.random() * facts.length); } while (next === last);
    facts.forEach((fact, i) => { fact.hidden = i !== next; });
    last = next;
  });
})();
