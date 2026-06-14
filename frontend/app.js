// Auto-detect API host (local vs deployed)
const BACKEND_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : 'https://jimmy2110-repomind-backend.hf.space'; // Replace with your actual HF Space URL when deployed

let currentRepoId = null;
let networkInstance = null;
let cachedFileSummaries = {};
let demoUrls = [];

// DOM Elements
const repoUrlInput = document.getElementById('repo-url-input');
const analyzeBtn = document.getElementById('analyze-btn');
const demoButtonsContainer = document.getElementById('demo-buttons-container');
const errorBanner = document.getElementById('error-banner');
const loadingPanel = document.getElementById('loading-panel');
const loadingText = document.getElementById('loading-text');
const dashboardGrid = document.getElementById('dashboard-grid');
const selectedNodeInfo = document.getElementById('selected-node-info');

const archOverviewText = document.getElementById('architecture-overview-text');
const startHereContainer = document.getElementById('start-here-container');

const chatMessagesContainer = document.getElementById('chat-messages-container');
const chatInputField = document.getElementById('chat-input-field');
const chatSendBtn = document.getElementById('chat-send-btn');

// Initialize App
window.addEventListener('DOMContentLoaded', () => {
    fetchDemos();
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
}

// Fetch Pre-baked Demos from backend
async function fetchDemos() {
    try {
        const response = await fetch(`${BACKEND_URL}/demos`);
        if (!response.ok) throw new Error('Failed to fetch demo list');
        const demos = await response.json();
        
        demoUrls = demos.map(d => d.url.toLowerCase());
        
        demoButtonsContainer.innerHTML = '';
        demos.forEach(demo => {
            const btn = document.createElement('button');
            btn.className = 'demo-btn';
            btn.innerText = demo.name;
            btn.title = demo.description;
            btn.addEventListener('click', () => {
                repoUrlInput.value = demo.url;
                analyzeRepository(demo.url);
            });
            demoButtonsContainer.appendChild(btn);
        });
    } catch (err) {
        console.error('Error fetching demos:', err);
        // Fallback static demo button if server is not running during load
        demoButtonsContainer.innerHTML = '<span style="font-size:0.8rem; color:var(--text-muted);">Launch backend to load demos</span>';
    }
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

// Analyze Repository API caller
async function analyzeRepository(url) {
    clearError();
    
    const isDemo = demoUrls.includes(url.toLowerCase());
    let searchCount = parseInt(localStorage.getItem('repomind_search_count') || '0', 10);
    let analyzedRepos = JSON.parse(localStorage.getItem('repomind_analyzed_repos') || '[]');
    const isAlreadyAnalyzed = analyzedRepos.includes(url.toLowerCase());
    
    if (!isDemo && !isAlreadyAnalyzed && searchCount >= 5) {
        showError("You have reached your limit of 5 new repository analyses. Deploy your own backend to lift this limit!");
        return;
    }
    
    loadingPanel.style.display = 'flex';
    dashboardGrid.style.display = 'none';
    loadingText.innerText = "Analyzing Repository...";
    
    try {
        const response = await fetch(`${BACKEND_URL}/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo_url: url })
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to analyze repository. Please check URL and try again.');
        }
        
        currentRepoId = data.repo_id;
        cachedFileSummaries = data.file_summaries || {};
        
        if (!isDemo && !isAlreadyAnalyzed) {
            localStorage.setItem('repomind_search_count', (searchCount + 1).toString());
            analyzedRepos.push(url.toLowerCase());
            localStorage.setItem('repomind_analyzed_repos', JSON.stringify(analyzedRepos));
        }
        
        // 1. Render Architecture tab
        archOverviewText.innerText = data.architecture_overview;
        
        // 2. Render Start Here lists
        startHereContainer.innerHTML = '';
        if (data.start_here && data.start_here.length > 0) {
            data.start_here.forEach(item => {
                const element = document.createElement('div');
                element.className = 'start-here-item';
                element.innerHTML = `
                    <div class="file-path">${item.file}</div>
                    <div class="file-reason">${item.reason}</div>
                `;
                startHereContainer.appendChild(element);
            });
        } else {
            startHereContainer.innerHTML = '<div style="color:var(--text-muted);">No start-here files recommended.</div>';
        }
        
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
        dashboardGrid.style.display = 'grid';
        selectedNodeInfo.innerHTML = "Scroll to zoom | Drag to pan | Click a node to view summary";
        
    } catch (err) {
        console.error('Analysis error:', err);
        showError(err.message);
        loadingPanel.style.display = 'none';
    }
}

// Render Graph using Vis.js
function renderDependencyGraph(graphData) {
    const container = document.getElementById('graph-canvas');
    
    // Vis Nodes structure
    const nodes = graphData.nodes.map(node => {
        // Compute size based on degree
        const size = Math.max(15, Math.min(40, 15 + (node.degree || 0) * 3));
        return {
            id: node.id,
            label: node.label,
            title: node.path,
            x: node.x,
            y: node.y,
            size: size,
            shape: 'dot',
            color: {
                background: '#1F2937',
                border: '#6366F1',
                highlight: {
                    background: '#6366F1',
                    border: '#8B5CF6'
                }
            },
            font: {
                color: '#F3F4F6',
                face: 'Plus Jakarta Sans',
                size: 13
            }
        };
    });
    
    // Vis Edges structure
    const edges = graphData.edges.map(edge => {
        return {
            from: edge.from,
            to: edge.to,
            arrows: 'to',
            color: {
                color: 'rgba(99, 102, 241, 0.35)',
                highlight: '#8B5CF6'
            },
            width: 1.5
        };
    });
    
    const data = {
        nodes: new vis.DataSet(nodes),
        edges: new vis.DataSet(edges)
    };
    
    const options = {
        physics: {
            enabled: true,
            solver: 'forceAtlas2Based',
            forceAtlas2Based: {
                gravitationalConstant: -50,
                centralGravity: 0.01,
                springLength: 100,
                springConstant: 0.08
            },
            stabilization: {
                iterations: 150,
                updateInterval: 25
            }
        },
        interaction: {
            hover: true,
            dragNodes: true
        }
    };
    
    if (networkInstance) {
        networkInstance.destroy();
    }
    
    networkInstance = new vis.Network(container, data, options);
    
    // Add Click Listeners for Interactive Node Summary
    networkInstance.on("click", (params) => {
        if (params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            const summary = cachedFileSummaries[nodeId] || "No detailed summary available.";
            selectedNodeInfo.innerHTML = `<strong>${nodeId}</strong>: ${summary}`;
        } else {
            selectedNodeInfo.innerHTML = "Scroll to zoom | Drag to pan | Click a node to view summary";
        }
    });
}

// Chat API caller
async function sendChatMessage() {
    const question = chatInputField.value.trim();
    if (!question || !currentRepoId) return;
    
    // Add user message
    appendMessage(question, 'user');
    chatInputField.value = '';
    
    // Add temporary loading indicator bubble
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
        
        // Remove loading bubble
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

// Helper to append message bubble to chat window
function appendMessage(text, senderClass) {
    const messageId = 'msg-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
    const bubble = document.createElement('div');
    bubble.id = messageId;
    bubble.className = `chat-message ${senderClass}`;
    bubble.innerHTML = text.replace(/\n/g, '<br>');
    
    chatMessagesContainer.appendChild(bubble);
    chatMessagesContainer.scrollTop = chatMessagesContainer.scrollHeight;
    
    return messageId;
}
