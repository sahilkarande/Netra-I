// ==========================
// analytics.js - Exam Analytics Dashboard
// ==========================

// Chart color palette
const chartColors = {
    primary: '#6366f1',
    success: '#10b981',
    danger: '#ef4444',
    warning: '#f59e0b',
    info: '#06b6d4',
    purple: '#8b5cf6'
};

// Initialize analytics charts
function initAnalytics(config) {
    createScoreDistribution(config.scores || []);
    createPassFailChart(config.passed, config.failed);
    createTimeAnalysis(config.times || [], config.examDuration);
    createTimelineChart(config.submissions || []);
}

// ==========================
// Chart: Score Distribution
// ==========================
function createScoreDistribution(scores) {
    const ctx = document.getElementById('scoreDistChart');
    if (!ctx) return;

    const ranges = [0, 0, 0, 0, 0]; // 0–20, 20–40, 40–60, 60–80, 80–100
    scores.forEach(s => {
        if (s <= 20) ranges[0]++;
        else if (s <= 40) ranges[1]++;
        else if (s <= 60) ranges[2]++;
        else if (s <= 80) ranges[3]++;
        else ranges[4]++;
    });

    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['0–20%', '20–40%', '40–60%', '60–80%', '80–100%'],
            datasets: [{
                label: 'Number of Students',
                data: ranges,
                backgroundColor: [
                    'rgba(239,68,68,0.8)',
                    'rgba(245,158,11,0.8)',
                    'rgba(99,102,241,0.8)',
                    'rgba(6,182,212,0.8)',
                    'rgba(16,185,129,0.8)'
                ],
                borderRadius: 8,
                borderSkipped: false
            }]
        },
        options: chartBaseOptions()
    });
}

// ==========================
// Chart: Pass/Fail Ratio
// ==========================
function createPassFailChart(passed, failed) {
    const ctx = document.getElementById('passFailChart');
    if (!ctx) return;

    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Passed', 'Failed'],
            datasets: [{
                data: [passed, failed],
                backgroundColor: [
                    'rgba(16,185,129,0.8)',
                    'rgba(239,68,68,0.8)'
                ],
                borderColor: '#1e1e2a',
                borderWidth: 3,
                hoverOffset: 15
            }]
        },
        options: {
            ...chartBaseOptions(),
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        padding: 20,
                        font: { size: 13, weight: '600' },
                        color: '#e5e7eb',
                        usePointStyle: true,
                        pointStyle: 'circle'
                    }
                }
            }
        }
    });
}

// ==========================
// Chart: Time Analysis
// ==========================
function createTimeAnalysis(times, examDuration) {
    const ctx = document.getElementById('timeAnalysisChart');
    if (!ctx) return;

    const ranges = [0, 0, 0, 0, 0];
    const interval = examDuration / 5;

    times.forEach(t => {
        const index = Math.min(Math.floor(t / interval), 4);
        ranges[index]++;
    });

    new Chart(ctx, {
        type: 'radar',
        data: {
            labels: [
                `0–${interval.toFixed(0)}m`,
                `${interval.toFixed(0)}–${(interval * 2).toFixed(0)}m`,
                `${(interval * 2).toFixed(0)}–${(interval * 3).toFixed(0)}m`,
                `${(interval * 3).toFixed(0)}–${(interval * 4).toFixed(0)}m`,
                `${(interval * 4).toFixed(0)}m+`
            ],
            datasets: [{
                label: 'Students',
                data: ranges,
                borderColor: chartColors.primary,
                backgroundColor: 'rgba(99,102,241,0.2)',
                borderWidth: 2,
                pointRadius: 5,
                pointBackgroundColor: chartColors.primary
            }]
        },
        options: chartBaseOptions()
    });
}

// ==========================
// Chart: Submission Timeline
// ==========================
function createTimelineChart(submissions) {
    const ctx = document.getElementById('timelineChart');
    if (!ctx) return;

    const daily = {};
    submissions.forEach(date => {
        const day = date.split(' ')[0];
        daily[day] = (daily[day] || 0) + 1;
    });

    const sorted = Object.keys(daily).sort();
    const counts = sorted.map(d => daily[d]);

    new Chart(ctx, {
        type: 'line',
        data: {
            labels: sorted.map(d =>
                new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
            ),
            datasets: [{
                label: 'Submissions',
                data: counts,
                borderColor: chartColors.info,
                backgroundColor: 'rgba(6,182,212,0.1)',
                borderWidth: 3,
                fill: true,
                tension: 0.4,
                pointRadius: 6,
                pointBackgroundColor: chartColors.info
            }]
        },
        options: chartBaseOptions()
    });
}

// ==========================
// Utility Functions
// ==========================
function chartBaseOptions() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: 'rgba(17,24,39,0.95)',
                padding: 12,
                cornerRadius: 8
            }
        },
        scales: {
            y: {
                beginAtZero: true,
                ticks: { color: '#a1a1aa' },
                grid: { color: '#2a2a3a' }
            },
            x: {
                ticks: { color: '#a1a1aa' },
                grid: { display: false }
            }
        }
    };
}

// Chart download
function downloadChart(chartId) {
    const canvas = document.getElementById(chartId);
    const link = document.createElement('a');
    link.download = `${chartId}-${Date.now()}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
}

// Review flagged student (placeholder)
function reviewStudent(id) {
    alert(`Reviewing flagged student exam #${id}`);
}
