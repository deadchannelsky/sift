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
const UPLOAD_TIMEOUT = 1200000; // 20 minutes (for large 1-2GB files)

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
    await checkPipelineResume();  // Check if we can resume from previous stage

    // Set end date to today
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('date-end').value = today;
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
    // Handle existing files that don't have size info
    fileSize.textContent = file.isExisting ? '(existing file)' : formatBytes(file.size);

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
        const clearDatabase = document.getElementById('clear-database').checked;
        const relevanceThreshold = parseInt(document.getElementById('relevance-threshold').value) / 100;

        const parseResult = await apiCall('POST', '/parse', {
            pst_filename: filename,
            date_start: dateStart,
            date_end: dateEnd,
            min_conversation_messages: minMessages,
            max_messages: maxMessages,
            clear_database: clearDatabase,
            relevance_threshold: relevanceThreshold
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

        const startTime = Date.now();
        let lastProgressTime = startTime;
        let lastProgressBytes = 0;

        // Progress tracking
        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) {
                const percent = (e.loaded / e.total) * 100;
                updateProgressBar('upload-bar', percent);

                // Calculate upload speed
                const currentTime = Date.now();
                const timeDiff = (currentTime - lastProgressTime) / 1000; // seconds
                const bytesDiff = e.loaded - lastProgressBytes;

                if (timeDiff > 2) {  // Update every 2 seconds
                    const speedMBps = (bytesDiff / (1024 * 1024)) / timeDiff;
                    const remainingBytes = e.total - e.loaded;
                    const remainingSeconds = remainingBytes / (speedMBps * 1024 * 1024);
                    const remainingMinutes = Math.ceil(remainingSeconds / 60);

                    const status = `${Math.round(percent)}% (${(e.loaded / (1024**3)).toFixed(2)}GB / ${(e.total / (1024**3)).toFixed(2)}GB) - ${speedMBps.toFixed(1)} MB/s - ETA: ${remainingMinutes}m`;
                    document.getElementById('upload-percent').textContent = status;
                    console.log('Upload progress:', status);

                    lastProgressTime = currentTime;
                    lastProgressBytes = e.loaded;
                }
            }
        });

        xhr.addEventListener('loadstart', () => {
            const fileSizeGB = (file.size / (1024**3)).toFixed(2);
            console.log('Upload started for:', file.name, 'Size:', fileSizeGB + 'GB', '(' + file.size + ' bytes)');
        });

        xhr.addEventListener('load', () => {
            console.log('Upload completed with status:', xhr.status);

            if (xhr.status >= 200 && xhr.status < 300) {
                try {
                    const result = JSON.parse(xhr.responseText);
                    console.log('Upload successful:', result);
                    resolve(result);
                } catch (e) {
                    console.error('Failed to parse upload response:', e, 'Response:', xhr.responseText);
                    reject(new Error('Invalid upload response: ' + e.message));
                }
            } else {
                try {
                    const error = JSON.parse(xhr.responseText);
                    console.error('Upload failed with status', xhr.status, ':', error);
                    reject(new Error(error.detail || 'Upload failed: ' + xhr.status));
                } catch (e) {
                    console.error('Upload failed, could not parse error response:', xhr.responseText);
                    reject(new Error('Upload failed with status ' + xhr.status));
                }
            }
        });

        xhr.addEventListener('error', (e) => {
            console.error('Upload error event:', e);
            reject(new Error('Upload network error'));
        });

        xhr.addEventListener('abort', (e) => {
            console.error('Upload aborted:', e);
            reject(new Error('Upload was aborted'));
        });

        xhr.addEventListener('timeout', () => {
            console.error('Upload timeout after', UPLOAD_TIMEOUT, 'ms');
            reject(new Error('Upload timeout'));
        });

        xhr.timeout = UPLOAD_TIMEOUT;
        xhr.open('POST', API_BASE + 'upload');
        console.log('Sending upload to:', API_BASE + 'upload');
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
            // Show Data Inspector button (available after parsing)
            document.getElementById('open-inspector-btn').style.display = 'inline-block';
        } else if (jobType === 'enrich') {
            document.getElementById('start-aggregate-btn').style.display = 'inline-block';
            // Also show RAG embedding generation button
            document.getElementById('start-rag-embed-btn').style.display = 'inline-block';
            document.getElementById('rag-status').className = 'status-badge pending';
            document.getElementById('rag-status').textContent = 'Ready';
            document.getElementById('rag-info').textContent = 'Click "Generate Embeddings" to enable semantic search';
            // Show REPL Explorer button (code-based exploration)
            document.getElementById('open-repl-btn').style.display = 'inline-block';
            document.getElementById('repl-status').className = 'status-badge';
            document.getElementById('repl-status').style.background = '#d1fae5';
            document.getElementById('repl-status').style.color = '#065f46';
            document.getElementById('repl-status').textContent = 'Ready';
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

        // If aggregation failed, show retry section on results page
        if (status.status === 'failed' || status.error) {
            console.error('Aggregation failed:', status.error);
            goToResults();
            showError('aggregation-error', 'Aggregation failed: ' + (status.error || 'Unknown error'));
            document.getElementById('aggregation-retry-section').style.display = 'block';
        }
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

        // Update model indicators with current model
        if (result.current_model) {
            const displayName = result.current_model.split('/').pop().split(':')[0];
            document.getElementById('parse-model-name').textContent = displayName;
            document.getElementById('enrich-model-name').textContent = displayName;
            document.getElementById('post-filter-model-name').textContent = displayName;
        }

    } catch (error) {
        console.error('Error loading models:', error);
    }
}

