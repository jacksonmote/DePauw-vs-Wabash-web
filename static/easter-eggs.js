(() => {
  const button = document.getElementById("tiger-entrance");
  const fallback = document.getElementById("entrance-fallback");
  const status = document.getElementById("entrance-status");
  const wrapper = document.querySelector(".entrance-wrap");
  if (!button || !fallback || !status || !wrapper) return;
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
  fallback.hidden = true;
  button.hidden = false;
})();
