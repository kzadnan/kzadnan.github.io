const nav = document.getElementById("site-nav");
const toggle = document.querySelector(".nav-toggle");
const year = document.getElementById("year");
const filters = document.querySelectorAll(".filter");
const pubs = document.querySelectorAll(".pub");

if (year) {
  year.textContent = String(new Date().getFullYear());
}

if (toggle && nav) {
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("is-open");
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  });

  nav.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => {
      nav.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
    });
  });
}

filters.forEach((button) => {
  button.addEventListener("click", () => {
    filters.forEach((item) => item.classList.remove("is-active"));
    button.classList.add("is-active");
    const mode = button.dataset.filter;
    pubs.forEach((pub) => {
      const show = mode === "all" || pub.dataset.role === "first";
      pub.classList.toggle("is-hidden", !show);
    });
  });
});