async function switchModel() {
    const modelSelect = document.getElementById('model-select');
    const modelName = modelSelect.value;

    if (!modelName) {
        console.warn('No model selected');
        return;
    }

    try {
        // Show visual feedback that model is switching
        modelSelect.disabled = true;
        console.log('Switching to model:', modelName);

        // URL-encode model name to handle slashes and colons (e.g., hf.co/org/model:variant)
        const encodedModelName = encodeURIComponent(modelName);
        const response = await apiCall('POST', `/models/${encodedModelName}`, {});
        console.log('✅ Model switched successfully:', response);

        // Extract short model name for display (e.g., "granite-4.0-h-tiny-GGUF" from full path)
        const modelDisplayName = modelName.split('/').pop().split(':')[0];

        // Update all model indicator elements on the page
        document.getElementById('parse-model-name').textContent = modelDisplayName;
        document.getElementById('enrich-model-name').textContent = modelDisplayName;
        document.getElementById('post-filter-model-name').textContent = modelDisplayName;

        // Show success notification in header
        const statusBadge = document.getElementById('backend-status');
        if (statusBadge) {
            const oldText = statusBadge.textContent;
            statusBadge.textContent = `✓ Model: ${modelDisplayName}`;
            statusBadge.className = 'status-badge success';
            setTimeout(() => {
                statusBadge.textContent = oldText;
                statusBadge.className = 'status-badge connected';
            }, 3000);
        }

        modelSelect.disabled = false;

    } catch (error) {
        console.error('❌ Error switching model:', error);
        alert('Failed to switch model: ' + error.message);
        modelSelect.disabled = false;
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

        let projectsLoaded = false;
        let stakeholdersLoaded = false;

        if (projects.ok) {
            aggregatedData.projects = (await projects.json()).projects;
            document.getElementById('stat-projects').textContent = aggregatedData.projects.length;
            displayProjects();
            projectsLoaded = true;
        }

        if (stakeholders.ok) {
            const stakeholderData = await stakeholders.json();
            aggregatedData.stakeholders = stakeholderData.stakeholders;
            document.getElementById('stat-stakeholders').textContent = aggregatedData.stakeholders.length;
            displayStakeholders();
            stakeholdersLoaded = true;

            // Show filtering stats if available
            const stats = stakeholderData.stats;
            if (stats && stats.filtered_out > 0) {
                console.log(`Stakeholder filtering: ${stats.filtered_out} removed (${stats.total_before_filtering} total)`);
            }
        }

        // Show retry section and post-filter section if aggregation is complete (even if partial)
        if (projectsLoaded || stakeholdersLoaded) {
            document.getElementById('aggregation-retry-section').style.display = 'block';
            document.getElementById('post-filter-section').style.display = 'block';
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
    document.getElementById('upload-btn').textContent = 'Upload & Parse';  // Reset button text
    document.getElementById('upload-progress').style.display = 'none';
    document.getElementById('upload-error').style.display = 'none';
    document.getElementById('upload-bar').style.width = '0%';
    document.getElementById('upload-percent').textContent = '0%';

    // Reset date fields
    document.getElementById('date-start').value = '2020-01-01';
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('date-end').value = today;

    // Reset clear database checkbox
    document.getElementById('clear-database').checked = false;

    // Reset relevance threshold slider
    document.getElementById('relevance-threshold').value = 80;
    document.getElementById('threshold-value').textContent = 80;
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

async function checkPipelineResume() {
    // Check if pipeline can be resumed from a previous stage
    try {
        const resumeStatus = await apiCall('GET', '/pipeline/resume');

        if (resumeStatus.can_resume && resumeStatus.stage) {
            console.log('Pipeline resumable at stage:', resumeStatus.stage);

            // Show resume section prominently at top
            const resumeSection = document.getElementById('pipeline-resume-section');
            if (resumeSection) {
                resumeSection.style.display = 'block';
                document.getElementById('resume-message').textContent = resumeStatus.message;
                document.getElementById('resume-stage').value = resumeStatus.stage;

                // Show stats with better formatting
                const stats = resumeStatus.stats;
                if (stats) {
                    const statsText = `📊 ${stats.total_messages} messages • ${stats.conversations} conversations • ${stats.completed_enrichment}/${stats.total_messages} enriched`;
                    document.getElementById('resume-stats').textContent = statsText;
                }

                // Hide upload zone since they should resume first
                document.getElementById('upload-zone').style.display = 'none';
                document.getElementById('existing-files-section').style.display = 'none';
                document.getElementById('config-section').style.display = 'none';
                document.getElementById('upload-btn').style.display = 'none';

                // Show warning about enrichment loss if they choose fresh start
                if (stats && stats.completed_enrichment > 0) {
                    document.getElementById('existing-enrichment-warning').style.display = 'block';
                }
            }
        }
    } catch (error) {
        console.error('Error checking pipeline resume status:', error);
    }
}

function toggleResumeOptions() {
    // Toggle between resume section and new analysis confirmation
    const resumeSection = document.getElementById('pipeline-resume-section');
    const confirmSection = document.getElementById('new-analysis-confirm');
    const uploadZone = document.getElementById('upload-zone');
    const existingFiles = document.getElementById('existing-files-section');
    const configSection = document.getElementById('config-section');
    const uploadBtn = document.getElementById('upload-btn');

    if (resumeSection.style.display === 'none') {
        // Show resume, hide confirmation and upload
        resumeSection.style.display = 'block';
        confirmSection.style.display = 'none';
        uploadZone.style.display = 'none';
        existingFiles.style.display = 'none';
        configSection.style.display = 'none';
        uploadBtn.style.display = 'none';
    } else {
        // Show upload, hide resume and confirmation
        resumeSection.style.display = 'none';
        confirmSection.style.display = 'block';
        uploadZone.style.display = 'block';
        existingFiles.style.display = existingFiles.innerHTML ? 'block' : 'none';
        configSection.style.display = 'block';
        uploadBtn.style.display = 'inline-block';
        clearFile(); // Reset upload form
    }
}

function resumePipeline(stage) {
    // Jump into pipeline at specified stage
    if (stage === 'enrich') {
        // Start enrichment directly
        console.log('Resuming at enrichment stage...');
        currentJobIds.parse = 'resume-mode';
        goToPipeline();
        startEnrichment();
    } else if (stage === 'aggregate') {
        // Start aggregation directly - but first show RAG and REPL buttons since enrichment is complete
        console.log('Resuming at aggregation stage...');
        goToPipeline();

        // Show aggregate, RAG, and REPL buttons (enrichment already done)
        document.getElementById('start-aggregate-btn').style.display = 'inline-block';
        document.getElementById('start-rag-embed-btn').style.display = 'inline-block';
        document.getElementById('rag-status').className = 'status-badge pending';
        document.getElementById('rag-status').textContent = 'Ready';
        document.getElementById('rag-info').textContent = 'Click "Generate Embeddings" to enable semantic search';

        // Show REPL button (code-based exploration)
        document.getElementById('open-repl-btn').style.display = 'inline-block';
        document.getElementById('repl-status').className = 'status-badge';
        document.getElementById('repl-status').style.background = '#d1fae5';
        document.getElementById('repl-status').style.color = '#065f46';
        document.getElementById('repl-status').textContent = 'Ready';

        // User must manually choose between "Start Aggregation", "Generate Embeddings", or "REPL"
    }
}

function selectExistingFile(filename) {
    // Create synthetic file object for existing file
    selectedFile = {
        name: filename,
        size: 0,  // Size unknown (already on server)
        isExisting: true
    };

    // Display file info and show config section
    displayFileInfo(selectedFile);

    // Show parse button with updated text
    document.getElementById('upload-btn').textContent = 'Parse Selected File';
    document.getElementById('upload-btn').style.display = 'inline-block';
}

async function parseSelectedFile(filename) {
    // Get configuration from form
    const dateStart = document.getElementById('date-start').value;
    const dateEnd = document.getElementById('date-end').value;
    const minMessages = parseInt(document.getElementById('min-messages').value);
    const maxMessages = document.getElementById('max-messages').value
        ? parseInt(document.getElementById('max-messages').value)
        : null;

    try {
        // Start parsing
        const clearDatabase = document.getElementById('clear-database').checked;
        const relevanceThreshold = parseInt(document.getElementById('relevance-threshold').value) / 100;

        const parseResult = await apiCall('POST', '/parse', {
            pst_filename: filename,
            date_start: dateStart,
            date_end: dateEnd,
            min_conversation_messages: minMessages,
            max_messages: maxMessages,
            clear_database: clearDatabase,
            relevance_threshold: relevanceThreshold
        });

        currentJobIds.parse = parseResult.job_id;
        goToPipeline();
        startPolling('parse');

    } catch (error) {
        showError('upload-error', `Failed to parse: ${error.message}`);
    }
}

// ============================================================================
// AGGREGATION SETTINGS FUNCTIONS
// ============================================================================

function updateAggregationValue(elementId, value) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = parseFloat(value).toFixed(2);
    }
}

function switchAggregationTab(tabName) {
    // Hide all tab panes
    document.querySelectorAll('.agg-tab-pane').forEach(pane => {
        pane.style.display = 'none';
    });

    // Show selected tab pane
    const selectedPane = document.getElementById(`agg-tab-${tabName}`);
    if (selectedPane) {
        selectedPane.style.display = 'block';
    }

    // Update tab button styling
    document.querySelectorAll('.agg-tab-btn').forEach(btn => {
        if (btn.getAttribute('data-tab') === tabName) {
            btn.classList.add('active');
            btn.style.borderBottom = '3px solid #2563eb';
            btn.style.color = '#2563eb';
        } else {
            btn.classList.remove('active');
            btn.style.borderBottom = '3px solid transparent';
            btn.style.color = '#666';
        }
    });
}

function toggleDeduplicationSlider() {
    const dedupCheckbox = document.getElementById('agg-dedup-enable');
    const similarityInput = document.getElementById('agg-similarity');
    const similarityGroup = similarityInput.parentElement.parentElement;

    if (dedupCheckbox.checked) {
        similarityGroup.style.opacity = '1';
        similarityInput.disabled = false;
    } else {
        similarityGroup.style.opacity = '0.5';
        similarityInput.disabled = true;
    }
}

function getAggregationSettings() {
    return {
        min_role_confidence: parseFloat(document.getElementById('agg-min-conf').value),
        min_mention_count: parseInt(document.getElementById('agg-min-mentions').value),
        exclude_generic_names: document.getElementById('agg-exclude-generic').checked,
        enable_name_deduplication: document.getElementById('agg-dedup-enable').checked,
        name_similarity_threshold: parseFloat(document.getElementById('agg-similarity').value),
        validate_email_domains: document.getElementById('agg-validate-domains').checked,
        enable_diagnostics: document.getElementById('agg-enable-diags').checked
    };
}

async function resetAggregationSettings() {
    try {
        console.log('Fetching aggregation defaults...');
        const response = await fetch('/config/aggregation-defaults');

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const defaults = await response.json();
        console.log('Aggregation defaults loaded:', defaults);

        // Populate form with defaults
        document.getElementById('agg-min-conf').value = defaults.min_role_confidence;
        document.getElementById('agg-conf-value').textContent = defaults.min_role_confidence.toFixed(2);

        document.getElementById('agg-min-mentions').value = defaults.min_mention_count;
        document.getElementById('agg-mentions-value').textContent = defaults.min_mention_count;

        document.getElementById('agg-exclude-generic').checked = defaults.exclude_generic_names;
        document.getElementById('agg-validate-domains').checked = defaults.validate_email_domains;

        document.getElementById('agg-dedup-enable').checked = defaults.enable_name_deduplication;
        document.getElementById('agg-similarity').value = defaults.name_similarity_threshold;
        document.getElementById('agg-similarity-value').textContent = defaults.name_similarity_threshold.toFixed(2);

        document.getElementById('agg-enable-diags').checked = defaults.enable_diagnostics;

        // Update UI state
        toggleDeduplicationSlider();

        console.log('Aggregation settings reset to defaults');

    } catch (error) {
        console.error('Error resetting aggregation settings:', error);
        alert('Failed to load default settings: ' + error.message);
    }
}

async function retryAggregation() {
    const retryBtn = event.target;
    retryBtn.disabled = true;
    retryBtn.textContent = 'Re-running...';

    try {
        console.log('Retrying aggregation with custom settings...');

        // Collect settings from form
        const settings = getAggregationSettings();
        console.log('Aggregation settings:', settings);

        const result = await apiCall('POST', '/aggregate', {
            output_formats: ["json"],
            aggregation_settings: settings
        });

        currentJobIds.aggregate = result.job_id;
        console.log('Aggregation retry started:', result.job_id);

        // Close retry section and start polling
        document.getElementById('aggregation-retry-section').style.display = 'none';
        document.getElementById('aggregation-error').style.display = 'none';

        // Show pipeline with polling
        goToPipeline();
        startPolling('aggregate');

    } catch (error) {
        console.error('Error retrying aggregation:', error);
        showError('aggregation-error', 'Failed to retry aggregation: ' + error.message);
        retryBtn.disabled = false;
        retryBtn.textContent = 'Re-run Aggregation with New Settings';
    }
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

// ============================================================================
// POST-AGGREGATION FILTER FUNCTIONS
// ============================================================================

let postFilterJobId = null;
let postFilterPollInterval = null;

/**
 * Toggle visibility of post-filter section and show results section
 */
function togglePostFilterSection() {
    const section = document.getElementById('post-filter-section');
    if (section.style.display === 'none') {
        section.style.display = 'block';
    } else {
        section.style.display = 'none';
    }
}

/**
 * Update displayed threshold value in real-time
 */
function updateFilterThresholdDisplay(value) {
    const displayElement = document.getElementById('post-filter-threshold-value');
    if (displayElement) {
        const numValue = parseFloat(value);
        displayElement.textContent = numValue.toFixed(2);
    }
}

/**
 * Start the post-aggregation filter job
 */
async function startPostAggregationFilter() {
    const roleTextarea = document.getElementById('post-filter-role');
    const roleDescription = roleTextarea.value.trim();
    const threshold = parseFloat(document.getElementById('post-filter-threshold').value);

    if (!roleDescription) {
        showError('post-filter-error', 'Please describe your role and responsibilities');
        return;
    }

    // Disable button and show progress
    const btn = document.getElementById('start-post-filter-btn');
    btn.disabled = true;
    btn.textContent = 'Starting filter...';

    // Hide results/errors and show progress
    document.getElementById('post-filter-results').style.display = 'none';
    document.getElementById('post-filter-error').style.display = 'none';
    document.getElementById('post-filter-progress').style.display = 'block';

    try {
        const result = await apiCall('POST', '/post-aggregate-filter', {
            role_description: roleDescription,
            confidence_threshold: threshold
        });

        postFilterJobId = result.job_id;
        console.log('Post-aggregation filter started:', postFilterJobId);

        // Start polling for progress
        pollPostAggregationFilterStatus();

    } catch (error) {
        console.error('Error starting post-aggregation filter:', error);
        showError('post-filter-error', 'Failed to start filter: ' + error.message);
        btn.disabled = false;
        btn.textContent = 'Run Quality Filter';
        document.getElementById('post-filter-progress').style.display = 'none';
    }
}

/**
 * Poll for post-aggregation filter job status
 */
function pollPostAggregationFilterStatus() {
    if (!postFilterJobId) return;

    // Clear any existing interval
    if (postFilterPollInterval) {
        clearInterval(postFilterPollInterval);
    }

    // Immediately check status, then set interval
    checkPostAggregationFilterStatus();
    postFilterPollInterval = setInterval(checkPostAggregationFilterStatus, POLL_INTERVAL);
}

/**
 * Check status of post-aggregation filter job
 */
async function checkPostAggregationFilterStatus() {
    if (!postFilterJobId) return;

    try {
        const response = await apiCall('GET', `/post-aggregate-filter/${postFilterJobId}/status`);
        const status = response.status || 'processing';
        const stats = response.stats || {};

        // Update progress bar
        const progressPercent = stats.progress_percent || 0;
        document.getElementById('post-filter-progress-bar').style.width = progressPercent + '%';
        document.getElementById('post-filter-progress-text').textContent = progressPercent + '%';

        // Update progress message
        if (stats.projects_analyzed !== undefined) {
            document.getElementById('post-filter-progress-message').textContent =
                `Evaluated: ${stats.projects_analyzed} / ${stats.projects_total || '?'} projects`;
        }

        // Check if done
        if (status === 'completed' || status === 'failed') {
            clearInterval(postFilterPollInterval);
            postFilterPollInterval = null;

            if (status === 'completed') {
                displayPostFilterResults();
            } else {
                showError('post-filter-error', 'Filter job failed: ' + (response.error || 'Unknown error'));
                document.getElementById('post-filter-progress').style.display = 'none';
            }

            // Re-enable button
            const btn = document.getElementById('start-post-filter-btn');
            btn.disabled = false;
            btn.textContent = 'Run Quality Filter';
        }

    } catch (error) {
        console.error('Error checking post-filter status:', error);
    }
}

/**
 * Display post-aggregation filter results
 */
async function displayPostFilterResults() {
    try {
        // Fetch results
        const response = await apiCall('GET', '/post-aggregate-filter/results/summary');
        const results = response || {};

        // Hide progress, show results
        document.getElementById('post-filter-progress').style.display = 'none';
        document.getElementById('post-filter-results').style.display = 'block';

        // Update statistics (match backend response keys)
        document.getElementById('post-filter-total').textContent = results.total_projects || 0;
        document.getElementById('post-filter-included').textContent = results.included_projects || 0;
        document.getElementById('post-filter-excluded').textContent = results.excluded_projects || 0;

        const avgConf = results.confidence_avg !== undefined ? results.confidence_avg : 0;
        document.getElementById('post-filter-avg-conf').textContent = (avgConf * 100).toFixed(1) + '%';

        // Display excluded projects
        const excludedList = document.getElementById('post-filter-excluded-list');
        excludedList.innerHTML = '';

        const excludedProjects = results.excluded_projects_preview || [];
        if (excludedProjects.length === 0) {
            excludedList.innerHTML = '<p style="color: #666; font-style: italic;">No projects excluded - all projects passed the confidence threshold.</p>';
        } else {
            excludedProjects.forEach(project => {
                const projectDiv = document.createElement('div');
                projectDiv.style.cssText = 'background: white; padding: 12px; border: 1px solid #e5e7eb; border-radius: 3px; margin-bottom: 10px;';

                const projectName = project.name || 'Unknown';
                const confidence = project.confidence || 0;
                const reasoning = typeof project.reasoning === 'string' ? [project.reasoning] : (project.reasoning || []);

                projectDiv.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 8px;">
                        <h6 style="margin: 0; color: #1f2937; font-weight: 600;">${projectName}</h6>
                        <span style="background: #fee2e2; color: #991b1b; padding: 2px 8px; border-radius: 3px; font-size: 0.85em; font-weight: 600;">
                            ${(confidence * 100).toFixed(1)}% confidence
                        </span>
                    </div>
                    <details style="cursor: pointer;">
                        <summary style="color: #5b21b6; font-weight: 600; user-select: none; padding: 5px 0;">Reasoning Details</summary>
                        <ul style="margin: 8px 0 0 0; padding-left: 20px; color: #666; font-size: 0.9em;">
                            ${reasoning.map(reason => `<li style="margin: 4px 0;">${reason}</li>`).join('')}
                        </ul>
                    </details>
                `;

                excludedList.appendChild(projectDiv);
            });
        }

        console.log('Post-filter results displayed:', results);

    } catch (error) {
        console.error('Error displaying post-filter results:', error);
        showError('post-filter-error', 'Failed to display results: ' + error.message);
    }
}

// ============================================================================
// RAG QUERY FUNCTIONS
// ============================================================================

let ragSessionId = null;
let ragChatHistory = [];

async function startEmbeddingGeneration() {
    try {
        document.getElementById('start-rag-embed-btn').disabled = true;
        document.getElementById('rag-info').textContent = 'Starting embedding generation...';

        const result = await apiCall('POST', '/rag/embeddings/generate');
        const jobId = result.job_id;

        currentJobIds.rag_embed = jobId;

        // Update UI
        document.getElementById('rag-status').className = 'status-badge processing';
        document.getElementById('rag-progress-bar').style.display = 'block';

        // Start polling
        startEmbeddingPolling(jobId);

    } catch (error) {
        console.error('❌ Error starting embedding generation:', error);
        showError('rag-error', 'Failed to start embedding generation: ' + error.message);
        document.getElementById('start-rag-embed-btn').disabled = false;
    }
}

async function startEmbeddingPolling(jobId) {
    const pollInterval = setInterval(async () => {
        try {
            const status = await apiCall('GET', `/rag/embeddings/status/${jobId}`);

            // Update progress
            const progressPercent = status.progress_percent || 0;
            document.getElementById('rag-progress').style.width = progressPercent + '%';
            document.getElementById('rag-info').textContent = `Embedding generation: ${progressPercent}% complete`;

            if (status.status === 'completed') {
                clearInterval(pollInterval);
                document.getElementById('rag-status').className = 'status-badge success';
                document.getElementById('rag-status').textContent = 'Ready';
                document.getElementById('rag-info').textContent = 'Embeddings ready! Click "Start RAG Session" to begin chatting.';
                document.getElementById('open-rag-chat-btn').style.display = 'block';
                document.getElementById('rag-progress-bar').style.display = 'none';
                console.log('✅ Embeddings generated successfully');
            } else if (status.status === 'failed') {
                clearInterval(pollInterval);
                document.getElementById('rag-status').className = 'status-badge error';
                document.getElementById('rag-info').textContent = 'Embedding generation failed.';
                showError('rag-error', 'Embedding generation failed');
            }
        } catch (error) {
            console.error('Error polling embedding status:', error);
        }
    }, POLL_INTERVAL);
}

async function goToRAGChat() {
    try {
        // Create new session
        const result = await apiCall('POST', '/rag/session');
        ragSessionId = result.session_id;
        ragChatHistory = [];

        console.log('✅ Created RAG session:', ragSessionId);

        // Clear messages and show page
        const messagesDiv = document.getElementById('rag-messages');
        messagesDiv.innerHTML = `
            <div class="rag-message assistant">
                <div class="message-avatar">🤖</div>
                <div class="message-content">
                    <p><strong>Session started!</strong></p>
                    <p>Ask me anything about your emails. I'll search and provide answers with citations to specific messages.</p>
                </div>
            </div>
        `;

        // Navigate to RAG page
        showPage('rag-page');
        document.getElementById('rag-query-input').focus();

    } catch (error) {
        console.error('❌ Error starting RAG session:', error);
        alert('Failed to start RAG session: ' + error.message);
    }
}

async function sendRAGQuery() {
    const input = document.getElementById('rag-query-input');
    const query = input.value.trim();

    if (!query) {
        return;
    }

    // Add user message to UI
    addRAGMessage('user', query);
    input.value = '';

    // Show loading
    document.getElementById('rag-loading').style.display = 'block';
    document.getElementById('rag-send-btn').disabled = true;

    try {
        const result = await apiCall('POST', `/rag/${ragSessionId}/query`, {
            query: query,
            chat_history: ragChatHistory
        });

        // Add assistant message with citations
        addRAGMessage('assistant', result.answer, result.citations);

        // Update chat history
        ragChatHistory.push({ role: 'user', content: query });
        ragChatHistory.push({ role: 'assistant', content: result.answer });

        console.log(`✅ RAG query processed (${result.retrieved_count} messages retrieved)`);

    } catch (error) {
        console.error('❌ Error processing RAG query:', error);
        addRAGMessage('assistant', '❌ Error: ' + error.message);
    } finally {
        document.getElementById('rag-loading').style.display = 'none';
        document.getElementById('rag-send-btn').disabled = false;
        document.getElementById('rag-query-input').focus();
    }
}

function addRAGMessage(role, content, citations = null) {
    const messagesDiv = document.getElementById('rag-messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `rag-message ${role}`;

    const avatar = role === 'user' ? '👤' : '🤖';

    let citationsHTML = '';
    if (citations && citations.length > 0) {
        citationsHTML = `
            <div class="citations" style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #e5e7eb;">
                <p style="font-weight: 600; margin-bottom: 10px;">📎 Sources (${citations.length} email${citations.length !== 1 ? 's' : ''}):</p>
                ${citations.map((c, idx) => `
                    <div class="citation-card" style="background: #f3f4f6; padding: 10px 12px; border-radius: 4px; margin-bottom: 8px; cursor: pointer; transition: all 0.2s ease;"
                         onmouseover="this.style.transform='translateX(5px)'; this.style.boxShadow='0 2px 8px rgba(0,0,0,0.1)'"
                         onmouseout="this.style.transform='translateX(0)'; this.style.boxShadow='none'"
                         onclick="expandCitation(${c.message_id})">
                        <strong>[${idx + 1}] ${c.subject || '(no subject)'}</strong>
                        <p style="font-size: 0.9em; color: #666; margin: 5px 0 0 0;">
                            From: ${c.sender} • ${new Date(c.date).toLocaleDateString()}
                        </p>
                    </div>
                `).join('')}
            </div>
        `;
    }

    messageDiv.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-content">
            <p style="white-space: pre-wrap; word-wrap: break-word;">${content}</p>
            ${citationsHTML}
        </div>
    `;

    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;  // Scroll to bottom
}

async function expandCitation(messageId) {
    try {
        const message = await apiCall('GET', `/rag/message/${messageId}`);

        // Format extractions
        let extractionsText = 'No extractions found';
        if (message.extractions && Object.keys(message.extractions).length > 0) {
            extractionsText = JSON.stringify(message.extractions, null, 2);
        }

        // Create modal
        const modal = document.createElement('div');
        modal.className = 'rag-citation-modal';
        modal.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            padding: 20px;
        `;

        const content = document.createElement('div');
        content.style.cssText = `
            background: white;
            padding: 30px;
            border-radius: 8px;
            max-width: 800px;
            max-height: 80vh;
            overflow-y: auto;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
        `;

        const dateStr = new Date(message.date).toLocaleString();
        content.innerHTML = `
            <h3 style="margin: 0 0 15px 0; color: #1f2937;">${message.subject || '(no subject)'}</h3>
            <div style="background: #f3f4f6; padding: 12px; border-radius: 4px; margin-bottom: 15px; font-size: 0.9em;">
                <p style="margin: 0 0 5px 0;"><strong>From:</strong> ${message.sender.name} &lt;${message.sender.email}&gt;</p>
                <p style="margin: 0 0 5px 0;"><strong>Date:</strong> ${dateStr}</p>
                <p style="margin: 0;"><strong>To:</strong> ${message.recipients}</p>
            </div>
            <div style="border-top: 1px solid #e5e7eb; padding-top: 15px; margin-bottom: 15px;">
                <p style="white-space: pre-wrap; word-wrap: break-word; color: #374151; line-height: 1.6;">${message.body}</p>
            </div>
            <div style="background: #f9fafb; padding: 15px; border-radius: 4px; border: 1px solid #e5e7eb;">
                <h4 style="margin: 0 0 10px 0; color: #5b21b6;">Extracted Intelligence:</h4>
                <pre style="background: white; padding: 10px; border-radius: 3px; overflow-x: auto; font-size: 0.85em; color: #666;">${extractionsText}</pre>
            </div>
            <div style="margin-top: 20px; display: flex; gap: 10px; justify-content: flex-end;">
                <button class="btn btn-secondary" onclick="this.closest('.rag-citation-modal').remove()">Close</button>
            </div>
        `;

        modal.appendChild(content);
        modal.onclick = (e) => {
            if (e.target === modal) modal.remove();
        };

        document.body.appendChild(modal);

    } catch (error) {
        console.error('❌ Error loading message:', error);
        alert('Failed to load message: ' + error.message);
    }
}

function clearRAGSession() {
    if (confirm('Clear chat history and start a fresh session?')) {
        goToRAGChat();
    }
}

// ============================================================================
// REPL EXPLORER FUNCTIONS
// ============================================================================

let replSessionId = null;

async function goToREPL() {
    showPage('repl-page');

    // Load models for the model selector
    await loadREPLModels();

    // Create session and load corpus stats
    try {
        const response = await apiCall('POST', '/repl/session');
        if (response.success) {
            replSessionId = response.session_id;

            // Update corpus stats display
            const stats = response.corpus_stats;
            document.getElementById('repl-stat-messages').textContent = stats.total_messages.toLocaleString();
            document.getElementById('repl-stat-dates').textContent = stats.date_range || 'N/A';
            document.getElementById('repl-stat-senders').textContent = stats.unique_senders.toLocaleString();
            document.getElementById('repl-stat-projects').textContent = stats.unique_projects.toLocaleString();

            console.log('✅ REPL session created:', replSessionId);
        }
    } catch (error) {
        console.error('❌ Error creating REPL session:', error);
        alert('Failed to create REPL session: ' + error.message);
    }
}

async function loadREPLModels() {
    try {
        console.log('Loading models for REPL selector...');
        const response = await apiCall('GET', '/models');
        console.log('Models response:', response);

        const models = response.available_models || [];
        const select = document.getElementById('repl-model-select');

        if (!select) {
            console.error('REPL model select element not found!');
            return;
        }

        // Keep the default option, show current model
        const currentModel = response.current_model || 'default';
        select.innerHTML = `<option value="">Use current (${currentModel})</option>`;

        // Add all models
        if (models.length === 0) {
            console.warn('No models in available_models array');
        }

        models.forEach(model => {
            const opt = document.createElement('option');
            opt.value = model.name;
            const sizeStr = model.size_gb ? ` (${model.size_gb.toFixed(1)}GB)` : '';
            opt.textContent = `${model.name}${sizeStr}`;
            select.appendChild(opt);
        });

        console.log(`✅ Loaded ${models.length} models for REPL selector`);
    } catch (error) {
        console.error('❌ Error loading REPL models:', error);
    }
}

async function sendREPLQuery() {
    const input = document.getElementById('repl-query-input');
    const question = input.value.trim();

    if (!question) {
        alert('Please enter a question');
        return;
    }

    if (!replSessionId) {
        alert('No REPL session active. Please refresh the page.');
        return;
    }

    // Get selected model
    const modelSelect = document.getElementById('repl-model-select');
    const model = modelSelect.value || null;

    // Show loading
    document.getElementById('repl-loading').style.display = 'block';
    document.getElementById('repl-send-btn').disabled = true;
    document.getElementById('repl-trace-container').style.display = 'none';
    document.getElementById('repl-answer-container').style.display = 'none';

    try {
        const response = await apiCall('POST', `/repl/${replSessionId}/query`, {
            question: question,
            max_iterations: 3,
            model: model
        });

        if (response.success) {
            // Display the trace
            displayREPLTrace(response.trace, response.model_used);

            // Display the answer
            displayREPLAnswer(response.answer);

            // Show history section
            document.getElementById('repl-history-section').style.display = 'block';

            // Clear input
            input.value = '';
        }
    } catch (error) {
        console.error('❌ REPL query error:', error);
        alert('REPL query failed: ' + error.message);
    } finally {
        document.getElementById('repl-loading').style.display = 'none';
        document.getElementById('repl-send-btn').disabled = false;
    }
}

function displayREPLTrace(trace, modelUsed) {
    const container = document.getElementById('repl-trace-container');
    const stepsDiv = document.getElementById('repl-trace-steps');
    const modelSpan = document.getElementById('repl-trace-model');

    container.style.display = 'block';
    modelSpan.textContent = `Model: ${modelUsed}`;

    stepsDiv.innerHTML = '';

    trace.forEach((step, index) => {
        const stepDiv = document.createElement('div');
        stepDiv.className = 'repl-step';

        const hasError = !!step.error;
        const statusClass = hasError ? 'error' : 'success';
        const statusText = hasError ? 'Error' : 'Executed';

        // Syntax highlight the code (simple version)
        const highlightedCode = highlightPython(step.code);

        // Format the result
        let resultContent;
        if (hasError) {
            resultContent = `<div class="repl-error-content">${escapeHtml(step.error)}</div>`;
        } else {
            const resultStr = typeof step.result === 'object'
                ? JSON.stringify(step.result, null, 2)
                : String(step.result);
            resultContent = `<div class="repl-result-content">${escapeHtml(resultStr)}</div>`;
        }

        stepDiv.innerHTML = `
            <div class="repl-step-header">
                <span class="repl-step-number">Step ${step.step}</span>
                <span class="repl-step-status ${statusClass}">${statusText}</span>
            </div>
            <div class="repl-code-block">${highlightedCode}</div>
            <div class="repl-result-block">
                <div class="repl-result-label">Result</div>
                ${resultContent}
            </div>
        `;

        stepsDiv.appendChild(stepDiv);
    });
}

function highlightPython(code) {
    if (!code) return '';

    // Just escape HTML - syntax highlighting was causing issues
    // (replacing 'class' inside span tags, etc.)
    return escapeHtml(code);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function displayREPLAnswer(answer) {
    const container = document.getElementById('repl-answer-container');
    const content = document.getElementById('repl-answer-content');

    container.style.display = 'block';
    content.textContent = answer;
}

function clearREPLSession() {
    if (confirm('Clear session and start fresh?')) {
        replSessionId = null;
        document.getElementById('repl-trace-container').style.display = 'none';
        document.getElementById('repl-answer-container').style.display = 'none';
        document.getElementById('repl-history-section').style.display = 'none';
        document.getElementById('repl-query-input').value = '';

        // Create new session
        goToREPL();
    }
}

// Update pipeline status to show REPL button when appropriate
function updateREPLButton() {
    // Show REPL button after enrichment is complete
    const enrichStatus = document.getElementById('enrich-status');
    const replBtn = document.getElementById('open-repl-btn');

    if (enrichStatus && replBtn) {
        const statusText = enrichStatus.textContent.toLowerCase();
        if (statusText === 'completed' || statusText === 'complete') {
            replBtn.style.display = 'inline-block';
        }
    }
}

// ============================================================================
// DATA INSPECTOR FUNCTIONS
// ============================================================================

let inspectorCurrentPage = 1;
let inspectorSearchTimeout = null;

async function goToInspector() {
    showPage('inspector-page');
    await loadInspectorStats();
    await loadInspectorMessages();
}

async function loadInspectorStats() {
    try {
        const response = await fetch(`${API_BASE}/inspector/stats`);
        const data = await response.json();

        if (data.success) {
            document.getElementById('inspector-stat-total').textContent = data.stats.total;
            document.getElementById('inspector-stat-enriched').textContent = data.stats.enriched;
            document.getElementById('inspector-stat-pending').textContent = data.stats.pending;
            document.getElementById('inspector-stat-failed').textContent = data.stats.failed;
            document.getElementById('inspector-stat-task-e').textContent = data.stats.task_e;
        }
    } catch (error) {
        console.error('Error loading inspector stats:', error);
    }
}

async function loadInspectorMessages() {
    const status = document.getElementById('inspector-filter-status').value;
    const search = document.getElementById('inspector-search').value;
    const pageSize = parseInt(document.getElementById('inspector-page-size').value);

    const messagesContainer = document.getElementById('inspector-messages');
    messagesContainer.innerHTML = '<div style="padding: 30px; text-align: center; color: #9ca3af;">Loading messages...</div>';

    try {
        const params = new URLSearchParams({
            status: status,
            search: search,
            page: inspectorCurrentPage,
            page_size: pageSize
        });

        const response = await fetch(`${API_BASE}/inspector/messages?${params}`);
        const data = await response.json();

        if (data.success) {
            if (data.messages.length === 0) {
                messagesContainer.innerHTML = '<div style="padding: 30px; text-align: center; color: #9ca3af;">No messages found</div>';
            } else {
                messagesContainer.innerHTML = data.messages.map(msg => `
                    <div onclick="selectInspectorMessage(${msg.id})" style="padding: 10px 15px; display: grid; grid-template-columns: 50px 200px 1fr 120px 100px; gap: 10px; border-bottom: 1px solid #e5e7eb; cursor: pointer; font-size: 0.9em; transition: background 0.15s;" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='white'">
                        <span style="color: #64748b;">${msg.id}</span>
                        <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(msg.sender_email)}">${escapeHtml(msg.sender_email || msg.sender_name || '-')}</span>
                        <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(msg.subject)}">${escapeHtml(msg.subject || '(no subject)')}</span>
                        <span style="color: #64748b;">${msg.date}</span>
                        <span class="status-badge ${msg.status === 'completed' ? 'success' : msg.status === 'failed' ? 'error' : 'pending'}" style="font-size: 0.8em; padding: 2px 8px;">${msg.status}</span>
                    </div>
                `).join('');
            }

            // Update pagination
            const pagination = data.pagination;
            document.getElementById('inspector-page-info').textContent = `Page ${pagination.page} of ${pagination.total_pages}`;
            document.getElementById('inspector-prev').disabled = pagination.page <= 1;
            document.getElementById('inspector-next').disabled = pagination.page >= pagination.total_pages;
        }
    } catch (error) {
        console.error('Error loading inspector messages:', error);
        messagesContainer.innerHTML = '<div style="padding: 30px; text-align: center; color: #ef4444;">Error loading messages</div>';
    }
}

function debounceInspectorSearch() {
    clearTimeout(inspectorSearchTimeout);
    inspectorSearchTimeout = setTimeout(() => {
        inspectorCurrentPage = 1;
        loadInspectorMessages();
    }, 300);
}

function inspectorPrevPage() {
    if (inspectorCurrentPage > 1) {
        inspectorCurrentPage--;
        loadInspectorMessages();
    }
}

function inspectorNextPage() {
    inspectorCurrentPage++;
    loadInspectorMessages();
}

async function selectInspectorMessage(messageId) {
    try {
        const response = await fetch(`${API_BASE}/inspector/message/${messageId}`);
        const data = await response.json();

        if (data.success) {
            displayInspectorDetail(data.message, data.extractions);
        }
    } catch (error) {
        console.error('Error loading message detail:', error);
    }
}

function displayInspectorDetail(message, extractions) {
    const detailContainer = document.getElementById('inspector-detail');
    detailContainer.style.display = 'block';

    // Scroll to detail
    detailContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });

    // Display raw message
    const rawContainer = document.getElementById('inspector-raw-message');
    rawContainer.innerHTML = `
        <div style="margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid #e5e7eb;">
            <p><strong>Subject:</strong> ${escapeHtml(message.subject)}</p>
            <p><strong>From:</strong> ${escapeHtml(message.sender_name)} &lt;${escapeHtml(message.sender_email)}&gt;</p>
            <p><strong>To:</strong> ${escapeHtml(message.recipients)}</p>
            ${message.cc ? `<p><strong>CC:</strong> ${escapeHtml(message.cc)}</p>` : ''}
            <p><strong>Date:</strong> ${message.date}</p>
            <p><strong>Status:</strong> <span class="status-badge ${message.enrichment_status === 'completed' ? 'success' : message.enrichment_status === 'failed' ? 'error' : 'pending'}">${message.enrichment_status}</span></p>
            ${message.is_spurious ? '<p style="color: #dc2626;"><strong>Flagged as spurious</strong></p>' : ''}
        </div>
        <div style="background: #f9fafb; padding: 12px; border-radius: 4px; max-height: 400px; overflow-y: auto;">
            <p style="color: #64748b; font-size: 0.85em; margin-bottom: 8px;">Body (${message.body_length} chars):</p>
            <pre style="white-space: pre-wrap; word-wrap: break-word; font-family: inherit; margin: 0;">${escapeHtml(message.body_snippet)}</pre>
        </div>
    `;

    // Display extractions
    const extractionsContainer = document.getElementById('inspector-extractions');

    const taskOrder = [
        'task_e_summary', 'task_e_sentiment',
        'task_a_projects', 'task_b_stakeholders', 'task_c_importance', 'task_d_meetings'
    ];

    const taskLabels = {
        'task_a_projects': '📁 Projects (Task A)',
        'task_b_stakeholders': '👥 Stakeholders (Task B)',
        'task_c_importance': '⚡ Importance (Task C)',
        'task_d_meetings': '📅 Meetings (Task D)',
        'task_e_summary': '📝 Summary (Task E1)',
        'task_e_sentiment': '💬 Sentiment (Task E2)'
    };

    let extractionHtml = '';

    for (const taskName of taskOrder) {
        const ext = extractions[taskName];
        if (!ext) continue;

        const label = taskLabels[taskName] || taskName;
        const isTaskE = taskName.startsWith('task_e');

        extractionHtml += `
            <div style="margin-bottom: 20px; padding: 12px; background: ${isTaskE ? '#faf5ff' : 'white'}; border: 1px solid ${isTaskE ? '#e9d5ff' : '#e5e7eb'}; border-radius: 6px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                    <strong style="color: ${isTaskE ? '#7c3aed' : '#374151'};">${label}</strong>
                    ${ext.confidence ? `<span style="font-size: 0.85em; color: #64748b;">Confidence: ${ext.confidence}</span>` : ''}
                </div>
                ${ext.error ? `<p style="color: #dc2626;">${ext.error}</p>` : ''}
                ${ext.data ? `<pre style="white-space: pre-wrap; word-wrap: break-word; font-size: 0.85em; background: #f9fafb; padding: 10px; border-radius: 4px; margin: 0; max-height: 200px; overflow-y: auto;">${escapeHtml(JSON.stringify(ext.data, null, 2))}</pre>` : '<p style="color: #9ca3af;">No data</p>'}
                ${ext.processing_time_ms ? `<p style="font-size: 0.8em; color: #9ca3af; margin-top: 8px;">Processing time: ${ext.processing_time_ms}ms</p>` : ''}
            </div>
        `;
    }

    if (!extractionHtml) {
        extractionHtml = '<p style="color: #9ca3af; text-align: center; padding: 20px;">No extractions found for this message</p>';
    }

    extractionsContainer.innerHTML = extractionHtml;
}

// Update pipeline status to show Inspector button when appropriate
function updateInspectorButton() {
    // Show Inspector button after parsing is complete
    const parseStatus = document.getElementById('parse-status');
    const inspectorBtn = document.getElementById('open-inspector-btn');

    if (parseStatus && inspectorBtn) {
        const statusText = parseStatus.textContent.toLowerCase();
        if (statusText === 'completed' || statusText === 'complete') {
            inspectorBtn.style.display = 'inline-block';
        }
    }
}
