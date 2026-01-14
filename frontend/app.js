/**
 * Sift Frontend Application
 *
 * Handles:
 * - File upload with progress tracking
 * - Background job polling
 * - Results visualization
 * - Page navigation
 */

// ============================================================================
// CONFIGURATION
// ============================================================================

const API_BASE = '/';
const POLL_INTERVAL = 2000; // 2 seconds
const UPLOAD_TIMEOUT = 300000; // 5 minutes

// Global state
let currentJobIds = {
    parse: null,
    enrich: null,
    aggregate: null
};

let pollIntervals = {
    parse: null,
    enrich: null,
    aggregate: null
};

let aggregatedData = {
    projects: [],
    stakeholders: []
};

let selectedFile = null;  // Store the currently selected file

// ============================================================================
// INITIALIZATION
// ============================================================================

document.addEventListener('DOMContentLoaded', async () => {
    setupEventListeners();
    await checkBackendStatus();
    loadModels();
    loadPSTFiles();  // Load list of previously uploaded PST files
});

function setupEventListeners() {
    // Upload zone
    const uploadZone = document.getElementById('upload-zone');
    uploadZone.addEventListener('click', () => document.getElementById('file-input').click());
    uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadZone.classList.add('drag-over');
    });
    uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
    uploadZone.addEventListener('drop', handleFileDrop);

    document.getElementById('file-input').addEventListener('change', handleFileSelect);

    // Update value displays
    document.getElementById('min-messages').addEventListener('input', (e) => {
        updateValue('min-messages-value', e.target.value);
    });
    document.getElementById('batch-size').addEventListener('input', (e) => {
        updateValue('batch-value', e.target.value);
    });
}

// ============================================================================
// FILE UPLOAD HANDLING
// ============================================================================

function handleFileDrop(e) {
    e.preventDefault();
    document.getElementById('upload-zone').classList.remove('drag-over');

    const files = e.dataTransfer.files;
    if (files.length > 0) {
        const file = files[0];
        selectedFile = file;  // Store the file globally
        displayFileInfo(file);
        showUploadButton();
    }
}

function handleFileSelect(e) {
    const files = e.target.files;
    if (files.length > 0) {
        const file = files[0];
        selectedFile = file;  // Store the file globally
        displayFileInfo(file);
        showUploadButton();
    }
}

function displayFileInfo(file) {
    const fileInfo = document.getElementById('file-info');
    const fileName = document.getElementById('file-name');
    const fileSize = document.getElementById('file-size');

    fileName.textContent = file.name;
    fileSize.textContent = formatBytes(file.size);

    fileInfo.style.display = 'flex';
    document.getElementById('upload-zone').style.display = 'none';
    document.getElementById('config-section').style.display = 'block';
}

function clearFile() {
    selectedFile = null;  // Clear the stored file
    document.getElementById('file-input').value = '';
    document.getElementById('file-info').style.display = 'none';
    document.getElementById('upload-zone').style.display = 'block';
    document.getElementById('config-section').style.display = 'none';
    document.getElementById('upload-btn').style.display = 'none';
    document.getElementById('upload-btn').textContent = 'Upload & Parse';  // Reset button text
}

function showUploadButton() {
    document.getElementById('upload-btn').style.display = 'inline-block';
}

