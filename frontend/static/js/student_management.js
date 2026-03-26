// ============= IMPORT MODAL FUNCTIONS =============

/**
 * Open import modal
 */
function openImportModal() {
    const modal = document.getElementById('importModal');
    if (modal) {
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        console.log('Import modal opened');
        
        // Show PRN validation warning
        setTimeout(() => showPRNValidationWarning(), 100);
    } else {
        console.error('Import modal not found!');
    }
}

/**
 * Close import modal
 */
function closeImportModal() {
    const modal = document.getElementById('importModal');
    if (modal) {
        modal.style.display = 'none';
        document.body.style.overflow = 'auto';
        clearFile();
        console.log('Import modal closed');
    }
}

/**
 * Handle file selection
 */
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    console.log('File selected:', file.name, file.size, file.type);
    
    // Validate file size (5MB max)
    const maxSize = 5 * 1024 * 1024; // 5MB
    if (file.size > maxSize) {
        showAlert('File size exceeds 5MB limit. Please upload a smaller file.', 'error');
        clearFile();
        return;
    }
    
    // Validate file type
    const validTypes = ['.csv', '.xlsx', '.xls', '.json'];
    const fileName = file.name.toLowerCase();
    const isValid = validTypes.some(type => fileName.endsWith(type));
    
    if (!isValid) {
        showAlert('Invalid file type. Please upload CSV, Excel, or JSON file.', 'error');

        clearFile();
        return;
    }
    
    // Show file preview with validation warning
    const uploadZone = document.getElementById('uploadZone');
    const filePreview = document.getElementById('filePreview');
    const fileNameEl = document.getElementById('fileName');
    const fileSizeEl = document.getElementById('fileSize');
    const uploadBtn = document.getElementById('uploadBtn');
    
    if (uploadZone) uploadZone.style.display = 'none';
    if (filePreview) filePreview.style.display = 'flex';
    if (fileNameEl) fileNameEl.textContent = file.name;
    if (fileSizeEl) fileSizeEl.textContent = formatFileSize(file.size);
    if (uploadBtn) uploadBtn.disabled = false;
    
    showAlert('File selected! Remember: PRN must be exactly 12 digits.', 'success');
}

/**
 * Clear selected file
 */
function clearFile() {
    const fileInput = document.getElementById('csvFile');
    const uploadZone = document.getElementById('uploadZone');
    const filePreview = document.getElementById('filePreview');
    const uploadBtn = document.getElementById('uploadBtn');
    
    if (fileInput) fileInput.value = '';
    if (uploadZone) uploadZone.style.display = 'flex';
    if (filePreview) filePreview.style.display = 'none';
    if (uploadBtn) uploadBtn.disabled = true;
}

/**
 * Format file size
 */
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

/**
 * Toggle format guide
 */
function toggleFormatGuide() {
    const guide = document.getElementById('formatGuide');
    if (guide) {
        if (guide.style.display === 'none' || !guide.style.display) {
            guide.style.display = 'block';
        } else {
            guide.style.display = 'none';
        }
    }
}

/**
 * Validate PRN number - must be exactly 12 digits
 */
function validatePRN(prn) {
    if (!prn) return true; // Empty is allowed
    
    // Remove spaces and decimal points
    const cleanPRN = prn.toString().replace(/\s/g, '').replace(/\./g, '');
    
    // Check if it's exactly 12 digits
    if (!/^\d{12}$/.test(cleanPRN)) {
        return false;
    }
    
    return true;
}

/**
 * Clean and format PRN/Roll number (remove decimals)
 */
function cleanNumber(value) {
    if (!value) return '';
    
    // Convert to string and remove decimal points and spaces
    let cleaned = value.toString().replace(/\s/g, '').replace(/\./g, '');
    
    // Remove any non-digit characters
    cleaned = cleaned.replace(/\D/g, '');
    
    return cleaned;
}

