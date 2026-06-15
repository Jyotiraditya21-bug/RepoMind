// Auto-detect API host (local vs deployed)
const BACKEND_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : 'https://jimmy2110-repomind-backend.hf.space';

// Device fingerprinting to strictly prevent API key exhaustion (bypassing incognito/cleared cookies)
function getCanvasFingerprint() {
    try {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        canvas.width = 200;
        canvas.height = 40;
        ctx.textBaseline = "top";
        ctx.font = "14px 'Arial'";
        ctx.fillStyle = "#f60";
        ctx.fillRect(125, 1, 62, 20);
        ctx.fillStyle = "#069";
        ctx.fillText("RepoMind,fp.1", 2, 2);
        ctx.fillStyle = "rgba(102, 204, 0, 0.7)";
        ctx.fillText("RepoMind,fp.1", 4, 15);
        return canvas.toDataURL();
    } catch(e) {
        return "";
    }
}

function getDeviceFingerprint() {
    try {
        const components = [
            navigator.userAgent,
            screen.width + "x" + screen.height,
            screen.colorDepth,
            new Date().getTimezoneOffset(),
            navigator.language,
            navigator.hardwareConcurrency || "",
            navigator.deviceMemory || "",
            getCanvasFingerprint()
        ];
        const rawString = components.join('|');
        // Fast djb2 hash function to return a stable hexadecimal string
        let hash = 5381;
        for (let i = 0; i < rawString.length; i++) {
            hash = ((hash << 5) + hash) + rawString.charCodeAt(i);
        }
        return (hash >>> 0).toString(16);
    } catch(e) {
        return "fallback_" + Math.random().toString(36).substr(2, 9);
    }
}

let currentRepoId = null;
let networkInstance = null;
let cachedFileSummaries = {};
let cachedGraphData = null;
let physicsEnabled = true;

// DOM Elements
const repoUrlInput = document.getElementById('repo-url-input');
const analyzeBtn = document.getElementById('analyze-btn');
const recentGroup = document.getElementById('recent-group');
const recentButtonsContainer = document.getElementById('recent-buttons-container');
const errorBanner = document.getElementById('error-banner');
const loadingPanel = document.getElementById('loading-panel');
const loadingText = document.getElementById('loading-text');

const dashboardWrapper = document.getElementById('dashboard-wrapper');
const dashboardGrid = document.getElementById('dashboard-grid');

// Summary Card Elements
const summaryRepoName = document.getElementById('summary-repo-name');
const summaryTotalFiles = document.getElementById('summary-total-files');
const summaryTotalImports = document.getElementById('summary-total-imports');
const summaryDensity = document.getElementById('summary-density');
const langBarContainer = document.getElementById('lang-bar-container');
const langLegendContainer = document.getElementById('lang-legend-container');

// Graph Panel Elements
const badgeFiles = document.getElementById('badge-files');
const badgeLinks = document.getElementById('badge-links');
const graphSearchInput = document.getElementById('graph-search-input');
const graphSearchResults = document.getElementById('graph-search-results');
const legendItems = document.getElementById('legend-items');

// Graph Controls
const graphBtnZoomIn = document.getElementById('graph-btn-zoom-in');
const graphBtnZoomOut = document.getElementById('graph-btn-zoom-out');
const graphBtnFit = document.getElementById('graph-btn-fit');
const graphBtnPhysics = document.getElementById('graph-btn-physics');

// Figma-style Inspector Panel
const inspectorPanel = document.getElementById('inspector-panel');
const inspectorCloseBtn = document.getElementById('inspector-close-btn');
const inspectorTitle = document.getElementById('inspector-title');
const inspectorLangBadge = document.getElementById('inspector-lang-badge');
const inspectorMeta = document.getElementById('inspector-meta');
const inspectorDesc = document.getElementById('inspector-desc');
const inspectorImports = document.getElementById('inspector-imports');
const inspectorImportedBy = document.getElementById('inspector-imported-by');