async function startUpload() {
    if (!selectedFile) {
        showError('upload-error', 'No file selected');
        return;
    }

    const file = selectedFile;

    const uploadBtn = document.getElementById('upload-btn');
    const uploadProgress = document.getElementById('upload-progress');
    const uploadError = document.getElementById('upload-error');

    uploadBtn.disabled = true;
    uploadError.style.display = 'none';
    uploadProgress.style.display = 'block';

    try {
        let filename;

        // Check if this is an existing file or a new upload
        if (file.isExisting) {
            // Existing file - skip upload
            filename = file.name;
            document.getElementById('upload-percent').textContent = '100%';
            updateProgressBar('upload-bar', 100);
        } else {
            // New file - upload it
            const uploadResult = await uploadFile(file);
            filename = uploadResult.filename;
        }

        // Start parsing
        const dateStart = document.getElementById('date-start').value;
        const dateEnd = document.getElementById('date-end').value;
        const minMessages = parseInt(document.getElementById('min-messages').value);
        const maxMessages = document.getElementById('max-messages').value
            ? parseInt(document.getElementById('max-messages').value)
            : null;

        const parseResult = await apiCall('POST', '/parse', {
            pst_filename: filename,
            date_start: dateStart,
            date_end: dateEnd,
            min_conversation_messages: minMessages,
            max_messages: maxMessages
        });

        currentJobIds.parse = parseResult.job_id;
        goToPipeline();
        startPolling('parse');

    } catch (error) {
        showError('upload-error', error.message);
        uploadBtn.disabled = false;
    }
}

async function uploadFile(file) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        const formData = new FormData();
        formData.append('file', file);

        // Progress tracking
        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) {
                const percent = (e.loaded / e.total) * 100;
                updateProgressBar('upload-bar', percent);
                document.getElementById('upload-percent').textContent = Math.round(percent) + '%';
            }
        });

        xhr.addEventListener('load', () => {
            if (xhr.status >= 200 && xhr.status < 300) {
                try {
                    const result = JSON.parse(xhr.responseText);
                    resolve(result);
                } catch (e) {
                    reject(new Error('Invalid upload response'));
                }
            } else {
                const error = JSON.parse(xhr.responseText);
                reject(new Error(error.detail || 'Upload failed'));
            }
        });

        xhr.addEventListener('error', () => reject(new Error('Upload failed')));
        xhr.addEventListener('timeout', () => reject(new Error('Upload timeout')));

        xhr.timeout = UPLOAD_TIMEOUT;
        xhr.open('POST', API_BASE + 'upload');
        xhr.send(formData);
    });
}

// ============================================================================
// API CALLS
// ============================================================================

async function apiCall(method, endpoint, data = null) {
    const options = {
        method: method,
        headers: { 'Content-Type': 'application/json' }
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    const response = await fetch(API_BASE + endpoint.substring(1), options);

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || `API error: ${response.status}`);
    }

    return await response.json();
}

// ============================================================================
// BACKEND STATUS
// ============================================================================

async function checkBackendStatus() {
    try {
        const response = await fetch(API_BASE);
        const data = await response.json();
        const statusBadge = document.getElementById('backend-status');
        statusBadge.className = 'status-badge completed';
        statusBadge.textContent = '✓ Connected';
        document.getElementById('footer-version').textContent = `v${data.version}`;
    } catch (error) {
        const statusBadge = document.getElementById('backend-status');
        statusBadge.className = 'status-badge failed';
        statusBadge.textContent = '✗ Offline';
    }
}

// ============================================================================
// JOB POLLING
// ============================================================================

function startPolling(jobType) {
    // Clear existing interval if any
    if (pollIntervals[jobType]) {
        clearInterval(pollIntervals[jobType]);
    }

    // Poll immediately, then every POLL_INTERVAL
    pollJob(jobType);
    pollIntervals[jobType] = setInterval(() => pollJob(jobType), POLL_INTERVAL);
}

async function pollJob(jobType) {
    const jobId = currentJobIds[jobType];
    if (!jobId) return;

    try {
        let statusEndpoint;
        switch (jobType) {
            case 'parse':
                statusEndpoint = `/status/${jobId}`;
                break;
            case 'enrich':
                statusEndpoint = `/enrich/${jobId}/status`;
                break;
            case 'aggregate':
                statusEndpoint = `/aggregate/${jobId}/status`;
                break;
        }

        const status = await apiCall('GET', statusEndpoint);
        updateJobCard(jobType, status);

        // Stop polling when complete or failed
        if (status.status === 'completed' || status.status === 'failed') {
            clearInterval(pollIntervals[jobType]);

            if (status.status === 'completed') {
                handleJobCompletion(jobType, status);
            }
        }

    } catch (error) {
        console.error(`Poll error for ${jobType}:`, error);
    }
}

