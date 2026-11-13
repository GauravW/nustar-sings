// Toggle the sidebar on narrow screens. Links and forms handle navigation.
const menuButton = document.querySelector('#menu-button');
const sidebar = document.querySelector('#sidebar');

menuButton.addEventListener('click', function () {
    const isOpen = sidebar.classList.toggle('open');
    menuButton.setAttribute('aria-expanded', String(isOpen));
});

// Show a readable fallback when a product image cannot be loaded.
document.querySelectorAll('.signal-image').forEach(image => {
    function unavailable() {
        const link = image.closest('a');
        if (link) link.hidden = true;
        else image.hidden = true;
        const fallback = (link || image).nextElementSibling;
        if (fallback && fallback.classList.contains('image-unavailable')) fallback.hidden = false;
    }
    image.addEventListener('error', unavailable);
    if (image.complete && image.naturalWidth === 0) unavailable();
});

// Shared reviewer fields on Scanning and Details. Server validation also applies.
document.querySelectorAll('.reviewer-control').forEach(control => {
    const select = control.querySelector('select');
    const label = control.querySelector('.custom-reviewer');
    const input = label.querySelector('input');
    function updateReviewer() {
        const custom = select.value === 'other';
        label.hidden = !custom;
        input.required = custom;
        input.disabled = !custom;
    }
    select.addEventListener('change', updateReviewer);
    updateReviewer();
});


// Native dialog keeps keyboard focus inside the popup and supports Escape.
const imagePopup = document.querySelector('#image-popup');
const popupImage = document.querySelector('#image-popup-image');
const popupError = document.querySelector('#image-popup-error');
let imageOpener;
document.addEventListener('click', event => {
    const link = event.target.closest('a[data-image-popup]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    imageOpener = link;
    const title = link.dataset.imageTitle || link.querySelector('img')?.alt || 'Product image';
    document.querySelector('#image-popup-title').textContent = title;
    document.querySelector('#image-popup-full').href = link.href;
    popupError.hidden = true;
    popupImage.hidden = false;
    popupImage.alt = title;
    popupImage.src = link.href;
    imagePopup.showModal();
    document.body.classList.add('image-popup-open');
});
popupImage.addEventListener('error', () => { popupImage.hidden = true; popupError.hidden = false; });
document.querySelector('#image-popup-close').addEventListener('click', () => imagePopup.close());
imagePopup.addEventListener('click', event => {
    if (event.target !== imagePopup) return;
    const bounds = imagePopup.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) imagePopup.close();
});
imagePopup.addEventListener('close', () => {
    document.body.classList.remove('image-popup-open');
    popupImage.removeAttribute('src');
    imageOpener?.focus();
});
// Unrecognized report dimensions fall back to the full report thumbnail.
document.querySelectorAll('.report-preview img').forEach(image => {
    function checkLayout() {
        if (image.naturalWidth && (image.naturalWidth !== 1200 || image.naturalHeight !== 1200)) {
            image.parentElement.classList.remove('report-preview', 'report-solar', 'report-saa');
            image.alt = 'Combined report; panel preview unavailable';
        }
    }
    image.addEventListener('load', checkLayout);
    if (image.complete) checkLayout();
});
