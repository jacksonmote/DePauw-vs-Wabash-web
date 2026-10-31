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