// Tabs & Chat
const archOverviewText = document.getElementById('architecture-overview-text');
const startHereContainer = document.getElementById('start-here-container');
const chatMessagesContainer = document.getElementById('chat-messages-container');
const chatInputField = document.getElementById('chat-input-field');
const chatSendBtn = document.getElementById('chat-send-btn');

// Initialize App
window.addEventListener('DOMContentLoaded', () => {
    updateUsageCounter();
    renderRecentReposList();
    setupEventListeners();
});

// Event Listeners Setup
function setupEventListeners() {
    analyzeBtn.addEventListener('click', () => {
        const url = repoUrlInput.value.trim();
        if (url) {
            analyzeRepository(url);
        }
    });

    chatSendBtn.addEventListener('click', sendChatMessage);
    chatInputField.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendChatMessage();
        }
    });

    // Close inspector
    inspectorCloseBtn.addEventListener('click', () => {
        inspectorPanel.classList.remove('active');
    });

    // Graph Controls
    graphBtnZoomIn.addEventListener('click', () => {
        if (networkInstance) {
            const currentScale = networkInstance.getScale();
            networkInstance.moveTo({ scale: currentScale * 1.3, animation: true });
        }
    });

    graphBtnZoomOut.addEventListener('click', () => {
        if (networkInstance) {
            const currentScale = networkInstance.getScale();
            networkInstance.moveTo({ scale: currentScale / 1.3, animation: true });
        }
    });

    graphBtnFit.addEventListener('click', () => {
        if (networkInstance) {
            networkInstance.fit({ animation: true });
        }
    });

    graphBtnPhysics.addEventListener('click', () => {
        if (networkInstance) {
            physicsEnabled = !physicsEnabled;
            networkInstance.setOptions({ physics: { enabled: physicsEnabled } });
            if (physicsEnabled) {
                graphBtnPhysics.classList.add('active');
                graphBtnPhysics.title = "Toggle Physics (Freeze)";
            } else {
                graphBtnPhysics.classList.remove('active');
                graphBtnPhysics.title = "Toggle Physics (Resume)";
            }
        }
    });

    // Search event
    graphSearchInput.addEventListener('input', handleGraphSearch);
    
    // Close search dropdown on click outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.graph-search-container')) {
            graphSearchResults.style.display = 'none';
        }
    });
}

// Update the rate limits counter tag
function updateUsageCounter() {
    const count = parseInt(localStorage.getItem('repomind_search_count') || '0', 10);
    const counterEl = document.getElementById('limit-count');
    if (counterEl) {
        counterEl.innerText = `${count} / 5`;
    }
}



// History Manager: Save repo to localStorage
function saveRepoToHistory(name, url) {
    let recentRepos = JSON.parse(localStorage.getItem('repomind_recent_repos') || '[]');
    // Avoid duplicates
    recentRepos = recentRepos.filter(item => item.url.toLowerCase() !== url.toLowerCase());
    // Insert at front
    recentRepos.unshift({ name, url });
    // Limit to 3 items
    if (recentRepos.length > 3) recentRepos.pop();
    
    localStorage.setItem('repomind_recent_repos', JSON.stringify(recentRepos));
    renderRecentReposList();
}

// Render Recent Repos List
function renderRecentReposList() {
    const recentRepos = JSON.parse(localStorage.getItem('repomind_recent_repos') || '[]');
    if (recentRepos.length === 0) {
        recentGroup.style.display = 'none';
        return;
    }
    
    recentGroup.style.display = 'flex';
    recentButtonsContainer.innerHTML = '';
    
    recentRepos.forEach(repo => {
        const btn = document.createElement('button');
        btn.className = 'demo-btn';
        btn.style.borderColor = 'rgba(224, 122, 95, 0.25)';
        btn.innerHTML = `<span>${repo.name}</span>`;
        btn.title = repo.url;
        btn.addEventListener('click', () => {
            repoUrlInput.value = repo.url;
            analyzeRepository(repo.url);
        });
        recentButtonsContainer.appendChild(btn);
    });
}