function updateJobCard(jobType, status) {
    const card = document.getElementById(`${jobType}-card`);
    if (!card) return;

    const statusBadge = card.querySelector('.status-badge');
    const progressFill = card.querySelector('.progress-fill');
    const info = card.querySelector('.job-info');
    const errorDiv = card.querySelector('.alert-danger');

    // Update status badge
    statusBadge.className = `status-badge ${status.status}`;
    statusBadge.textContent = status.status.charAt(0).toUpperCase() + status.status.slice(1);

    // Update progress bar
    updateProgressBar(progressFill, status.progress_percent);

    // Update info text
    const processed = status.processed_messages || 0;
    const total = status.total_messages || 0;
    info.textContent = `${processed}/${total} messages processed`;

    // Update workflow step icon
    const workflowStep = document.getElementById(`${jobType}-step`);
    if (workflowStep) {
        workflowStep.className = `workflow-step ${status.status}`;
    }

    // Handle errors
    if (status.error) {
        errorDiv.style.display = 'block';
        errorDiv.textContent = status.error;
    } else {
        errorDiv.style.display = 'none';
    }

    // Show start button for next phase when current completes
    if (status.status === 'completed') {
        if (jobType === 'parse') {
            document.getElementById('start-enrich-btn').style.display = 'inline-block';
            document.getElementById('enrich-config').style.display = 'block';
        } else if (jobType === 'enrich') {
            document.getElementById('start-aggregate-btn').style.display = 'inline-block';
        }
    }

    // Show/hide cancel button based on job status
    const cancelBtn = document.getElementById(`cancel-${jobType}-btn`);
    if (cancelBtn) {
        // Show cancel button only when job is actively processing
        if (status.status === 'processing' || status.status === 'queued') {
            cancelBtn.style.display = 'inline-block';
        } else {
            cancelBtn.style.display = 'none';
        }
    }

    // Stop polling if job is complete, failed, or cancelled
    if (status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled') {
        if (pollIntervals[jobType]) {
            clearInterval(pollIntervals[jobType]);
            pollIntervals[jobType] = null;
        }
    }
}

function handleJobCompletion(jobType, status) {
    if (jobType === 'parse') {
        // Parse complete, ready for enrichment
    } else if (jobType === 'enrich') {
        // Enrich complete, ready for aggregation
    } else if (jobType === 'aggregate') {
        // Aggregation complete, show results
        loadResults();
        document.getElementById('results-btn').style.display = 'inline-block';
    }
}

// ============================================================================
// ENRICHMENT & AGGREGATION CONTROL
// ============================================================================

async function startEnrichment() {
    try {
        const batchSize = parseInt(document.getElementById('batch-size').value);

        const enrichResult = await apiCall('POST', '/enrich', {
            batch_size: batchSize
        });

        currentJobIds.enrich = enrichResult.job_id;
        document.getElementById('start-enrich-btn').style.display = 'none';
        startPolling('enrich');

    } catch (error) {
        showError('enrich-error', error.message);
    }
}

async function startAggregation() {
    try {
        const aggregateResult = await apiCall('POST', '/aggregate', {
            output_formats: ['json']
        });

        currentJobIds.aggregate = aggregateResult.job_id;
        document.getElementById('start-aggregate-btn').style.display = 'none';
        startPolling('aggregate');

    } catch (error) {
        showError('aggregate-error', error.message);
    }
}

async function cancelJob(jobType) {
    const jobTypeMap = { parse: 'parse', enrich: 'enrich', aggregate: 'aggregate' };
    const jobId = currentJobIds[jobTypeMap[jobType]];

    if (!jobId) {
        showError(`${jobType}-error`, `No ${jobType} job to cancel`);
        return;
    }

    try {
        await apiCall('POST', `/jobs/${jobId}/cancel`);
        showError(`${jobType}-error`, `${jobType.charAt(0).toUpperCase() + jobType.slice(1)} cancelled by user`);
        // The job status will update to "cancelled" on next poll
    } catch (error) {
        showError(`${jobType}-error`, `Failed to cancel: ${error.message}`);
    }
}

