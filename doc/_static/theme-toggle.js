// Two colour schemes, not furo's three: the toggle flips between light and dark,
// and a first visit starts in the one the system prefers.
const dark = () => matchMedia("(prefers-color-scheme: dark)").matches;
document.addEventListener("DOMContentLoaded", () => {
  if (!["light", "dark"].includes(document.body.dataset.theme)) {
    document.body.dataset.theme = dark() ? "dark" : "light";
  }
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".theme-toggle")) return;
  event.stopPropagation();
  const theme = document.body.dataset.theme === "dark" ? "light" : "dark";
  document.body.dataset.theme = theme;
  localStorage.setItem("theme", theme);
}, true);