// Switch tabs: Overview vs Chat
window.switchTab = function(tabName) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    
    if (tabName === 'overview') {
        document.getElementById('tab-btn-overview').classList.add('active');
        document.getElementById('tab-content-overview').classList.add('active');
    } else {
        document.getElementById('tab-btn-chat').classList.add('active');
        document.getElementById('tab-content-chat').classList.add('active');
    }
};

// Show Error message
function showError(message) {
    errorBanner.innerText = message;
    errorBanner.style.display = 'block';
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Clear Error message
function clearError() {
    errorBanner.innerText = '';
    errorBanner.style.display = 'none';
}

// Extract repository name from GitHub URL
function getRepoNameFromUrl(url) {
    try {
        const cleaned = url.replace(/\/$/, ""); // Remove trailing slash
        const parts = cleaned.split('/');
        if (parts.length >= 2) {
            return parts[parts.length - 2] + '/' + parts[parts.length - 1];
        }
    } catch (e) {}
    return "Repository";
}

// Analyze Repository API caller
async function analyzeRepository(url) {
    clearError();
    inspectorPanel.classList.remove('active');
    
    let searchCount = parseInt(localStorage.getItem('repomind_search_count') || '0', 10);
    let analyzedRepos = JSON.parse(localStorage.getItem('repomind_analyzed_repos') || '[]');
    const isAlreadyAnalyzed = analyzedRepos.includes(url.toLowerCase());
    
    if (!isAlreadyAnalyzed && searchCount >= 5) {
        showError("You have reached your limit of 5 new repository analyses. Deploy your own backend to lift this limit!");
        return;
    }
    
    loadingPanel.style.display = 'flex';
    dashboardWrapper.style.display = 'none';
    loadingText.innerText = "Analyzing Repository...";
    
    try {
        const deviceId = getDeviceFingerprint();
        const response = await fetch(`${BACKEND_URL}/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                repo_url: url,
                device_id: deviceId
            })
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to analyze repository. Please check URL and try again.');
        }
        
        currentRepoId = data.repo_id;
        cachedFileSummaries = data.file_summaries || {};
        cachedGraphData = data.graph_data;
        
        if (!isAlreadyAnalyzed) {
            searchCount += 1;
            localStorage.setItem('repomind_search_count', searchCount.toString());
            updateUsageCounter();
            analyzedRepos.push(url.toLowerCase());
            localStorage.setItem('repomind_analyzed_repos', JSON.stringify(analyzedRepos));
        }
        
        // Save to recent history list
        const shortName = getRepoNameFromUrl(url);
        saveRepoToHistory(shortName, url);
        
        // Populate Premium Summary Card Details
        summaryRepoName.innerText = `Repository: ${shortName}`;
        const totalFiles = data.graph_data.nodes.length;
        const totalImports = data.graph_data.edges.length;
        summaryTotalFiles.innerText = totalFiles;
        summaryTotalImports.innerText = totalImports;
        
        // Density calculation: Edges / (Nodes * (Nodes - 1))
        const density = totalFiles > 1 
            ? ((totalImports / (totalFiles * (totalFiles - 1))) * 100).toFixed(1) 
            : 0;
        summaryDensity.innerText = `${density}%`;
        
        // Compute and Draw language stats
        analyzeLanguages(data.graph_data.nodes);
        
        // 1. Render Architecture tab
        archOverviewText.innerHTML = parseMarkdown(data.architecture_overview);
        
        // 2. Render Start Here lists
        startHereContainer.innerHTML = '';
        if (data.start_here && data.start_here.length > 0) {
            data.start_here.forEach((item, index) => {
                const element = document.createElement('div');
                element.className = 'start-here-item scroll-reveal';
                element.style.animationDelay = `${index * 0.1}s`;
                element.innerHTML = `
                    <div class="file-path">${item.file}</div>
                    <div class="file-reason">${item.reason}</div>
                `;
                element.addEventListener('click', () => {
                    focusAndInspectNode(item.file);
                });
                startHereContainer.appendChild(element);
            });
        } else {
            startHereContainer.innerHTML = '<div style="color:var(--text-muted);">No start-here files recommended.</div>';
        }
        
        // Setup scroll-reveal observers
        setupScrollReveal();
        
        // 3. Render Dependency Graph
        renderDependencyGraph(data.graph_data);
        
        // Reset chat history
        chatMessagesContainer.innerHTML = `
            <div class="chat-message assistant">
                I have completed analyzing the repository: <strong>${url}</strong>.<br><br>
                Feel free to ask me questions about its modules, imports, and high-level design.
            </div>
        `;
        
        // Show dashboard
        loadingPanel.style.display = 'none';
        dashboardWrapper.style.display = 'flex';
        
    } catch (err) {
        console.error('Analysis error:', err);
        showError(err.message);
        loadingPanel.style.display = 'none';
    }
}

// Helper to determine language categories by file path
function getLanguageFromPath(path) {
    const ext = path.split('.').pop().toLowerCase();
    switch(ext) {
        case 'py': return 'Python';
        case 'js': case 'jsx': case 'ts': case 'tsx': return 'JavaScript/TypeScript';
        case 'rs': return 'Rust';
        case 'go': return 'Go';
        case 'java': return 'Java';
        case 'cs': return 'C#';
        case 'cpp': case 'cc': case 'h': case 'c': return 'C/C++';
        case 'html': case 'css': return 'HTML/CSS';
        case 'json': case 'yaml': case 'yml': case 'toml': case 'md': return 'Config/Docs';
        default: return 'Other';
    }
}

// Earth-tone coloring system for language categories
function getLanguageColor(lang) {
    const colors = {
        'Python': '#5F7257',                // Sage Green
        'JavaScript/TypeScript': '#E07A5F', // Terracotta Peach
        'Rust': '#D4A373',                  // Warm Sand Ochre
        'Go': '#6096BA',                    // Muted Soft Blue
        'Java': '#9E768F',                  // Lavender Sage
        'C#': '#A78A7F',                    // Soft Clay
        'C/C++': '#E9D8A6',                 // Warm Cream Yellow
        'HTML/CSS': '#7798AB',              // Slate Gray Blue
        'Config/Docs': '#C4A484',           // Light Oak
        'Other': '#9AA59F'                  // Olive Gray
    };
    return colors[lang] || colors['Other'];
}

// Compute language percentages and draw distribution bar
function analyzeLanguages(nodes) {
    const stats = {};
    nodes.forEach(node => {
        const lang = getLanguageFromPath(node.id);
        stats[lang] = (stats[lang] || 0) + 1;
    });
    
    const total = nodes.length;
    const sortedLangs = Object.entries(stats)
        .map(([lang, count]) => ({ lang, count, percentage: ((count / total) * 100).toFixed(1) }))
        .sort((a, b) => b.count - a.count);
        
    // Draw Bar
    langBarContainer.innerHTML = '';
    langLegendContainer.innerHTML = '';
    
    sortedLangs.forEach(item => {
        const color = getLanguageColor(item.lang);
        
        // Create bar segment
        const segment = document.createElement('div');
        segment.className = 'lang-bar-segment';
        segment.style.width = `${item.percentage}%`;
        segment.style.backgroundColor = color;
        segment.title = `${item.lang}: ${item.count} files (${item.percentage}%)`;
        langBarContainer.appendChild(segment);
        
        // Create legend item
        const legend = document.createElement('div');
        legend.className = 'lang-legend-item';
        legend.innerHTML = `
            <span class="lang-legend-dot" style="background-color: ${color}"></span>
            <span>${item.lang} <span style="font-weight:400; opacity:0.8;">(${item.percentage}%)</span></span>
        `;
        langLegendContainer.appendChild(legend);
    });
}

// Dynamic node directory coloring setup
function getNodeDirectoryColors(nodeId) {
    const parts = nodeId.split('/');
    const dir = parts.length > 1 ? parts[0] : 'root';
    
    // Predetermined premium palettes
    const palettes = {
        'backend': { bg: '#E3EAE0', border: '#5F7257' },
        'frontend': { bg: '#F9E5DE', border: '#E07A5F' },
        'root': { bg: '#FAECD8', border: '#E3A03B' }
    };
    
    if (palettes[dir]) {
        return palettes[dir];
    }
    
    // Hash directory name to assign a stable organic tone
    const fallbackTones = [
        { bg: '#E7E6E1', border: '#8A8A83' }, // Stone Grey
        { bg: '#ECE5C8', border: '#9E946C' }, // Dry Grass
        { bg: '#DFEAEB', border: '#789599' }, // Light Sage-Slate
        { bg: '#E4DFEB', border: '#9A8CB2' }  // Pale Lilac Sage
    ];
    
    let hash = 0;
    for (let i = 0; i < dir.length; i++) {
        hash = dir.charCodeAt(i) + ((hash << 5) - hash);
    }
    const idx = Math.abs(hash) % fallbackTones.length;
    return fallbackTones[idx];
}

// Render Graph using Vis.js
function renderDependencyGraph(graphData) {
    const container = document.getElementById('graph-canvas');
    
    // Clear directory legend lists
    legendItems.innerHTML = '';
    const uniqueDirs = new Set();
    
    // Vis Nodes structure
    const nodes = graphData.nodes.map(node => {
        const size = Math.max(15, Math.min(38, 15 + (node.degree || 0) * 2.5));
        const dir = node.id.split('/').length > 1 ? node.id.split('/')[0] : 'root';
        uniqueDirs.add(dir);
        
        const palette = getNodeDirectoryColors(node.id);
        
        return {
            id: node.id,
            label: node.label,
            title: node.id,
            x: node.x,
            y: node.y,
            size: size,
            shape: 'dot',
            color: {
                background: palette.bg,
                border: palette.border,
                highlight: {
                    background: '#FAF9F6',
                    border: '#E07A5F'
                }
            },
            font: {
                color: '#2D332F',
                face: 'Plus Jakarta Sans',
                size: 11,
                weight: '500'
            }
        };
    });
    
    // Populate Directory Legend in UI
    Array.from(uniqueDirs).sort().forEach(dir => {
        const palette = getNodeDirectoryColors(dir + '/dummy');
        const item = document.createElement('div');
        item.className = 'legend-item';
        item.innerHTML = `
            <span class="legend-color-dot" style="background-color: ${palette.bg}; border: 1px solid ${palette.border}"></span>
            <span>${dir}</span>
        `;
        legendItems.appendChild(item);
    });
    
    // Vis Edges structure
    const edges = graphData.edges.map(edge => {
        return {
            from: edge.from,
            to: edge.to,
            arrows: 'to',
            color: {
                color: 'rgba(95, 114, 87, 0.25)',
                highlight: '#E07A5F'
            },
            width: 1.25,
            smooth: {
                type: 'continuous',
                roundness: 0.2
            }
        };
    });
    
    badgeFiles.innerText = `Files: ${nodes.length}`;
    badgeLinks.innerText = `Imports: ${edges.length}`;
    
    const data = {
        nodes: new vis.DataSet(nodes),
        edges: new vis.DataSet(edges)
    };
    
    const options = {
        physics: {
            enabled: true,
            solver: 'forceAtlas2Based',
            forceAtlas2Based: {
                gravitationalConstant: -70,
                centralGravity: 0.008,
                springLength: 90,
                springConstant: 0.06
            },
            stabilization: {
                iterations: 120,
                updateInterval: 30
            }
        },
        interaction: {
            hover: true,
            dragNodes: true,
            zoomView: true,
            dragView: true
        }
    };
    
    if (networkInstance) {
        networkInstance.destroy();
    }
    
    networkInstance = new vis.Network(container, data, options);
    
    // Double click to reset focus
    networkInstance.on("doubleClick", () => {
        networkInstance.fit({ animation: true });
        inspectorPanel.classList.remove('active');
    });
    
    // Click Node Listener
    networkInstance.on("click", (params) => {
        if (params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            showNodeInspector(nodeId);
        } else {
            inspectorPanel.classList.remove('active');
        }
    });
}

// Compute Degree & populate the Figma Inspector Panel
function showNodeInspector(nodeId) {
    if (!cachedGraphData) return;
    
    const summary = cachedFileSummaries[nodeId] || "No detailed summary generated.";
    
    // Calculate in-degree / out-degree
    let inDegree = 0;
    let outDegree = 0;
    const imports = [];
    const importedBy = [];
    
    cachedGraphData.edges.forEach(edge => {
        if (edge.from === nodeId) {
            outDegree++;
            imports.push(edge.to);
        }
        if (edge.to === nodeId) {
            inDegree++;
            importedBy.push(edge.from);
        }
    });
    
    // Populate inspector
    inspectorTitle.innerText = nodeId;
    inspectorTitle.title = nodeId;
    
    const lang = getLanguageFromPath(nodeId);
    inspectorLangBadge.innerText = lang;
    inspectorLangBadge.style.backgroundColor = getLanguageColor(lang) + '20';
    inspectorLangBadge.style.color = getLanguageColor(lang);
    
    inspectorMeta.innerText = `Degree: ${inDegree + outDegree} | In-Degree: ${inDegree} | Out-Degree: ${outDegree}`;
    inspectorDesc.innerText = summary;
    
    // Render clickable tags
    inspectorImports.innerHTML = '';
    if (imports.length > 0) {
        imports.forEach(imp => {
            const tag = document.createElement('span');
            tag.innerText = imp.split('/').pop();
            tag.title = imp;
            tag.addEventListener('click', () => focusAndInspectNode(imp));
            inspectorImports.appendChild(tag);
        });
    } else {
        inspectorImports.innerHTML = '<span class="empty">None</span>';
    }
    
    inspectorImportedBy.innerHTML = '';
    if (importedBy.length > 0) {
        importedBy.forEach(impBy => {
            const tag = document.createElement('span');
            tag.innerText = impBy.split('/').pop();
            tag.title = impBy;
            tag.addEventListener('click', () => focusAndInspectNode(impBy));
            inspectorImportedBy.appendChild(tag);
        });
    } else {
        inspectorImportedBy.innerHTML = '<span class="empty">None</span>';
    }
    
    inspectorPanel.classList.add('active');
}

// Focus camera on node and slide inspector open
function focusAndInspectNode(nodeId) {
    if (!networkInstance) return;
    
    // Make sure node exists
    try {
        networkInstance.selectNodes([nodeId]);
        networkInstance.focus(nodeId, {
            scale: 1.05,
            animation: {
                duration: 600,
                easingFunction: 'easeInOutQuad'
            }
        });
        showNodeInspector(nodeId);
        
        // If physics is running, freeze to keep view stable
        if (physicsEnabled) {
            // Wait brief moment for transition to finish then freeze
            setTimeout(() => {
                if (networkInstance && physicsEnabled) {
                    physicsEnabled = false;
                    networkInstance.setOptions({ physics: { enabled: false } });
                    graphBtnPhysics.classList.remove('active');
                }
            }, 650);
        }
    } catch(e) {
        console.warn("Could not focus node:", nodeId);
    }
}

// Fuzzy Graph Autocomplete Search
function handleGraphSearch(e) {
    const query = e.target.value.toLowerCase().trim();
    if (!query || !cachedGraphData) {
        graphSearchResults.style.display = 'none';
        return;
    }
    
    const matches = cachedGraphData.nodes.filter(node => 
        node.id.toLowerCase().includes(query)
    ).slice(0, 5);
    
    if (matches.length === 0) {
        graphSearchResults.innerHTML = '<div class="search-result-item" style="color:var(--text-muted); cursor:default;">No files found</div>';
    } else {
        graphSearchResults.innerHTML = '';
        matches.forEach(node => {
            const item = document.createElement('div');
            item.className = 'search-result-item';
            item.innerText = node.id;
            item.addEventListener('click', () => {
                graphSearchInput.value = node.id.split('/').pop();
                focusAndInspectNode(node.id);
                graphSearchResults.style.display = 'none';
            });
            graphSearchResults.appendChild(item);
        });
    }
    graphSearchResults.style.display = 'block';
}

// Scroll reveal animations implementation
function setupScrollReveal() {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('revealed');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.15 });
    
    document.querySelectorAll('.scroll-reveal').forEach(el => {
        observer.observe(el);
    });
}

// Chat API caller
async function sendChatMessage() {
    const question = chatInputField.value.trim();
    if (!question || !currentRepoId) return;
    
    appendMessage(question, 'user');
    chatInputField.value = '';
    
    const loadingId = appendMessage('Thinking...', 'assistant loading');
    
    try {
        const response = await fetch(`${BACKEND_URL}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                repo_id: currentRepoId,
                question: question
            })
        });
        
        const data = await response.json();
        
        const loadingBubble = document.getElementById(loadingId);
        if (loadingBubble) loadingBubble.remove();
        
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to fetch answer.');
        }
        
        appendMessage(data.answer, 'assistant');
        
    } catch (err) {
        console.error('Chat error:', err);
        const loadingBubble = document.getElementById(loadingId);
        if (loadingBubble) loadingBubble.remove();
        appendMessage(`Error: ${err.message}`, 'assistant');
    }
}

