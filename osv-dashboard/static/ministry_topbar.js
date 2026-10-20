(() => {
  const menu = document.getElementById("user-menu");
  const toggle = document.getElementById("user-toggle");
  if (!menu || !toggle) return;

  const setOpen = open => {
    menu.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", String(open));
  };

  toggle.addEventListener("click", () => setOpen(!menu.classList.contains("open")));
  document.addEventListener("click", event => {
    if (!menu.contains(event.target)) setOpen(false);
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && menu.classList.contains("open")) {
      setOpen(false);
      toggle.focus();
    }
  });
})();