/**
 * Format PRN for display (add spacing for readability)
 */
function formatPRNDisplay(prn) {
    if (!prn) return '';
    
    const cleaned = cleanNumber(prn);
    
    // Format as XXX-XXX-XXX-XXX for 12 digit PRN
    if (cleaned.length === 12) {
        return cleaned.replace(/(\d{3})(\d{3})(\d{3})(\d{3})/, '$1-$2-$3-$4');
    }
    
    return cleaned;
}

// ============= SELECTION MANAGEMENT =============

/**
 * Toggle all checkboxes
 */
function toggleSelectAll() {
    const selectAllCheckbox = document.getElementById('selectAll');
    const checkboxes = document.querySelectorAll('.student-checkbox');
    
    if (selectAllCheckbox) {
        const isChecked = selectAllCheckbox.checked;
        checkboxes.forEach(checkbox => {
            checkbox.checked = isChecked;
        });
        updateSelectionCount();
    }
}

/**
 * Update selection counter
 */
function updateSelectionCount() {
    const selectedCount = document.querySelectorAll('.student-checkbox:checked').length;
    const totalCount = document.querySelectorAll('.student-checkbox').length;
    const countElement = document.getElementById('selectionCount');
    
    if (countElement) {
        if (selectedCount > 0) {
            countElement.textContent = `${selectedCount} of ${totalCount} selected`;
            countElement.style.color = '#3b82f6';
            countElement.style.fontWeight = '600';
        } else {
            countElement.textContent = '0 selected';
            countElement.style.color = '#6b7280';
            countElement.style.fontWeight = '400';
        }
    }
    
    // Update select all checkbox state
    const selectAllCheckbox = document.getElementById('selectAll');
    if (selectAllCheckbox && totalCount > 0) {
        selectAllCheckbox.checked = selectedCount === totalCount;
        selectAllCheckbox.indeterminate = selectedCount > 0 && selectedCount < totalCount;
    }
}

/**
 * Get array of selected student IDs
 */
function getSelectedStudentIds() {
    return Array.from(document.querySelectorAll('.student-checkbox:checked'))
        .map(checkbox => parseInt(checkbox.value))
        .filter(id => !isNaN(id));
}

// ============= EXPORT FUNCTIONS =============

/**
 * Export all students with current filters applied
 */
function exportStudents() {
    const params = new URLSearchParams(window.location.search);
    const exportUrl = '/faculty/export_students?' + params.toString();
    
    showAlert('Preparing export...', 'info');
    window.location.href = exportUrl;
    
    setTimeout(() => {
        showAlert('Export complete! Check your downloads.', 'success');
    }, 1000);
}

/**
 * Export only selected students
 */
function exportSelected() {
    const selectedIds = getSelectedStudentIds();
    
    if (selectedIds.length === 0) {
        showAlert('Please select at least one student to export', 'warning');
        return;
    }
    
    const params = new URLSearchParams();
    selectedIds.forEach(id => params.append('ids', id));
    
    const exportUrl = '/faculty/export_students?' + params.toString();
    
    showAlert(`Exporting ${selectedIds.length} student(s)...`, 'info');
    window.location.href = exportUrl;
    
    setTimeout(() => {
        showAlert(`Successfully exported ${selectedIds.length} student(s)!`, 'success');
    }, 1000);
}

// ============= DELETE FUNCTIONS =============

/**
 * Delete selected students
 */
function deleteSelected() {
    const selectedIds = getSelectedStudentIds();
    
    if (selectedIds.length === 0) {
        showAlert('Please select at least one student to delete', 'warning');
        return;
    }
    
    if (!confirm(`Are you sure you want to delete ${selectedIds.length} student(s)? This action cannot be undone.`)) {
        return;
    }
    
    showAlert('Deleting students...', 'info');
    
    fetch('/faculty/delete_students', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ids: selectedIds })
    })
    .then(response => {
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            showAlert(`Successfully deleted ${selectedIds.length} student(s)`, 'success');
            setTimeout(() => window.location.reload(), 1500);
        } else {
            showAlert('Error: ' + (data.error || 'Failed to delete students'), 'error');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showAlert('Network error. Please try again.', 'error');
    });
}

