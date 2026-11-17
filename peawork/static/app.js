(function () {
  "use strict";

  // Sidebar (mobile)
  document.addEventListener("click", function (event) {
    const toggle = event.target.closest("[data-sidebar-toggle]");
    if (toggle) {
      const sidebar = document.getElementById("sidebar");
      if (!sidebar) return;
      const open = sidebar.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", String(open));
      return;
    }

    const dismiss = event.target.closest("[data-dismiss]");
    if (dismiss) {
      const alert = dismiss.closest(".alert");
      if (alert) alert.remove();
      return;
    }

    // Close sidebar when clicking outside on mobile.
    const sidebar = document.getElementById("sidebar");
    if (
      sidebar &&
      sidebar.classList.contains("is-open") &&
      !event.target.closest("#sidebar") &&
      !event.target.closest("[data-sidebar-toggle]")
    ) {
      sidebar.classList.remove("is-open");
      const toggleButton = document.querySelector("[data-sidebar-toggle]");
      if (toggleButton) toggleButton.setAttribute("aria-expanded", "false");
    }
  });

  // Confirm destructive actions.
  document.addEventListener("submit", function (event) {
    const form = event.target;
    const message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  // Auto-dismiss success alerts after 6 seconds (errors stay).
  window.setTimeout(function () {
    document.querySelectorAll(".alert--success").forEach(function (alert) {
      alert.remove();
    });
  }, 6000);
})();