// Regex-based robust Markdown-to-HTML parser
function parseMarkdown(text) {
    let html = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // Fenced Code Blocks with Copy button: ```lang\ncode\n```
    html = html.replace(/```(\w*)\n([\s\S]*?)\n```/g, (match, lang, code) => {
        return `<pre><code class="code-block">${code.trim()}</code><button class="copy-code-btn" onclick="copyCodeToClipboard(this)">Copy</button></pre>`;
    });

    // Inline Code: `code`
    html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

    // Bold: **text**
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

    // Bullet Lists: Convert "- Item"
    const lines = html.split('\n');
    let inList = false;
    let resultLines = [];

    for (let line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('- ')) {
            if (!inList) {
                resultLines.push('<ul>');
                inList = true;
            }
            resultLines.push(`<li>${trimmed.substring(2)}</li>`);
        } else {
            if (inList) {
                resultLines.push('</ul>');
                inList = false;
            }
            resultLines.push(line);
        }
    }
    if (inList) {
        resultLines.push('</ul>');
    }

    return resultLines.join('\n')
        .replace(/\n/g, '<br>')
        .replace(/<\/ul><br>/g, '</ul>')
        .replace(/<br><ul>/g, '<ul>')
        .replace(/<\/li><br>/g, '</li>');
}

// Global Copy Code utility
window.copyCodeToClipboard = function(btn) {
    const code = btn.previousElementSibling.innerText;
    navigator.clipboard.writeText(code).then(() => {
        const originalText = btn.innerText;
        btn.innerText = 'Copied!';
        setTimeout(() => {
            btn.innerText = originalText;
        }, 1500);
    }).catch(err => {
        console.error('Failed to copy: ', err);
    });
};

// Helper to append message bubble to chat window
function appendMessage(text, senderClass) {
    const messageId = 'msg-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
    const bubble = document.createElement('div');
    bubble.id = messageId;
    bubble.className = `chat-message ${senderClass}`;
    
    if (senderClass.includes('loading')) {
        bubble.innerHTML = '<span></span><span></span><span></span>';
    } else {
        bubble.innerHTML = parseMarkdown(text);
    }
    
    chatMessagesContainer.appendChild(bubble);
    chatMessagesContainer.scrollTop = chatMessagesContainer.scrollHeight;
    
    return messageId;
}
