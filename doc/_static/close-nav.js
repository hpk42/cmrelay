// On a phone the sidebar is a menu over the page: a tap on an entry closes it,
// also when the entry is a section of the page already shown.
document.addEventListener("click", (event) => {
  if (event.target.closest(".sidebar-fullnav a")) {
    document.getElementById("__navigation").checked = false;
  }
});
