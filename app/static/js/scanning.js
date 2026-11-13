// Selection is deliberately local to this page; navigation resets it.
const form = document.querySelector('#bulk-form');
const selectPage = document.querySelector('#select-page');
const boxes = [...document.querySelectorAll('.row-checkbox')];
function updateSelection() {
    const selected = boxes.filter(box => box.checked);
    document.querySelector('#selection-count').textContent = `${selected.length} selected`;
    selectPage.checked = boxes.length > 0 && selected.length === boxes.length;
    selectPage.indeterminate = selected.length > 0 && selected.length < boxes.length;
    selectPage.disabled = boxes.length === 0;
    document.querySelectorAll('.bulk-action').forEach(button => button.disabled = selected.length === 0);
    boxes.forEach(box => box.closest('tr').classList.toggle('selected-row', box.checked));
}
selectPage.addEventListener('change', () => {
    boxes.forEach(box => box.checked = selectPage.checked);
    updateSelection();
});
boxes.forEach(box => box.addEventListener('change', updateSelection));
form.addEventListener('submit', event => {
    const count = boxes.filter(box => box.checked).length;
    const tag = event.submitter?.value === 'discard' ? 'Discard' : document.querySelector('#bulk-tag').selectedOptions[0].textContent;
    if (!count || !window.confirm(`Assign ${count} selected candidate(s) to ${tag}? Each change will be recorded in history.`)) {
        event.preventDefault();
    }
});
updateSelection();
