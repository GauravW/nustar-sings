// Counts are calculated by Flask using the same filters as Scanning.
const canvas = document.querySelector('#classification-chart');
if (canvas && window.Chart) {
    const items = JSON.parse(document.querySelector('#chart-data').textContent);
    new Chart(canvas, {
        type: 'doughnut',
        data: {
            labels: items.map(item => item.label),
            datasets: [{ data: items.map(item => item.count),
                backgroundColor: items.map(item => item.color), borderWidth: 2, borderColor: '#ffffff' }]
        },
        options: {
            responsive: true, maintainAspectRatio: false, cutout: '68%', animation: false,
            plugins: { legend: { display: false }, tooltip: { callbacks: {
                label(context) {
                    const total = items.reduce((sum, item) => sum + item.count, 0);
                    return `${context.label}: ${context.raw} (${(context.raw / total * 100).toFixed(1)}%)`;
                }
            }}},
            onClick(event, elements) {
                if (elements.length) window.location.assign(items[elements[0].index].url);
            },
            onHover(event, elements) { canvas.style.cursor = elements.length ? 'pointer' : 'default'; }
        }
    });
}