async function loadModels() {
    try {
        const result = await apiCall('GET', '/models');
        const select = document.getElementById('model-select');

        select.innerHTML = '';
        result.available_models.forEach(model => {
            const option = document.createElement('option');
            option.value = model.name;
            option.textContent = `${model.name} (${model.size_gb ? model.size_gb.toFixed(1) : '?'}GB)`;
            if (model.name === result.current_model) {
                option.selected = true;
            }
            select.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading models:', error);
    }
}

async function switchModel() {
    const modelName = document.getElementById('model-select').value;
    try {
        await apiCall('POST', `/models/${modelName}`, {});
    } catch (error) {
        console.error('Error switching model:', error);
    }
}

// ============================================================================
// RESULTS LOADING & DISPLAY
// ============================================================================

async function loadResults() {
    try {
        // Load stats
        const stats = await apiCall('GET', '/stats');
        document.getElementById('stat-messages').textContent = stats.database.messages;
        document.getElementById('stat-conversations').textContent = stats.database.conversations;

        // Load aggregated data
        const projects = await fetch(API_BASE + 'reports/aggregated_projects.json');
        const stakeholders = await fetch(API_BASE + 'reports/aggregated_stakeholders.json');

        if (projects.ok) {
            aggregatedData.projects = (await projects.json()).projects;
            document.getElementById('stat-projects').textContent = aggregatedData.projects.length;
            displayProjects();
        }

        if (stakeholders.ok) {
            aggregatedData.stakeholders = (await stakeholders.json()).stakeholders;
            document.getElementById('stat-stakeholders').textContent = aggregatedData.stakeholders.length;
            displayStakeholders();
        }

    } catch (error) {
        console.error('Error loading results:', error);
    }
}

function displayProjects() {
    const tbody = document.getElementById('projects-tbody');
    tbody.innerHTML = '';

    aggregatedData.projects.forEach(project => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td><strong>${project.canonical_name}</strong></td>
            <td>${project.total_mentions}</td>
            <td>${formatConfidence(project.avg_confidence)}</td>
            <td>${project.importance_tier || 'N/A'}</td>
            <td>${project.stakeholders.length}</td>
        `;
        tbody.appendChild(row);
    });

    if (aggregatedData.projects.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="loading-message">No projects found</td></tr>';
    }
}

function displayStakeholders() {
    const tbody = document.getElementById('stakeholders-tbody');
    tbody.innerHTML = '';

    aggregatedData.stakeholders.forEach(person => {
        const row = document.createElement('tr');
        const role = person.primary_role || 'Unknown';
        row.innerHTML = `
            <td><strong>${person.name}</strong></td>
            <td>${person.email}</td>
            <td>${role}</td>
            <td>${person.projects.length}</td>
            <td>${person.message_count}</td>
        `;
        tbody.appendChild(row);
    });

    if (aggregatedData.stakeholders.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="loading-message">No stakeholders found</td></tr>';
    }
}

// ============================================================================
// REPORT DOWNLOADS
// ============================================================================

async function downloadReport(filename) {
    try {
        const response = await fetch(`${API_BASE}reports/${filename}`);
        if (!response.ok) {
            throw new Error(`File not found: ${filename}`);
        }

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);

    } catch (error) {
        alert(`Error downloading: ${error.message}`);
    }
}

// ============================================================================
// PAGE NAVIGATION
// ============================================================================

function goToUpload() {
    showPage('upload-page');
    resetUploadPage();
    loadPSTFiles();
}

function resetUploadPage() {
    // Clear all UI state
    selectedFile = null;
    document.getElementById('file-input').value = '';
    document.getElementById('file-info').style.display = 'none';
    document.getElementById('upload-zone').style.display = 'block';
    document.getElementById('config-section').style.display = 'none';
    document.getElementById('upload-btn').style.display = 'none';
    document.getElementById('upload-progress').style.display = 'none';
    document.getElementById('upload-error').style.display = 'none';
    document.getElementById('upload-bar').style.width = '0%';
    document.getElementById('upload-percent').textContent = '0%';
}

async function loadPSTFiles() {
    try {
        const result = await apiCall('GET', '/pst-files');
        const filesList = result.files || [];

        const existingFilesSection = document.getElementById('existing-files-section');
        const existingFilesList = document.getElementById('existing-files-list');

        if (filesList.length === 0) {
            existingFilesSection.style.display = 'none';
            return;
        }

        // Show the section
        existingFilesSection.style.display = 'block';

        // Clear existing items
        existingFilesList.innerHTML = '';

        // Add each file as a selectable item
        filesList.forEach(file => {
            const fileItem = document.createElement('div');
            fileItem.className = 'file-item';
            fileItem.innerHTML = `
                <div class="file-item-info">
                    <div class="file-item-name">${file.filename}</div>
                    <div class="file-item-meta">${file.size_mb} MB • Uploaded ${new Date(file.uploaded_at).toLocaleDateString()}</div>
                </div>
                <div class="file-item-action">
                    <button class="btn btn-secondary btn-small" onclick="selectExistingFile('${file.filename}')">
                        Select
                    </button>
                </div>
            `;
            existingFilesList.appendChild(fileItem);
        });

    } catch (error) {
        console.error('Failed to load PST files:', error);
        // Silently fail - don't show error if files can't be loaded
    }
}

function selectExistingFile(filename) {
    // Create a synthetic file object to mimic uploaded file
    // We'll store the filename and mark it as existing
    selectedFile = {
        name: filename,
        isExisting: true
    };

    // Display file info
    const fileInfo = document.getElementById('file-info');
    document.getElementById('file-name').textContent = filename;
    document.getElementById('file-size').textContent = '(existing file)';
    fileInfo.style.display = 'block';
    document.getElementById('upload-zone').style.display = 'none';
    document.getElementById('config-section').style.display = 'block';
    document.getElementById('upload-btn').textContent = 'Parse Selected File';
    document.getElementById('upload-btn').style.display = 'inline-block';
}

function goToPipeline() {
    showPage('pipeline-page');
}

function goToResults() {
    showPage('results-page');
}

function showPage(pageId) {
    // Hide all pages
    document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));

    // Show selected page
    document.getElementById(pageId).classList.add('active');

    // Scroll to top
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ============================================================================
// TAB SWITCHING
// ============================================================================

function switchTab(tabId) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => tab.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));

    // Show selected tab
    document.getElementById(tabId).classList.add('active');

    // Mark button as active
    event.target.classList.add('active');
}

// ============================================================================
// TABLE SORTING
// ============================================================================

function sortTable(tableId, columnIndex) {
    const table = document.getElementById(tableId);
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));

    // Skip loading message row
    if (rows[0]?.querySelector('.loading-message')) {
        return;
    }

    rows.sort((a, b) => {
        const aValue = a.cells[columnIndex].textContent;
        const bValue = b.cells[columnIndex].textContent;

        // Try numeric sort first
        const aNum = parseFloat(aValue);
        const bNum = parseFloat(bValue);

        if (!isNaN(aNum) && !isNaN(bNum)) {
            return aNum - bNum;
        }

        // String sort
        return aValue.localeCompare(bValue);
    });

    // Clear and repopulate tbody
    tbody.innerHTML = '';
    rows.forEach(row => tbody.appendChild(row));
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

function updateProgressBar(elementOrId, percent) {
    const element = typeof elementOrId === 'string'
        ? document.getElementById(elementOrId)
        : elementOrId;

    if (element) {
        element.style.width = Math.min(100, percent) + '%';
    }
}

function updateValue(elementId, value) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = value;
    }
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';

    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));

    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

function formatConfidence(confidence) {
    if (typeof confidence === 'number') {
        return (confidence * 100).toFixed(0) + '%';
    }
    return 'N/A';
}

function showError(elementId, message) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = message;
        element.style.display = 'block';
    }
}