/**
 * Delete single student
 */
function deleteStudent(studentId) {
    if (!confirm('Are you sure you want to delete this student? This action cannot be undone.')) {
        return;
    }
    
    showAlert('Deleting student...', 'info');
    
    fetch(`/faculty/delete_student/${studentId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => {
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            showAlert('Student deleted successfully', 'success');
            setTimeout(() => window.location.reload(), 1500);
        } else {
            showAlert('Error: ' + (data.error || 'Failed to delete student'), 'error');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showAlert('Network error. Please try again.', 'error');
    });
}

// ============= HELPER FUNCTIONS =============

/**
 * Show alert message
 */
function showAlert(message, type = 'info') {
    // Remove existing alerts
    const existingAlerts = document.querySelectorAll('.custom-alert');
    existingAlerts.forEach(alert => alert.remove());
    
    // Create new alert
    const alert = document.createElement('div');
    alert.className = `custom-alert alert-${type}`;
    alert.innerHTML = `
        <div class="alert-content">
            <i class="bi bi-${getAlertIcon(type)}"></i>
            <span>${message}</span>
        </div>
    `;
    
    // Add CSS for alerts if not exists
    if (!document.getElementById('alert-styles')) {
        const style = document.createElement('style');
        style.id = 'alert-styles';
        style.textContent = `
            .custom-alert {
                position: fixed;
                top: 20px;
                right: 20px;
                padding: 16px 24px;
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                z-index: 10000;
                display: flex;
                align-items: center;
                gap: 12px;
                animation: slideIn 0.3s ease;
                max-width: 400px;
            }
            .alert-success { background: #10b981; color: white; }
            .alert-error { background: #ef4444; color: white; }
            .alert-warning { background: #f59e0b; color: white; }
            .alert-info { background: #3b82f6; color: white; }
            .alert-content { display: flex; align-items: center; gap: 10px; }
            .alert-content i { font-size: 20px; }
            @keyframes slideIn {
                from { transform: translateX(400px); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            .fade-out {
                animation: fadeOut 0.3s ease;
            }
            @keyframes fadeOut {
                from { opacity: 1; }
                to { opacity: 0; }
            }
            .prn-validation-banner {
                background: linear-gradient(135deg, #fef3c7, #fde68a);
                border: 2px solid #f59e0b;
                border-radius: 8px;
                padding: 12px 16px;
                margin-bottom: 16px;
                display: flex;
                align-items: center;
                gap: 12px;
                font-size: 13px;
                color: #92400e;
                font-weight: 500;
            }
            .prn-validation-banner i {
                font-size: 18px;
                color: #f59e0b;
            }
        `;
        document.head.appendChild(style);
    }
    
    document.body.appendChild(alert);
    
    // Auto-remove after 3 seconds
    setTimeout(() => {
        alert.classList.add('fade-out');
        setTimeout(() => alert.remove(), 300);
    }, 3000);
}

/**
 * Show PRN validation warning in modal
 */
function showPRNValidationWarning() {
    const modalBody = document.querySelector('.modal-body-custom');
    
    if (!modalBody) return;
    
    // Check if warning already exists
    if (document.querySelector('.prn-validation-banner')) return;
    
    const warning = document.createElement('div');


    
    // Insert at the beginning of modal body
    modalBody.insertBefore(warning, modalBody.firstChild);
}

/**
 * Get icon for alert type
 */
function getAlertIcon(type) {
    const icons = {
        'success': 'check-circle-fill',
        'error': 'x-circle-fill',
        'warning': 'exclamation-triangle-fill',
        'info': 'info-circle-fill'
    };
    return icons[type] || 'info-circle-fill';
}

// ============= FILTER FUNCTIONS =============

/**
 * Toggle advanced filters
 */
function toggleAdvancedFilters() {
    const filters = document.getElementById('advancedFilters');
    const icon = document.getElementById('filterToggleIcon');
    const text = document.getElementById('filterToggleText');
    
    if (filters) {
        if (filters.style.display === 'none' || !filters.style.display) {
            filters.style.display = 'block';
            if (icon) icon.style.transform = 'rotate(180deg)';
            if (text) text.textContent = 'Hide Advanced';
        } else {
            filters.style.display = 'none';
            if (icon) icon.style.transform = 'rotate(0deg)';
            if (text) text.textContent = 'Show Advanced';
        }
    }
}

/**
 * Clear search input
 */
function clearSearch() {
    const searchInput = document.querySelector('input[name="q"]');
    if (searchInput) {
        searchInput.value = '';
        document.getElementById('filterForm').submit();
    }
}

/**
 * Clear all filters
 */
function clearFilters() {
    window.location.href = window.location.pathname;
}

// ============= INITIALIZATION =============

/**
 * Validate import form before submission
 */
function validateImportForm(event) {
    const fileInput = document.getElementById('csvFile');
    
    if (!fileInput || !fileInput.files || !fileInput.files[0]) {
        event.preventDefault();
        showAlert('Please select a file to upload', 'error');
        return false;
    }
    
    const file = fileInput.files[0];
    const fileName = file.name.toLowerCase();
    
    // Check file type
    if (!fileName.endsWith('.csv') && !fileName.endsWith('.xlsx') && !fileName.endsWith('.xls') && !fileName.endsWith('.json')) {
        event.preventDefault();
        showAlert('Invalid file type. Please upload CSV, Excel, or JSON file.', 'error');

        return false;
    }
    
    // Check file size
    const maxSize = 5 * 1024 * 1024; // 5MB
    if (file.size > maxSize) {
        event.preventDefault();
        showAlert('File size exceeds 5MB limit.', 'error');
        return false;
    }
    
    showAlert('Uploading file... Please wait.', 'info');
    return true;
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('Student management page loaded');
    
    // Check if modal exists
    const modal = document.getElementById('importModal');
    if (modal) {
        console.log('Import modal found in DOM');
        
        // Stop clicks inside the modal dialog from bubbling to the backdrop
        const modalDialog = modal.querySelector('.modal-dialog');
        if (modalDialog) {
            modalDialog.addEventListener('click', function(event) {
                event.stopPropagation();
            });
        }
        
        // Close modal only when clicking the backdrop itself
        modal.addEventListener('click', function(event) {
            if (event.target === modal) {
                closeImportModal();
            }
        });
    } else {
        console.error('Import modal NOT found in DOM!');
    }
    
    // Add form validation
    const importForm = document.getElementById('importForm');
    if (importForm) {
        importForm.addEventListener('submit', validateImportForm);
        console.log('Import form validation attached');
    }
    
    // Attach change listeners to all checkboxes
    document.querySelectorAll('.student-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', updateSelectionCount);
    });
    
    // Initial count update
    updateSelectionCount();
    
    // Log PRN validation info
    console.log('PRN Validation Rules:');
    console.log('- Must be exactly 12 digits');
    console.log('- No letters or special characters');
    console.log('- Decimals will be automatically removed');
    console.log('- Example: 250840325012');
});

// Close modal with Escape key
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
        const modal = document.getElementById('importModal');
        if (modal && modal.style.display === 'flex') {
            closeImportModal();
        }
    }
});

// Prevent form submission on Enter key in search
document.addEventListener('DOMContentLoaded', function() {
    const searchInput = document.querySelector('input[name="q"]');
    if (searchInput) {
        searchInput.addEventListener('keypress', function(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                document.getElementById('filterForm').submit();
            }
        });
    }
});

console.log('student_management.js loaded successfully');