/* The only hand-written JS in the application.
   Three jobs, none of them business logic:
     - open the drawer / modal once htmx has filled it,
     - close them on Escape, on the scrim, or on a close button,
     - show a toast when the server asks for one via HX-Trigger.
   Every URL behind these also renders as an ordinary page, so nothing here is
   load-bearing. */
(function () {
  var drawer = null, scrim = null, modal = null, toast = null, toastTimer = null;

  function ready() {
    drawer = document.getElementById('drawer');
    scrim = document.getElementById('scrim');
    modal = document.getElementById('modal');
    toast = document.getElementById('toast');
  }

  function anyOpen() {
    return (drawer && drawer.classList.contains('open')) || (modal && modal.classList.contains('open'));
  }

  function openOverlay(el) {
    el.classList.add('open');
    if (el === drawer) drawer.setAttribute('aria-hidden', 'false');
    scrim.classList.add('open');
  }

  function closeOverlays() {
    if (modal && modal.classList.contains('open')) {
      modal.classList.remove('open');
      document.getElementById('modal-body').innerHTML = '';
      if (drawer && drawer.classList.contains('open')) return;   // drawer stays behind the modal
    }
    if (drawer) {
      drawer.classList.remove('open');
      drawer.setAttribute('aria-hidden', 'true');
    }
    scrim.classList.remove('open');
  }

  function showToast(message) {
    if (!toast || !message) return;
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove('show'); }, 2200);
  }

  document.addEventListener('DOMContentLoaded', function () {
    ready();
    // A deep link that landed on the board with a design in the URL.
    if (drawer && drawer.querySelector('.drawer-inner')) openOverlay(drawer);
  });

  document.body.addEventListener('htmx:afterSwap', function (event) {
    if (!drawer) ready();
    if (event.target.id === 'drawer-body') openOverlay(drawer);
    if (event.target.id === 'modal-body') openOverlay(modal);
  });

  document.body.addEventListener('toast', function (event) {
    showToast(event.detail ? (event.detail.value || event.detail) : '');
  });

  // A view that finished the job the modal was opened for asks it to close.
  // Not load-bearing: without JS the same POST redirects instead.
  document.body.addEventListener('overlay-close', function () {
    closeOverlays();
  });

  document.addEventListener('click', function (event) {
    if (event.target.closest('[data-close-overlays]')) {
      event.preventDefault();
      closeOverlays();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && anyOpen()) closeOverlays();
  });
})();
